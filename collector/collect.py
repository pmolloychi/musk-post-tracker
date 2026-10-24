"""Collect new @elonmusk posts and log them to the Musk Post Tracker sheet.

One run:
  1. Ask the Apps Script endpoint for the newest Post ID already logged.
  2. Fetch newer posts from the X API (pay-per-use; each post returned is billed once per UTC day).
  3. Categorize and describe the whole batch in one Claude call.
  4. Submit each post to the Wayback Machine (best effort).
  5. Screenshot each post from X's official embed (falls back to a labeled card rendered from API data).
  6. Send rows + screenshots to the Apps Script endpoint, which appends to the sheet and saves images to Drive.

Env: X_BEARER_TOKEN, ANTHROPIC_API_KEY, SHEET_ENDPOINT, SHEET_SECRET,
     optional WAYBACK_ACCESS_KEY / WAYBACK_SECRET_KEY, BACKFILL_PAGES, MAX_POSTS.
"""

import base64
import html
import json
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import requests

from categories import CATEGORIES

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
        return {"id": ref_id, "username": u.get("username", "i"), "text": full_text(t)}

    created = datetime.fromisoformat(post["created_at"].replace("Z", "+00:00"))
    row = {
        "post_id": post["id"],
        "created_utc": post["created_at"],
        "date_ct": created.astimezone(CENTRAL).strftime("%Y-%m-%d %H:%M"),
        "link": f"https://x.com/elonmusk/status/{post['id']}",
        "text": full_text(post),
        "context": None,  # text of the post he replied to, quoted or retweeted
        "screenshot_id": post["id"],
    }
    if "retweeted" in refs:
        row["type"] = "Retweet"
        orig = ref_info(refs["retweeted"])
        if orig:
            row["text"] = f"RT @{orig['username']}: {orig['text']}"
            row["context"] = f"Original post by @{orig['username']}"
            row["screenshot_id"] = orig["id"]  # retweets have no embed of their own
            row["original_link"] = f"https://x.com/{orig['username']}/status/{orig['id']}"
    elif "quoted" in refs:
        row["type"] = "Quote"
        q = ref_info(refs["quoted"])
        if q:
            row["context"] = f"Quoting @{q['username']}: {q['text']}"
        if "replied_to" in refs:
            row["context"] = (row["context"] or "") + " (also a reply)"
    elif "replied_to" in refs:
        row["type"] = "Reply"
        p = ref_info(refs["replied_to"])
        if p:
            row["context"] = f"Replying to @{p['username']}: {p['text']}"
    else:
        row["type"] = "Standalone"
    return row


# ---------- Claude categorization (one call per run) ----------

SYSTEM = f"""You categorize posts by Elon Musk on X for a journalist's archive.
For each post, choose the single best category and, only if the post clearly also fits another, a secondary one; otherwise set secondary_category to "None".
Categories: {", ".join(CATEGORIES)}.
Write a neutral, factual one- or two-sentence description of what the post says or does, including what it responds to when context is given.
Do not editorialize, speculate about motive, or judge accuracy. If a post is only an emoji or a link, say so plainly."""

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
                },
                "required": ["post_id", "category", "secondary_category", "description"],
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
            {"post_id": r["post_id"], "type": r["type"], "text": r["text"], "context": r["context"]}
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

def wayback_submit(url):
    ak, sk = os.environ.get("WAYBACK_ACCESS_KEY"), os.environ.get("WAYBACK_SECRET_KEY")
    if not (ak and sk):
        return None
    try:
        r = requests.post(
            "https://web.archive.org/save",
            headers={"Accept": "application/json", "Authorization": f"LOW {ak}:{sk}"},
            data={"url": url, "capture_outlinks": "0", "skip_first_archive": "1"},
            timeout=60,
        )
        return r.json().get("job_id")
    except Exception as e:  # archive failures must never stop the run
        print(f"Wayback submit failed for {url}: {e}")
        return None


def wayback_results(jobs, max_wait=240):
    """jobs: {post_id: (url, job_id)} -> {post_id: archive link or note}"""
    results, pending, deadline = {}, dict(jobs), time.time() + max_wait
    while pending and time.time() < deadline:
        time.sleep(10)
        for pid, (url, job) in list(pending.items()):
            try:
                s = requests.get(f"https://web.archive.org/save/status/{job}", timeout=30).json()
            except Exception:
                continue
            if s.get("status") == "success":
                results[pid] = f"https://web.archive.org/web/{s['timestamp']}/{s['original_url']}"
                pending.pop(pid)
            elif s.get("status") == "error":
                results[pid] = f"FAILED ({s.get('status_ext', 'error')})"
                pending.pop(pid)
    for pid, (url, _) in pending.items():
        results[pid] = f"PENDING: https://web.archive.org/web/*/{url}"
    return results


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
        ctx = browser.new_context(viewport={"width": 600, "height": 1200}, device_scale_factor=2)
        for r in rows:
            pid = r["post_id"]
            page = ctx.new_page()  # fresh page per post so a failed load can't break the next one
            try:
                page.goto(
                    f"https://platform.twitter.com/embed/Tweet.html?id={r['screenshot_id']}&theme=light&dnt=true&conversation=none",
                    wait_until="networkidle", timeout=30000,
                )
                el = page.locator("article").first
                el.wait_for(state="visible", timeout=15000)
                page.wait_for_timeout(1200)  # let avatar and media finish painting
                shots[pid] = (el.screenshot(type="png"), "X embed")
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

def main():
    backfill_pages = int(os.environ.get("BACKFILL_PAGES") or 0)
    latest = None if backfill_pages else sheet_call("latest_id").get("latest_id")
    max_pages = backfill_pages or 5
    posts, includes = fetch_posts(latest, max_pages)
    known = set(sheet_call("known_ids").get("ids", [])) if backfill_pages else set()
    posts = [p for p in posts if p["id"] not in known]
    if not posts:
        print("No new posts.")
        return
    posts.sort(key=lambda p: int(p["id"]))  # oldest first, so the sheet reads chronologically
    cap = int(os.environ.get("MAX_POSTS") or 500)
    posts = posts[:cap]
    rows = [normalize(p, includes) for p in posts]
    print(f"{len(rows)} new posts")

    jobs = {}
    for r in rows:
        job = wayback_submit(r["link"])
        if job:
            jobs[r["post_id"]] = (r["link"], job)
        time.sleep(1)  # stay well under Save Page Now's per-minute limit

    labels = classify(rows)
    shots = screenshots(rows)
    archives = wayback_results(jobs) if jobs else {}

    for i in range(0, len(rows), 10):
        payload = []
        for r in rows[i:i + 10]:
            pid = r["post_id"]
            lab = labels.get(pid, {})
            cat = lab.get("category", "UNCATEGORIZED")
            if lab.get("secondary_category") not in (None, "None", cat):
                cat += f"; {lab['secondary_category']}"
            png, method = shots.get(pid, (None, None))
            payload.append({
                "post_id": pid,
                "date_ct": r["date_ct"],
                "type": r["type"],
                "link": r.get("original_link", r["link"]),
                "text": r["text"],
                "category": cat,
                "description": lab.get("description", ""),
                "archive": archives.get(pid, "not submitted"),
                "screenshot_name": f"{r['date_ct'][:10]}-elonmusk-{pid}.png",
                "screenshot_b64": base64.b64encode(png).decode() if png else None,
                "screenshot_method": method,
            })
        res = sheet_call("append", {"rows": payload})
        print(f"Appended {res.get('appended')} rows")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Run failed: {e}", file=sys.stderr)
        raise
