"""Fetch the sheet's export, check it, and turn it into the numbers and post lists the site shows."""

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config

CENTRAL = ZoneInfo("America/Chicago")


class PublishError(Exception):
    """A check failed. The build stops and the live site keeps its last version."""

    def __init__(self, check, value):
        super().__init__(f"{check}: {value}")
        self.check, self.value = check, value

    def line(self):
        return f"FAILED {self.check}: {self.value}. See publish/RUNBOOK.md#{self.check}"


def fetch():
    from sheet import sheet_call
    return sheet_call("export")


def load_previous(path):
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def split(s):
    return [x.strip() for x in str(s or "").split(";") if x.strip()]


def status_of(s):
    s = str(s or "").strip()
    if not s or s.startswith("Live"):
        return "live"
    if s.startswith("Deleted"):
        return "deleted"
    return None


def ct_to_utc(date_ct):
    return datetime.strptime(date_ct, "%Y-%m-%d %H:%M").replace(tzinfo=CENTRAL).astimezone(timezone.utc)


# ---------- Checks ----------

def validate(data, previous, now=None):
    rows = data.get("rows") or []
    if not rows:
        raise PublishError("no-rows", "the export returned no posts")
    seen = set()
    for r in rows:
        for k in config.REQUIRED:
            if not str(r.get(k) or "").strip():
                raise PublishError("required-field", f"post {r.get('post_id') or '?'} has no {k}")
        pid = r["post_id"]
        if pid in seen:
            raise PublishError("duplicate-id", pid)
        seen.add(pid)
        if r["type"] not in config.TYPES:
            raise PublishError("unknown-type", f"{r['type']!r} on post {pid}")
        topics = split(r.get("category"))
        if len(topics) > 2 or any(t not in config.KNOWN_TOPICS for t in topics):
            raise PublishError("unknown-topic", f"{r.get('category')!r} on post {pid}")
        if status_of(r.get("status")) is None:
            raise PublishError("unknown-status", f"{r.get('status')!r} on post {pid}")
        try:
            ct_to_utc(r["date_ct"])
        except ValueError:
            raise PublishError("bad-date", f"{r['date_ct']!r} on post {pid}")
    if previous and previous.get("total_posts"):
        floor = previous["total_posts"] * (1 - config.MAX_DROP)
        if len(rows) < floor:
            raise PublishError("post-count-drop", f"{len(rows)} posts, down from {previous['total_posts']}")
    newest = max(ct_to_utc(r["date_ct"]) for r in rows)
    now = now or datetime.now(timezone.utc)
    if now - newest > timedelta(hours=config.MAX_STALE_HOURS):
        raise PublishError("stale-data", f"newest post is from {newest:%Y-%m-%d %H:%M} UTC")
    sample = data.get("sample")
    if sample and sample.get("ids"):
        missing = [x["post_id"] for x in sample["ids"] if x["post_id"] not in seen]
        if missing:
            raise PublishError("sample-missing", f"{len(missing)} sample posts are not in the sheet, e.g. {missing[0]}")


# ---------- The sample ----------

def is_decided(finding, reviewed_at, drawn_at):
    """Same rule as isDecided_ in apps_script/Code.gs: a decision counts only if made at or after the draw."""
    at = str(reviewed_at or "").strip()
    return bool(str(finding or "").strip()) and at != "" and at >= drawn_at


def canonical_sample_text(ids, seed):
    return "\n".join(ids) + f"\nseed:{seed}\n"


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


# ---------- Building the site's data ----------

def slug(topic):
    return topic.lower().replace("&", "and").replace("/", "-").replace(" ", "-").replace("--", "-")


def archive_or_none(v):
    v = str(v or "").strip()
    return v if v.startswith("https://web.archive.org/") else None


def public_post(r):
    return {
        "post_id": r["post_id"],
        "date_ct": r["date_ct"],
        "type": r["type"],
        "text": r.get("text") or "",
        "context": r.get("context") or "",
        "link": r["link"],
        "their_link": r.get("their_link") or None,
        "archive_link": archive_or_none(r.get("archive_link")),
        "topics": split(r.get("category")),
        "status": status_of(r.get("status")),
    }


@dataclass
class Site:
    meta: dict
    monthly: list
    topics: dict
    sample: dict
    selected: list
    reviewed_ids: set = field(default_factory=set)


