"""Collect new @elonmusk posts and log them to the Musk Post Tracker sheet.

One run:
  1. Ask the Apps Script endpoint for the newest Post ID already logged.
  2. Fetch newer posts from GetXAPI (default, $0.001 per ~20-post page) or the official X API (SOURCE=x).
  3. Categorize and describe the whole batch in one Claude call.
  4. Submit each post to the Wayback Machine (best effort).
  5. Screenshot each post from X's official embed (falls back to a labeled card rendered from API data).
  6. Send rows + screenshots to the Apps Script endpoint, which appends to the sheet and saves images to Drive.

Env: GETXAPI_KEY (or X_BEARER_TOKEN with SOURCE=x), ANTHROPIC_API_KEY, SHEET_ENDPOINT, SHEET_SECRET,
     optional WAYBACK_ACCESS_KEY / WAYBACK_SECRET_KEY, BACKFILL_PAGES, BACKFILL_SINCE / BACKFILL_UNTIL, REFRESH, MAX_POSTS.
"""

import base64
import html
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic
import requests

from categories import CATEGORIES, FLAGS

MUSK_USER_ID = "44196397"
CENTRAL = ZoneInfo("America/Chicago")
X_API = "https://api.x.com/2"
MODEL = "claude-opus-5-5"


# ---------- Sheet endpoint (Apps Script web app) ----------

def sheet_call(action, payload=None):
    body = {"secret": os.environ["SHEET_SECRET"], "action": action, **(payload or {})}
    r = requests.post(os.environ["SHEET_ENDPOINT"], json=body, timeout=120)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Sheet endpoint error on {action}: {data}")
    return data


# ---------- X API ----------

