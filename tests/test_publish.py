import hashlib, json, os, sys
from datetime import datetime, timezone

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "collector"))
sys.path.insert(0, os.path.join(HERE, "..", "publish"))
import build
import config
import export
import render
from export import PublishError


def load(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as f:
        return json.load(f)


NOW = datetime(2026, 4, 25, 13, 0, tzinfo=timezone.utc)


def check(data, previous=None):
    export.validate(data, previous, now=NOW)


def built(tmp_path, name):
    out = tmp_path / "site"
    assert build.main(["--fixture", os.path.join(HERE, "fixtures", name), "--out", str(out)]) == 0
    return out


def read_all(out):
    return {str(p.relative_to(out)): p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file()}


# ---------- Counting ----------

def test_two_topic_post_counts_once_in_total_and_once_per_topic():
    data = load("export_complete.json")
    site = export.compute(data, 3)
    two = [r for r in data["rows"] if r["category"] == "US politics & elections; Immigration"][0]["post_id"]
    assert sum(r["wrote_political"] + r["retweeted_political"] for r in site.monthly) == site.meta["political_posts"]
    assert two in [p["post_id"] for p in site.topics["immigration"]["posts"]]
    assert two in [p["post_id"] for p in site.topics["us-politics-and-elections"]["posts"]]


def test_wrote_and_retweeted_split():
    site = export.compute(load("export_complete.json"), 3)
    rows = load("export_complete.json")["rows"]
    pol = [r for r in rows if set(export.split(r["category"])) & set(config.POLITICAL_TOPICS)]
    assert sum(m["retweeted_political"] for m in site.monthly) == sum(r["type"] == "Retweet" for r in pol)
    assert sum(m["wrote_political"] for m in site.monthly) == sum(r["type"] != "Retweet" for r in pol)


@pytest.mark.parametrize("raw,want", [("", "live"), ("Live", "live"), ("Live (was missing; noticed 2026-03-02)", "live"),
                                      ("Deleted (noticed 2026-04-02)", "deleted"), ("Gone", None)])
def test_status_prefixes(raw, want):
    assert export.status_of(raw) == want


def test_category_split_and_two_part_limit():
    assert export.split("US politics & elections; Immigration") == ["US politics & elections", "Immigration"]
    data = load("export_partial.json")
    data["rows"][0]["category"] = "Tesla; Immigration; Religion"
    with pytest.raises(PublishError) as e:
        check(data)
    assert e.value.check == "unknown-topic"


def test_archive_status_strings_become_null():
    site = export.compute(load("export_complete.json"), 3)
    links = {p["archive_link"] for t in site.topics.values() for p in t["posts"]}
    assert None in links
    assert all(v is None or v.startswith("https://web.archive.org/") for v in links)


# ---------- Validation ----------

def broken(mutate):
    data = load("export_partial.json")
    mutate(data)
    return data


@pytest.mark.parametrize("name,mutate,want", [
    ("blank link", lambda d: d["rows"][0].update(link=""), "required-field"),
    ("bad type", lambda d: d["rows"][0].update(type="Thread"), "unknown-type"),
    ("bad topic", lambda d: d["rows"][0].update(category="Sports"), "unknown-topic"),
    ("bad status", lambda d: d["rows"][0].update(status="Gone"), "unknown-status"),
    ("duplicate", lambda d: d["rows"].append(dict(d["rows"][0])), "duplicate-id"),
    ("bad date", lambda d: d["rows"][0].update(date_ct="yesterday"), "bad-date"),
    ("empty", lambda d: d.update(rows=[]), "no-rows"),
])
def test_each_validation_failure(name, mutate, want):
    with pytest.raises(PublishError) as e:
        check(broken(mutate))
    assert e.value.check == want


def test_post_count_drop_and_first_run():
    data = load("export_partial.json")
    check(data, None)  # no previous snapshot: the drop check is skipped
    check(data, {"total_posts": len(data["rows"])})
    with pytest.raises(PublishError) as e:
        check(data, {"total_posts": len(data["rows"]) * 2})
    assert e.value.check == "post-count-drop"


def test_stale_data():
    with pytest.raises(PublishError) as e:
        export.validate(load("export_partial.json"), None, now=datetime(2026, 5, 10, tzinfo=timezone.utc))
    assert e.value.check == "stale-data"


def test_sample_post_missing_from_rows():
    data = load("export_complete.json")
    data["rows"] = [r for r in data["rows"] if r["post_id"] != data["sample"]["ids"][0]["post_id"]]
    with pytest.raises(PublishError) as e:
        check(data)
    assert e.value.check == "sample-missing"


def test_failure_line_points_at_runbook():
    assert PublishError("stale-data", "x").line() == "FAILED stale-data: x. See publish/RUNBOOK.md#stale-data"


# ---------- Privacy ----------

def test_allow_list_rejects_private_key(tmp_path):
    with pytest.raises(PublishError) as e:
        render.write_json(str(tmp_path), "x.json", [{"post_id": "1", "text": "t", "flag_reason": "secret"}], set())
    assert e.value.check == "allow-list"


def test_allow_list_rejects_review_key_on_unreviewed_post(tmp_path):
    with pytest.raises(PublishError):
        render.write_json(str(tmp_path), "x.json", {"posts": [{"post_id": "1", "rationale": "r"}]}, set())
    render.write_json(str(tmp_path), "x.json", {"posts": [{"post_id": "1", "rationale": "r"}]}, {"1"})


def test_no_private_text_anywhere_in_output(tmp_path):
    data = load("export_complete.json")
    data["rows"][3]["flag_reason"] = "PRIVATE-REASON"  # extra keys from the export never reach the site
    data["rows"][3]["description"] = "PRIVATE-DESCRIPTION"
    path = tmp_path / "f.json"
    path.write_text(json.dumps(data))
    out = tmp_path / "site"
    assert build.main(["--fixture", str(path), "--out", str(out)]) == 0
    text = "".join(read_all(out).values())
    assert "PRIVATE-" not in text


# ---------- The sample ----------

@pytest.mark.parametrize("finding,at,want", [
    ("Approved", "2026-04-20 10:00", True),   # same minute as the draw counts
    ("Rejected", "2026-04-20 11:00", True),
    ("Approved", "2026-04-20 09:59", False),  # decided before the draw, e.g. after an Undo back to an old decision
    ("", "2026-04-20 11:00", False),
    ("Approved", "", False),
])
def test_decided_rule(finding, at, want):
    assert export.is_decided(finding, at, "2026-04-20 10:00") is want


def test_partial_sample_is_absent_from_every_output(tmp_path):
    data = load("export_partial.json")
    out = built(tmp_path, "export_partial.json")
    files = read_all(out)
    assert "data/sample.txt" not in files
    meta = json.loads(files["data/meta.json"])
    assert meta["sample"]["decided_count"] == 1
    assert "Review in progress: 1 of 3" in files["index.html"]
    selected = {x["post_id"] for x in data["reviewed"]}
    reviewed = {p["post_id"] for p in json.loads(files["data/reviewed.json"])}
    assert reviewed == selected


def test_complete_sample_is_published_and_matches_its_lock(tmp_path):
    data = load("export_complete.json")
    out = built(tmp_path, "export_complete.json")
    text = (out / "data" / "sample.txt").read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode()).hexdigest() == data["sample"]["sample_sha256"]
    reviewed = json.loads((out / "data" / "reviewed.json").read_text())
    groups = {p["post_id"]: p["group"] for p in reviewed}
    for x in data["sample"]["ids"]:
        assert groups[x["post_id"]] == "sample"