def months_through(last):
    out, y, m = [], 2026, 1
    while f"{y}-{m:02d}" <= last:
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def compute(data, sample_size=None, now=None):
    sample_size = sample_size or config.SAMPLE_SIZE
    rows = data["rows"]
    posts = {r["post_id"]: public_post(r) for r in rows}
    political = {pid for pid, p in posts.items() if set(p["topics"]) & set(config.POLITICAL_TOPICS)}
    data_through = max(r["date_ct"] for r in rows)
    months = months_through(data_through[:7])

    monthly = {m: {"month": m, "wrote_political": 0, "retweeted_political": 0, "other": 0} for m in months}
    for pid, p in posts.items():
        m = monthly.get(p["date_ct"][:7])
        if m is None:
            continue
        if pid not in political:
            m["other"] += 1
        elif p["type"] in config.WROTE:
            m["wrote_political"] += 1
        else:
            m["retweeted_political"] += 1

    review = {x["post_id"]: x for x in data.get("reviewed") or []}

    # The sample, only once every post in it is decided after the draw.
    s = data.get("sample")
    sample = {"drawn": bool(s), "complete": False, "size": sample_size,
              "decided_count": (s or {}).get("decided_count", 0)}
    sample_ids = set()
    if s:
        sample.update({k: s.get(k) for k in ("drawn_at", "frame_size", "frame_sha256", "sample_sha256")})
    if s and s.get("ids"):
        if len(s["ids"]) != sample_size:
            raise PublishError("sample-size", f"{len(s['ids'])} sample posts, expected {sample_size}")
        ids = [x["post_id"] for x in sorted(s["ids"], key=lambda x: int(x["sample_no"]))]
        text = canonical_sample_text(ids, s["seed"])
        if sha256_hex(text) != s["sample_sha256"]:
            raise PublishError("sample-hash", "the sample doesn't match its locked fingerprint")
        entries, upheld, strict = [], 0, 0
        for x in sorted(s["ids"], key=lambda x: int(x["sample_no"])):
            rv = review.get(x["post_id"])
            if not rv or not is_decided(rv.get("finding"), rv.get("reviewed_at"), s["drawn_at"]):
                raise PublishError("sample-undecided", f"sample post {x['post_id']} has no decision after the draw")
            ok = rv["finding"] == "Approved"
            match = ok and bool({h.lower() for h in split(rv.get("harm_type"))}
                                & {f.lower() for f in split(x.get("flag_at_draw"))})
            upheld += ok
            strict += match
            e = dict(posts[x["post_id"]], group="sample", sample_no=int(x["sample_no"]),
                     flag=x.get("flag_at_draw") or "", finding=rv["finding"], harm_type=rv.get("harm_type") or "",
                     conduct=rv.get("conduct") or "", rationale=rv.get("rationale") or "",
                     reviewed_at=rv.get("reviewed_at") or "", prior_finding=x.get("prior_finding") or "")
            entries.append(e)
            sample_ids.add(x["post_id"])
        n = len(entries)
        sample.update({
            "complete": True, "seed": s["seed"], "text": text, "posts": entries,
            "headline": {"k": upheld, "n": n, "interval": wilson(upheld, n)},
            "strict": {"k": strict, "n": n, "interval": wilson(strict, n)},
            "rereviewed": [e for e in entries if e["prior_finding"]],
        })

    selected = []
    for pid, rv in review.items():
        if pid in sample_ids or rv.get("finding") != "Approved" or pid not in posts:
            continue
        selected.append(dict(posts[pid], group="selected", flag=rv.get("flag") or "", finding="Approved",
                             harm_type=rv.get("harm_type") or "", conduct=rv.get("conduct") or "",
                             rationale=rv.get("rationale") or "", reviewed_at=rv.get("reviewed_at") or ""))
    selected.sort(key=lambda p: p["date_ct"], reverse=True)
    reviewed_posts = {p["post_id"]: p for p in selected}
    reviewed_posts.update({p["post_id"]: p for p in sample.get("posts", [])})

    topics = {}
    for t in config.POLITICAL_TOPICS:
        ids = [pid for pid in political if t in posts[pid]["topics"]]
        tm = {m: {"month": m, "wrote": 0, "retweeted": 0} for m in months}
        for pid in ids:
            m = tm.get(posts[pid]["date_ct"][:7])
            if m:
                m["wrote" if posts[pid]["type"] in config.WROTE else "retweeted"] += 1
        ordered = sorted(ids, key=lambda pid: posts[pid]["date_ct"], reverse=True)
        topics[slug(t)] = {
            "name": t, "slug": slug(t), "monthly": list(tm.values()),
            "reviewed": [reviewed_posts[pid] for pid in ordered if pid in reviewed_posts],
            "posts": [posts[pid] for pid in ordered],
        }

    built = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%MZ")
    meta = {
        "snapshot_id": f"{data_through}/{built}",
        "data_through_ct": data_through,
        "built_at_utc": built,
        "total_posts": len(posts),
        "political_posts": len(political),
        "missing_days": list(config.MISSING_DAYS),
        "sample": {k: sample.get(k) for k in config.FILE_KEYS["meta.sample"]} if s else None,
    }
    return Site(meta=meta, monthly=list(monthly.values()), topics=topics, sample=sample, selected=selected,
                reviewed_ids=set(reviewed_posts))
