import json, os, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
import collect

INCLUDES = {
    "tweets": [
        {"id": "900", "author_id": "1", "text": "original post text"},
        {"id": "901", "author_id": "2", "text": "something he replied to"},
    ],
    "users": [{"id": "1", "username": "alice"}, {"id": "2", "username": "bob"}],
    "media": [],
}
POSTS = [
    {"id": "1849000000000000003", "created_at": "2026-10-18T04:25:00.000Z", "text": "short", "note_tweet": {"text": "the full long text"}},
    {"id": "1849000000000000004", "created_at": "2026-10-18T05:00:00.000Z", "text": "RT @alice: orig…", "referenced_tweets": [{"type": "retweeted", "id": "900"}]},
    {"id": "1849000000000000005", "created_at": "2026-10-18T06:00:00.000Z", "text": "@bob True", "referenced_tweets": [{"type": "replied_to", "id": "901"}]},
    {"id": "1849000000000000006", "created_at": "2026-10-18T07:00:00.000Z", "text": "Wow", "referenced_tweets": [{"type": "quoted", "id": "900"}]},
]

def test_normalize():
    rows = [collect.normalize(p, INCLUDES) for p in POSTS]
    assert [r["type"] for r in rows] == ["Standalone", "Retweet", "Reply", "Quote"]
    assert rows[0]["text"] == "the full long text"
    assert rows[0]["date_ct"] == "2026-10-17 23:25"  # Central time, not UTC date
    assert rows[1]["text"] == "RT @alice: original post text"
    assert rows[1]["screenshot_id"] == "900"
    assert rows[1]["original_link"] == "https://x.com/alice/status/900"
    assert rows[2]["context"].startswith("Replying to @bob")
    assert rows[3]["context"].startswith("Quoting @alice")

def test_fallback_screenshot():
    rows = [collect.normalize(POSTS[0], INCLUDES)]
    shots = collect.screenshots(rows)
    png, method = shots[rows[0]["post_id"]]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    print("screenshot method:", method)

def test_main_end_to_end(monkeypatch):
    sent = []
    def fake_sheet(action, payload=None):
        if action == "latest_id": return {"ok": True, "latest_id": "1849000000000000002"}
        if action == "append":
            sent.extend(payload["rows"]); return {"ok": True, "appended": len(payload["rows"])}
    monkeypatch.setattr(collect, "sheet_call", fake_sheet)
    monkeypatch.setattr(collect, "fetch_posts", lambda since, pages: (POSTS, INCLUDES))
    monkeypatch.setattr(collect, "screenshots", lambda rows: {r["post_id"]: (b"png", "X embed") for r in rows})

    class FakeMessages:
        def create(self, **kw):
            assert kw["output_config"]["format"]["type"] == "json_schema"
            batch = json.loads(kw["messages"][0]["content"])
            out = {"posts": [{"post_id": b["post_id"], "category": "Tesla", "secondary_category": "None", "description": "d"} for b in batch]}
            return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=json.dumps(out))])
    monkeypatch.setattr(collect.anthropic, "Anthropic", lambda: types.SimpleNamespace(beta=types.SimpleNamespace(messages=FakeMessages())))
    monkeypatch.delenv("WAYBACK_ACCESS_KEY", raising=False)
    monkeypatch.delenv("BACKFILL_PAGES", raising=False)
    collect.main()
    assert len(sent) == 4
    assert sent[0]["category"] == "Tesla"
    assert sent[1]["link"] == "https://x.com/alice/status/900"
    assert sent[0]["screenshot_name"] == "2026-10-17-elonmusk-1849000000000000003.png"
    assert sent[0]["archive"] == "not submitted"