def test_sample_post_is_never_a_selected_example():
    data = load("export_complete.json")
    sid = data["sample"]["ids"][0]["post_id"]
    site = export.compute(data, 3)
    assert sid not in {p["post_id"] for p in site.selected}


def test_rates_and_strict_match():
    site = export.compute(load("export_complete.json"), 3)
    assert site.sample["headline"]["k"] == 2      # two approvals
    assert site.sample["strict"]["k"] == 1        # one of them names a harm the AI flagged
    assert [p["sample_no"] for p in site.sample["rereviewed"]] == [2]


def test_tampered_sample_fails_its_fingerprint():
    data = load("export_complete.json")
    data["sample"]["ids"][0], data["sample"]["ids"][1] = (
        dict(data["sample"]["ids"][0], post_id=data["sample"]["ids"][1]["post_id"]),
        dict(data["sample"]["ids"][1], post_id=data["sample"]["ids"][0]["post_id"]))
    with pytest.raises(PublishError) as e:
        export.compute(data, 3)
    assert e.value.check == "sample-hash"


def test_undecided_sample_post_fails():
    data = load("export_complete.json")
    data["reviewed"] = [x for x in data["reviewed"] if x["post_id"] != data["sample"]["ids"][2]["post_id"]]
    with pytest.raises(PublishError) as e:
        export.compute(data, 3)
    assert e.value.check == "sample-undecided"


def test_rate_hidden_until_sample_complete():
    site = export.compute(load("export_partial.json"), 3)
    assert not site.sample["complete"] and "headline" not in site.sample


@pytest.mark.parametrize("k,n,lo,hi", [(62, 100, 0.522, 0.709), (0, 100, 0.0, 0.037)])
def test_wilson(k, n, lo, hi):
    a, b = export.wilson(k, n)
    assert a == pytest.approx(lo, abs=0.001) and b == pytest.approx(hi, abs=0.001)


def test_draw_can_be_reproduced_from_seed_and_frame():
    # The review app ranks the frame by SHA-256 of "seed:id" and takes the first N. This is that rule in Python.
    frame = sorted(str(2000000000000000000 + i * 7) for i in range(50))
    seed = "00112233445566778899aabbccddeeff"
    ids = [i for _, i in sorted((hashlib.sha256(f"{seed}:{i}".encode()).hexdigest(), i) for i in frame)][:10]
    assert len(set(ids)) == 10
    text = export.canonical_sample_text(ids, seed)
    assert text.endswith(f"\nseed:{seed}\n") and text.count("\n") == 11


def test_build_writes_nojekyll_and_stylesheet(tmp_path):
    out = built(tmp_path, "export_complete.json")
    assert (out / ".nojekyll").exists() and (out / "assets" / "tpa.css").exists()
    assert (out / "topics" / "immigration" / "index.html").exists()