def fetch_posts(since_id, max_pages):
    headers = {"Authorization": f"Bearer {os.environ['X_BEARER_TOKEN']}"}
    params = {
        "max_results": 100,
        "tweet.fields": "created_at,referenced_tweets,note_tweet,attachments,author_id,conversation_id,in_reply_to_user_id",
        "expansions": "referenced_tweets.id,referenced_tweets.id.author_id,attachments.media_keys,in_reply_to_user_id",
        "user.fields": "username,name",
        "media.fields": "type,url,preview_image_url",
    }
    if since_id:
        params["since_id"] = since_id
    posts, includes = [], {"tweets": [], "users": [], "media": []}
    for _ in range(max_pages):
        r = requests.get(f"{X_API}/users/{MUSK_USER_ID}/tweets", headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            reset = int(r.headers.get("x-rate-limit-reset", time.time() + 60))
            wait = max(5, reset - int(time.time()))
            print(f"X rate limit; waiting {wait}s")
            time.sleep(min(wait, 900))
            continue
        r.raise_for_status()
        data = r.json()
        posts += data.get("data", [])
        for k in includes:
            includes[k] += data.get("includes", {}).get(k, [])
        token = data.get("meta", {}).get("next_token")
        if not token:
            break
        params["pagination_token"] = token
    return posts, includes


# ---------- GetXAPI (third-party, scraping-backed) ----------
# Converts GetXAPI's responses into the same X API v2 shape so nothing downstream changes.

GETXAPI = "https://api.getxapi.com"


def _id_gt(a, b):
    return len(a) > len(b) or (len(a) == len(b) and a > b)


_logged_shapes = {}


def _getx_to_v2(t, posts, includes):
    """Add one GetXAPI tweet (and any tweet it embeds) to v2-shaped lists. Returns its id."""
    author = t.get("author") or t.get("user") or {}
    if author and not _logged_shapes.get("user"):
        _logged_shapes["user"] = True
        print(f"GetXAPI embedded user keys: {sorted(author)}")
    # Embedded quoted posts use a different user object than top-level posts; accept either shape.
    uid = author.get("id") or author.get("id_str") or author.get("rest_id")
    uname = (author.get("userName") or author.get("screen_name") or author.get("username")
             or author.get("user_name") or author.get("handle"))
    if not uid and uname:
        uid = "name:" + uname
    if uid:
        includes["users"].append({"id": str(uid), "username": uname or "i", "name": author.get("name", "")})
    v2 = {"id": str(t.get("id") or t.get("id_str")), "text": t.get("text") or t.get("full_text") or "",
          "author_id": str(uid) if uid else None}
    # Embedded (quoted/retweeted) objects don't always carry createdAt; only top-level posts need it.
    raw_date = t.get("createdAt") or t.get("created_at")
    if raw_date:
        created = datetime.strptime(raw_date, "%a %b %d %H:%M:%S %z %Y")
        v2["created_at"] = created.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    elif posts is not None:
        raise ValueError(f"GetXAPI post {v2['id']} has no createdAt; keys: {sorted(t)}")
    refs = []
    rt = t.get("retweeted_tweet")
    if rt:
        refs.append({"type": "retweeted", "id": _getx_to_v2(rt, None, includes)})
    elif t.get("text", "").startswith("RT @"):
        v2["_rt_text_only"] = True  # retweet without the original attached; keep the RT text as-is
    qt = t.get("quoted_tweet")
    if qt and not _logged_shapes.get("quoted"):
        _logged_shapes["quoted"] = True
        print(f"GetXAPI quoted_tweet keys: {sorted(qt)}")  # documents the undocumented shape in the run log
    if rt and not _logged_shapes.get("retweeted"):
        _logged_shapes["retweeted"] = True
        print(f"GetXAPI retweeted_tweet keys: {sorted(rt)}")
    if qt:
        refs.append({"type": "quoted", "id": _getx_to_v2(qt, None, includes)})
    if t.get("isReply") and t.get("inReplyToId"):
        refs.append({"type": "replied_to", "id": str(t["inReplyToId"])})
        if t.get("inReplyToUsername"):
            v2["_reply_to_username"] = t["inReplyToUsername"]
    if refs:
        v2["referenced_tweets"] = refs
    (posts if posts is not None else includes["tweets"]).append(v2)
    return v2["id"]


def fetch_posts_getxapi(since_id, max_pages):
    """Pages newest-first through tweets_and_replies until it reaches posts already logged."""
    headers = {"Authorization": f"Bearer {os.environ['GETXAPI_KEY']}"}
    params = {"userName": "elonmusk"}
    posts, includes = [], {"tweets": [], "users": [], "media": []}
    for _ in range(max_pages):
        r = requests.get(f"{GETXAPI}/twitter/user/tweets_and_replies", headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            print("GetXAPI rate limit; waiting 30s")
            time.sleep(30)
            continue
        r.raise_for_status()
        data = r.json()
        page = data.get("tweets", [])
        new_on_page = 0
        for t in page:
            # tweets_and_replies can include other people's posts in a thread; keep only his
            if (t.get("author") or {}).get("userName", "elonmusk").lower() != "elonmusk":
                continue
            if since_id and not _id_gt(str(t["id"]), since_id):
                continue  # already logged (or the pinned post)
            _getx_to_v2(t, posts, includes)
            new_on_page += 1
        if since_id and page and new_on_page == 0:
            break  # whole page is older than what we have
        if not data.get("has_more") or not data.get("next_cursor"):
            break
        params["cursor"] = data["next_cursor"]
    return posts, includes


def getx_detail(post_id):
    """One post from GetXAPI ($0.001 per call), or None if it's deleted or unavailable."""
    try:
        r = requests.get(f"{GETXAPI}/twitter/tweet/detail", params={"id": post_id},
                         headers={"Authorization": f"Bearer {os.environ['GETXAPI_KEY']}"}, timeout=60)
        if r.status_code != 200:
            return None
        return r.json().get("data")
    except Exception as e:
        print(f"GetXAPI detail failed for {post_id}: {e}")
        return None


def fetch_parents(posts, includes):
    """GetXAPI timelines don't include the post being replied to, so look each one up."""
    have = {t["id"] for t in includes["tweets"]}
    for p in posts:
        for ref in p.get("referenced_tweets", []):
            if ref["type"] == "replied_to" and ref["id"] not in have:
                data = getx_detail(ref["id"])
                if data:
                    _getx_to_v2(data, None, includes)
                have.add(ref["id"])


def using_getxapi():
    return os.environ.get("SOURCE", "getxapi") == "getxapi"


def get_posts(since_id, max_pages):
    if using_getxapi():
        posts, includes = fetch_posts_getxapi(since_id, max_pages * 5)  # GetXAPI pages hold ~20 posts, not 100
        fetch_parents(posts, includes)
        return posts, includes
    return fetch_posts(since_id, max_pages)


def full_text(t):
    return (t.get("note_tweet") or {}).get("text") or t.get("text", "")


def normalize(post, includes):
    """Turn an X API post into the fields the sheet needs."""
    tweets = {t["id"]: t for t in includes["tweets"]}
    users = {u["id"]: u for u in includes["users"]}
    refs = {r["type"]: r["id"] for r in post.get("referenced_tweets", [])}

    def ref_info(ref_id):
        t = tweets.get(ref_id)
        if not t:
            return None
        u = users.get(t.get("author_id"), {})
        username = u.get("username", "i")
        return {"id": ref_id, "username": username, "text": full_text(t),
                "link": f"https://x.com/{username}/status/{ref_id}"}

    created = datetime.fromisoformat(post["created_at"].replace("Z", "+00:00"))
    row = {
        "post_id": post["id"],
        "created_utc": post["created_at"],
        "date_ct": created.astimezone(CENTRAL).strftime("%Y-%m-%d %H:%M"),
        "link": f"https://x.com/elonmusk/status/{post['id']}",
        "text": full_text(post),
        "context": None,  # who and what he replied to, quoted or retweeted
        "context_link": None,
        "screenshot_id": post["id"],
    }
    if "retweeted" in refs:
        row["type"] = "Retweet"
        orig = ref_info(refs["retweeted"])
        if orig:
            row["text"] = f"RT @{orig['username']}: {orig['text']}"
            row["context"] = f"Retweet of @{orig['username']}"
            row["context_link"] = orig["link"]
            row["screenshot_id"] = orig["id"]  # retweets have no embed of their own
            row["original_link"] = orig["link"]
    elif "quoted" in refs:
        row["type"] = "Quote"
        q = ref_info(refs["quoted"])
        if q:
            row["context"] = f"Quoting @{q['username']}: {q['text']}"
            row["context_link"] = q["link"]
        if "replied_to" in refs:
            p = ref_info(refs["replied_to"])
            row["context"] = (row["context"] or "") + (
                f"\n\nAlso replying to @{p['username']}: {p['text']}" if p else "\n\n(Also a reply)")
    elif "replied_to" in refs:
        row["type"] = "Reply"
        p = ref_info(refs["replied_to"])
        if p:
            row["context"] = f"Replying to @{p['username']}: {p['text']}"
            row["context_link"] = p["link"]
        else:
            who = f"@{post['_reply_to_username']}" if post.get("_reply_to_username") else "a post"
            row["context"] = f"Replying to {who} (that post is deleted or unavailable)"
            row["context_link"] = f"https://x.com/i/status/{refs['replied_to']}"
    elif post.get("_rt_text_only"):
        row["type"] = "Retweet"
    else:
        row["type"] = "Standalone"
    return row


# ---------- Claude categorization (one call per run) ----------

SYSTEM = f"""You categorize posts by Elon Musk on X for a journalist's archive.
For each post, choose the single best category and, only if the post clearly also fits another, a secondary one; otherwise set secondary_category to "None".
Categories: {", ".join(CATEGORIES)}.
Write a neutral, factual one- or two-sentence description of what the post says or does, including what it responds to when context is given.
Do not editorialize, speculate about motive, or judge accuracy. If a post is only an emoji or a link, say so plainly.
If no context is given, describe the post on its own; don't remark that context is missing.
The category is the topic only. It never says whether a post is bigoted; that is the flag's job.

Flags are separate from the description. They are suggestions for a human reviewer who checks every
one, not verdicts, so lean toward flagging when a post plausibly fits: a missed flag is worse than a
false one. Possible flags: {", ".join(FLAGS)}. Return an empty list when none apply.
- Judge the post together with the post it replies to, quotes or retweets. A short reply such as
  "True", "Exactly", "!!", "💯" or "Wow" endorses whatever the parent post says, so if the parent
  is bigoted, flag the reply.
- A retweet repeats the original post, so judge the original's text.
- Watch for coded language and dog whistles (for example "globalists", "great replacement",
  "remigration", "groomer", "13/50", "invaders"), not only explicit slurs.
- flag_reason: one neutral sentence that quotes the exact words that triggered the flag, and says
  whether they come from his post or the post he endorsed. Leave it empty when there are no flags."""

SCHEMA = {
    "type": "object",
    "properties": {
        "posts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "post_id": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "secondary_category": {"type": "string", "enum": CATEGORIES + ["None"]},
                    "description": {"type": "string"},
                    "flags": {"type": "array", "items": {"type": "string", "enum": FLAGS}},
                    "flag_reason": {"type": "string"},
                },
                "required": ["post_id", "category", "secondary_category", "description", "flags", "flag_reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["posts"],
    "additionalProperties": False,
}


def classify(rows, chunk=40):
    client = anthropic.Anthropic()
    out = {}
    for i in range(0, len(rows), chunk):
        batch = [
            {"post_id": r["post_id"], "type": r["type"], "text": r["text"], "context": r.get("context")}
            for r in rows[i:i + chunk]
        ]
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
        )
        if resp.stop_reason == "refusal":
            print(f"Claude declined batch starting at {i}; leaving those rows uncategorized")
            continue
        text = next(b.text for b in resp.content if b.type == "text")
        for p in json.loads(text)["posts"]:
            out[p["post_id"]] = p
    return out


# ---------- Wayback Machine (best effort) ----------
# archive.org refuses connections from GitHub's runners, so the Apps Script endpoint (on Google's
# servers) submits each post to Save Page Now and fills in the snapshot link on later runs.

def wayback_keys():
    ak, sk = os.environ.get("WAYBACK_ACCESS_KEY"), os.environ.get("WAYBACK_SECRET_KEY")
    return {"access": ak, "secret": sk} if ak and sk else None


# ---------- Screenshots ----------

CARD = """<html><body style="margin:0;background:#fff;font-family:-apple-system,Helvetica,Arial,sans-serif">
<div id="card" style="width:550px;padding:16px;border:1px solid #cfd9de;border-radius:12px;margin:8px">
<div style="font-weight:700">Elon Musk <span style="color:#536471;font-weight:400">@elonmusk</span></div>
<div style="white-space:pre-wrap;font-size:17px;line-height:1.35;margin:10px 0">{text}</div>
<div style="color:#536471;font-size:14px">{date} CT · {type} · post {pid}</div>
<div style="color:#b42318;font-size:12px;margin-top:10px">Reconstructed from X API data: X's embed did not load at capture time.</div>
</div></body></html>"""


def screenshots(rows):
    """Returns {post_id: (png_bytes, method)}. Uses one browser for the whole batch."""
    from playwright.sync_api import sync_playwright

    shots = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        # Central time so the date printed on the embed matches the sheet's Date (CT) column
        ctx = browser.new_context(viewport={"width": 600, "height": 1200}, device_scale_factor=2,
                                  timezone_id="America/Chicago")
        for r in rows:
            pid = r["post_id"]
            page = ctx.new_page()  # fresh page per post so a failed load can't break the next one
            try:
                page.goto(
                    # Default conversation mode: for a reply, X's embed shows the parent post above it
                    f"https://platform.twitter.com/embed/Tweet.html?id={r['screenshot_id']}&theme=light&dnt=true",
                    wait_until="networkidle", timeout=30000,
                )
                page.locator("article").first.wait_for(state="visible", timeout=15000)
                page.wait_for_timeout(1500)  # let avatars and media finish painting
                # Crop to everything the embed drew (parent + reply), not just the first card
                box = page.evaluate("""() => {
                    const els = [...document.querySelectorAll('article')].map(e => e.getBoundingClientRect());
                    const top = Math.min(...els.map(b => b.top)), left = Math.min(...els.map(b => b.left));
                    const bottom = Math.max(...els.map(b => b.bottom)), right = Math.max(...els.map(b => b.right));
                    return {x: left + scrollX, y: top + scrollY, width: right - left, height: bottom - top};
                }""")
                shots[pid] = (page.screenshot(type="png", clip=box, full_page=True), "X embed")
            except Exception as e:
                print(f"Embed failed for {pid} ({str(e).splitlines()[0]}); rendering fallback card")
                page.close()
                page = ctx.new_page()
                page.set_content(CARD.format(
                    text=html.escape(r["text"]), date=r["date_ct"], type=r["type"], pid=pid))
                shots[pid] = (page.locator("#card").screenshot(type="png"), "API reconstruction")
            finally:
                page.close()
        browser.close()
    return shots


# ---------- Main ----------

def build_payload(rows, labels, shots):
    payload = []
    for r in rows:
        pid = r["post_id"]
        lab = labels.get(pid)
        cat = desc = flag = flag_reason = None
        if lab:
            cat, desc = lab["category"], lab["description"]
            flag = "; ".join(lab.get("flags") or []) or "None"
            flag_reason = lab.get("flag_reason") or ""
            if lab.get("secondary_category") not in (None, "None", cat):
                cat += f"; {lab['secondary_category']}"
        png, method = shots.get(pid, (None, None))
        payload.append({
            "post_id": pid,
            "date_ct": r["date_ct"],
            "type": r["type"],
            "link": r.get("original_link", r["link"]),
            "text": r.get("text"),
            "context": r.get("context") or "",
            "context_link": r.get("context_link") or "",
            "category": cat,
            "description": desc,
            "flag": flag,
            "flag_reason": flag_reason,
            "screenshot_name": f"{r['date_ct'][:10]}-elonmusk-{pid}.png",
            "screenshot_b64": base64.b64encode(png).decode() if png else None,
            "screenshot_method": method,
        })
    return payload


def refresh_existing():
    """One-off: add reply/quote context, re-describe, and re-screenshot rows already in the sheet."""
    if not using_getxapi():
        raise SystemExit("Refresh needs GetXAPI (it looks posts up one at a time).")
    existing = sheet_call("rows")["rows"]
    posts, includes, rows = [], {"tweets": [], "users": [], "media": []}, []
    for e in existing:
        if e["type"] == "Retweet":
            # The sheet already holds the original's text and link; just fill the context columns.
            orig_user, orig_id = e["link"].rstrip("/").split("/")[-3], e["link"].rstrip("/").split("/")[-1]
            rows.append({"post_id": e["post_id"], "date_ct": e["date_ct"], "type": "Retweet",
                         "link": e["link"], "context": f"Retweet of @{orig_user}", "context_link": e["link"],
                         "screenshot_id": orig_id})
            continue
        data = getx_detail(e["post_id"])
        if data:
            _getx_to_v2(data, posts, includes)
        else:
            print(f"Post {e['post_id']} unavailable now; leaving its row as is")
    fetch_parents(posts, includes)
    rows += [normalize(p, includes) for p in posts]
    print(f"Refreshing {len(rows)} rows")
    labels = classify([r for r in rows if r.get("text")])
    shots = screenshots(rows)
    payload = build_payload(rows, labels, shots)
    for i in range(0, len(payload), 10):
        res = sheet_call("update", {"rows": payload[i:i + 10]})
        print(f"Updated {res.get('updated')} rows")


def relabel_existing():
    """Re-run topics, flags and descriptions on every row from the text already in the sheet.
    Touches only Category, Flag, Flag reason and Claude description; never reviewer columns."""
    existing = [e for e in sheet_call("rows")["rows"] if e.get("text")]
    rows = [{"post_id": e["post_id"], "type": e["type"], "text": e["text"], "context": e.get("context") or None}
            for e in existing]
    print(f"Relabeling {len(rows)} rows")
    labels = classify(rows)
    payload = []
    for pid, lab in labels.items():
        flag = "; ".join(lab.get("flags") or []) or "None"
        cat = lab["category"]
        if lab.get("secondary_category") not in (None, "None", cat):
            cat += f"; {lab['secondary_category']}"
        payload.append({"post_id": pid, "category": cat, "description": lab["description"],
                        "flag": flag, "flag_reason": lab.get("flag_reason") or ""})
    for i in range(0, len(payload), 50):
        res = sheet_call("relabel", {"rows": payload[i:i + 50]})
        print(f"Relabeled {res.get('updated')} rows")


def post_is_live(post_id):
    """True/False from X's free oEmbed endpoint, or None if X didn't give a clear answer."""
    url = f"https://publish.twitter.com/oembed?url=https://twitter.com/x/status/{post_id}&omit_script=1"
    for attempt in range(2):
        try:
            r = requests.get(url, timeout=20)
        except Exception:
            return None
        if r.status_code == 200:
            return True
        if r.status_code != 404:
            return None
        time.sleep(2)  # ask twice before calling a post gone
    return False


def check_deleted(full_sweep):
    """Mark posts Deleted when X no longer serves them. Daily: the last 14 days. Weekly: everything."""
    today = datetime.now(CENTRAL).strftime("%Y-%m-%d")
    cutoff = (datetime.now(CENTRAL) - timedelta(days=14)).strftime("%Y-%m-%d")
    changes, checked = [], 0
    for e in sheet_call("rows")["rows"]:
        if e["type"] == "Retweet":
            continue  # a retweet's row points at someone else's post; its liveness says little about him
        if not full_sweep and e["date_ct"][:10] < cutoff:
            continue
        live = post_is_live(e["post_id"])
        checked += 1
        time.sleep(0.3)
        status = e.get("status") or ""
        if live is False and not status.startswith("Deleted"):
            changes.append({"post_id": e["post_id"], "status": f"Deleted (noticed {today})"})
        elif live is True and status.startswith("Deleted"):
            changes.append({"post_id": e["post_id"], "status": f"Live (was missing; {status[len('Deleted '):].strip('()')})"})
        elif live is True and not status:
            changes.append({"post_id": e["post_id"], "status": "Live"})
    for i in range(0, len(changes), 200):
        sheet_call("set_status", {"rows": changes[i:i + 200]})
    print(f"Checked {checked} posts; {sum(c['status'].startswith('Deleted') for c in changes)} newly missing")


def search_day(day):
    """All of his posts on one UTC day via GetXAPI advanced search (timelines stop after ~3,200 posts)."""
    nxt = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    headers = {"Authorization": f"Bearer {os.environ['GETXAPI_KEY']}"}
    params = {"q": f"from:elonmusk include:nativeretweets since:{day} until:{nxt}",
              "product": "Latest"}
    posts, includes = [], {"tweets": [], "users": [], "media": []}
    for _ in range(100):  # 20 posts a page; no day of his comes close to 2,000
        r = requests.get(f"{GETXAPI}/twitter/tweet/advanced_search", headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(30)
            continue
        r.raise_for_status()
        data = r.json()
        for t in data.get("tweets", []):
            if (t.get("author") or {}).get("userName", "").lower() == "elonmusk":
                _getx_to_v2(t, posts, includes)
        if not data.get("has_more") or not data.get("next_cursor"):
            break
        params["cursor"] = data["next_cursor"]
    return posts, includes


def backfill_days(since, until):
    """Fill in history day by day, newest first. Safe to rerun: posts already in the sheet are skipped,
    so a run that hits the time limit just continues where it stopped next time."""
    if not using_getxapi():
        raise SystemExit("Date backfill needs GetXAPI's search.")
    started = time.time()
    known = set(sheet_call("known_ids").get("ids", []))
    day = datetime.strptime(until, "%Y-%m-%d")
    first = datetime.strptime(since, "%Y-%m-%d")
    total = 0
    while day >= first:
        if time.time() - started > 5 * 3600:  # GitHub kills jobs at 6 hours
            print(f"Stopping for time; rerun the same range to continue from {day:%Y-%m-%d}")
            break
        d = day.strftime("%Y-%m-%d")
        posts, includes = search_day(d)
        posts = list({p["id"]: p for p in posts if p["id"] not in known}.values())
        if posts:
            fetch_parents(posts, includes)
            posts.sort(key=lambda p: int(p["id"]))
            rows = [normalize(p, includes) for p in posts]
            labels = classify(rows)
            shots = screenshots(rows)
            payload = build_payload(rows, labels, shots)
            for i in range(0, len(payload), 10):
                # No archive submissions here: the hourly runs work through old rows a few at a time
                sheet_call("append", {"rows": payload[i:i + 10], "wayback": None})
            known.update(p["id"] for p in posts)
            total += len(posts)
        print(f"{d}: {len(posts)} new posts")
        day -= timedelta(days=1)
    print(f"Backfill added {total} posts")


def main():
    source_key = "GETXAPI_KEY" if using_getxapi() else "X_BEARER_TOKEN"
    missing = [k for k in (source_key, "ANTHROPIC_API_KEY", "SHEET_ENDPOINT", "SHEET_SECRET") if not os.environ.get(k)]
    if missing:
        print(f"Not configured yet (missing secrets: {', '.join(missing)}); skipping run.")
        return
    if os.environ.get("REFRESH") == "true":
        return refresh_existing()
    if os.environ.get("RELABEL") == "true":
        return relabel_existing()
    if os.environ.get("CHECK_DELETED") == "true":
        return check_deleted(full_sweep=datetime.now(CENTRAL).weekday() == 6)
    if os.environ.get("BACKFILL_SINCE"):
        until = os.environ.get("BACKFILL_UNTIL") or datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d")
        return backfill_days(os.environ["BACKFILL_SINCE"], until)
    backfill_pages = int(os.environ.get("BACKFILL_PAGES") or 0)
    if wayback_keys():
        try:
            done = sheet_call("check_archives", {"wayback": wayback_keys()})
            print(f"Archive links resolved: {done.get('resolved', 0)}, still pending: {done.get('pending', 0)}")
        except Exception as e:  # archive checks must never stop the run
            print(f"Archive check skipped: {e}")
    latest = None if backfill_pages else sheet_call("latest_id").get("latest_id")
    max_pages = backfill_pages or (5 if latest else 1)  # an empty sheet starts with the newest page only
    posts, includes = get_posts(latest, max_pages)
    known = set(sheet_call("known_ids").get("ids", [])) if backfill_pages else set()
    posts = list({p["id"]: p for p in posts if p["id"] not in known}.values())  # a post can appear twice across pages
    if not posts:
        print("No new posts.")
        return
    posts.sort(key=lambda p: int(p["id"]))  # oldest first, so the sheet reads chronologically
    cap = int(os.environ.get("MAX_POSTS") or 500)
    posts = posts[:cap]
    rows = [normalize(p, includes) for p in posts]
    print(f"{len(rows)} new posts")

    labels = classify(rows)
    shots = screenshots(rows)
    payload = build_payload(rows, labels, shots)
    for i in range(0, len(payload), 10):
        res = sheet_call("append", {"rows": payload[i:i + 10], "wayback": wayback_keys()})
        print(f"Appended {res.get('appended')} rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Run failed: {e}", file=sys.stderr)
        raise
