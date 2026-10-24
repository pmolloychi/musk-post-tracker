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
    monkeypatch.setattr(collect, "get_posts", lambda since, pages: (POSTS, INCLUDES))
    monkeypatch.setattr(collect, "screenshots", lambda rows: {r["post_id"]: (b"png", "X embed") for r in rows})

    class FakeMessages:
        def create(self, **kw):
            assert kw["output_config"]["format"]["type"] == "json_schema"
            batch = json.loads(kw["messages"][0]["content"])
            out = {"posts": [{"post_id": b["post_id"], "category": "Tesla", "secondary_category": "None", "description": "d", "flags": ["Racism"], "flag_reason": "Says \"x\"."} for b in batch]}
            return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=json.dumps(out))])
    monkeypatch.setattr(collect.anthropic, "Anthropic", lambda: types.SimpleNamespace(beta=types.SimpleNamespace(messages=FakeMessages())))
    monkeypatch.delenv("WAYBACK_ACCESS_KEY", raising=False)
    monkeypatch.delenv("BACKFILL_PAGES", raising=False)
    for k in ("GETXAPI_KEY", "ANTHROPIC_API_KEY", "SHEET_ENDPOINT", "SHEET_SECRET"):
        monkeypatch.setenv(k, "test")
    collect.main()
    assert len(sent) == 4
    assert sent[0]["category"] == "Tesla"
    assert sent[0]["flag"] == "Racism" and sent[0]["screenshot_method"]
    assert sent[1]["link"] == "https://x.com/alice/status/900"
    assert sent[2]["context"].startswith("Replying to @bob: something he replied to")
    assert sent[2]["context_link"] == "https://x.com/bob/status/901"
    assert sent[0]["screenshot_name"] == "2026-10-17-elonmusk-1849000000000000003.png"

def test_getxapi_conversion(monkeypatch):
    musk = {"id": "44196397", "userName": "elonmusk", "name": "Elon Musk"}
    alice = {"id": "1", "userName": "alice", "name": "Alice"}
    page = {"has_more": True, "next_cursor": "c2", "tweets": [
        {"id": "1849000000000000009", "createdAt": "Sat Oct 18 04:25:00 +0000 2026", "text": "pinned old one", "author": musk},
        {"id": "1849000000000000020", "createdAt": "Sat Oct 18 05:00:00 +0000 2026", "text": "RT @alice: hi", "author": musk,
         "retweeted_tweet": {"id": "900", "createdAt": "Fri Oct 17 05:00:00 +0000 2026", "text": "hi there in full", "author": alice}},
        {"id": "1849000000000000021", "createdAt": "Sat Oct 18 06:00:00 +0000 2026", "text": "@alice True", "author": musk,
         "isReply": True, "inReplyToId": "901", "inReplyToUsername": "alice"},
        {"id": "1849000000000000022", "createdAt": "Sat Oct 18 07:00:00 +0000 2026", "text": "Wow", "author": musk,
         "quoted_tweet": {"id": "902", "text": "quoted text", "author": alice}},
        {"id": "1849000000000000023", "createdAt": "Sat Oct 18 07:30:00 +0000 2026", "text": "someone else in thread", "author": alice},
    ]}
    old_page = {"has_more": True, "next_cursor": "c3", "tweets": [
        {"id": "1849000000000000001", "createdAt": "Sat Oct 18 01:00:00 +0000 2026", "text": "old", "author": musk}]}
    calls = []
    parent = {"id": "901", "createdAt": "Fri Oct 17 05:00:00 +0000 2026", "text": "what alice said", "author": alice}
    def fake_get(url, headers, params, timeout):
        assert headers["Authorization"] == "Bearer k"
        if url.endswith("/twitter/tweet/detail"):
            assert params == {"id": "901"}
            return types.SimpleNamespace(status_code=200, json=lambda: {"status": "success", "data": parent})
        calls.append(dict(params))
        assert url.endswith("/twitter/user/tweets_and_replies")
        return types.SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                     json=lambda: page if "cursor" not in params else old_page)
    monkeypatch.setattr(collect.requests, "get", fake_get)
    monkeypatch.setenv("GETXAPI_KEY", "k")
    posts, inc = collect.get_posts("1849000000000000010", 5)
    assert len(calls) == 2  # stops after a page with nothing new
    rows = [collect.normalize(p, inc) for p in sorted(posts, key=lambda p: int(p["id"]))]
    assert [r["type"] for r in rows] == ["Retweet", "Reply", "Quote"]
    assert rows[0]["text"] == "RT @alice: hi there in full" and rows[0]["screenshot_id"] == "900"
    assert rows[1]["context"] == "Replying to @alice: what alice said"
    assert rows[1]["context_link"] == "https://x.com/alice/status/901"
    assert rows[2]["context_link"] == "https://x.com/alice/status/902"
    assert rows[0]["context"] == "Retweet of @alice"
    assert rows[2]["context"] == "Quoting @alice: quoted text"
    assert rows[0]["date_ct"] == "2026-10-18 00:00"

def test_backfill_days(monkeypatch):
    musk = {"id": "44196397", "userName": "elonmusk", "name": "Elon Musk"}
    def fake_get(url, headers, params, timeout):
        assert url.endswith("/twitter/tweet/advanced_search")
        day = params["q"].split("since:")[1].split()[0]
        tid = {"2026-07-01": "1940000000000000001", "2026-07-02": "1940000000000000002"}[day]
        return types.SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {
            "has_more": False, "tweets": [{"id": tid, "createdAt": "Wed Jul 01 12:00:00 +0000 2026", "text": "hi", "author": musk}]})
    sent = []
    monkeypatch.setattr(collect.requests, "get", fake_get)
    monkeypatch.setenv("GETXAPI_KEY", "k")
    monkeypatch.setattr(collect, "sheet_call", lambda action, payload=None:
        {"ids": ["1940000000000000002"]} if action == "known_ids" else sent.extend(payload["rows"]) or {"ok": True})
    monkeypatch.setattr(collect, "classify", lambda rows: {})
    monkeypatch.setattr(collect, "screenshots", lambda rows: {})
    collect.backfill_days("2026-07-01", "2026-07-02")
    assert [r["post_id"] for r in sent] == ["1940000000000000001"]  # the known post is skipped


def test_relabel_and_check_deleted(monkeypatch):
    rows = [{"post_id": "1", "type": "Reply", "text": "True", "context": "Replying to @a: x", "date_ct": "2099-01-01 10:00", "status": "Live"},
            {"post_id": "2", "type": "Retweet", "text": "rt", "context": "", "date_ct": "2099-01-01 10:00", "status": ""},
            {"post_id": "3", "type": "Standalone", "text": "hi", "context": "", "date_ct": "2099-01-01 10:00", "status": "Deleted (noticed 2099-01-01)"}]
    calls = []
    monkeypatch.setattr(collect, "sheet_call", lambda a, p=None: {"rows": rows} if a == "rows" else calls.append((a, p)) or {"updated": 1})
    monkeypatch.setattr(collect, "classify", lambda rs: {r["post_id"]: {"category": "Other", "secondary_category": "None", "description": "d", "flags": [], "flag_reason": ""} for r in rs})
    collect.relabel_existing()
    action, payload = calls[-1]
    assert action == "relabel" and {r["flag"] for r in payload["rows"]} == {"None"}
    assert all(set(r) == {"post_id", "category", "description", "flag", "flag_reason"} for r in payload["rows"])

    monkeypatch.setattr(collect, "post_is_live", lambda pid: pid == "3")
    monkeypatch.setattr(collect.time, "sleep", lambda s: None)
    calls.clear()
    collect.check_deleted(full_sweep=True)
    statuses = {r["post_id"]: r["status"] for r in calls[0][1]["rows"]}
    assert statuses["1"].startswith("Deleted (noticed ")
    assert "2" not in statuses
    assert statuses["3"] == "Live (was missing; noticed 2099-01-01)"
