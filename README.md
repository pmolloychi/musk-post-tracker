# Musk Post Tracker

Every hour, a GitHub Action pulls new @elonmusk posts from GetXAPI, a third-party X data service. The official X API is a one-setting switch. It logs each post to the
[Musk Post Tracker sheet](https://docs.google.com/spreadsheets/d/13wqF9zWxmtZ73gkdRUmUOruUhOn8DRDs2PGOEci2J-I/edit)
with a category and a description written by Claude, submits the post to the Wayback Machine, and saves a
screenshot to Drive under `Musk Post Tracker screenshots/YYYY-MM/`.

## How a run works

1. The Action asks the sheet for the newest Post ID it already has.
2. It pages newest-first through GetXAPI's `/twitter/user/tweets_and_replies` until it reaches posts already in the sheet.
3. Claude (`claude-opus-5-5` at low effort) categorizes and describes the whole batch in one call. It picks
   from the fixed list in `collector/categories.py`.
4. Each post goes to Wayback Save Page Now. The Archive link column shows one of three things: a snapshot
   link, `FAILED (...)`, or `PENDING: <search link>`.
5. Headless Chromium screenshots X's official embed of each post at 2x. If the embed won't load, for example
   because the post was deleted or X blocks the request, the run instead renders a card from the API data. That
   card is labeled "Reconstructed from X API data", and the file's Drive description says so too.
6. The rows and images go to the Apps Script endpoint in the sheet. It appends rows, skips IDs the sheet
   already has, and files the images.

Retweets: the Link and screenshot point to the original post, because a retweet has no embed of its own, and the Text column
reads `RT @user: ...`.

## One-time setup

1. **GitHub**: create a private repo (e.g. `musk-post-tracker`) and push this folder.
2. **GetXAPI**: sign up at getxapi.com, add a few dollars of credit, and copy your API key.
3. **Anthropic**: create an API key at console.anthropic.com and set a monthly spend limit there.
4. **Sheet endpoint**:
   - Open the sheet and go to Extensions > Apps Script. Replace the code there with `apps_script/Code.gs`.
   - Under Project Settings > Script properties, add `SECRET`, set to a long random string.
   - Click Deploy > New deployment > Web app, with Execute as **Me** and Who has access **Anyone**. Authorize it, then copy the web app URL.
5. **Wayback (optional but recommended)**: log in at archive.org and copy the S3 keys from
   https://archive.org/account/s3.php.
6. **Repo secrets** (Settings > Secrets and variables > Actions): `GETXAPI_KEY`, `ANTHROPIC_API_KEY`,
   `SHEET_ENDPOINT` (the web app URL), `SHEET_SECRET`, `WAYBACK_ACCESS_KEY`, `WAYBACK_SECRET_KEY`.
7. Go to Actions > Collect Musk posts > Run workflow, and leave the input at 0 for the first run.

## Context for replies, quotes and retweets

The **Responding to** column shows who he's replying to, quoting or retweeting, and the text of that post.
**Their post link** links to it. For replies, the collector looks up each parent post on GetXAPI
($0.001 per reply). Screenshots use X's default embed, which shows the parent post above a reply.
Quotes already show the quoted post inside the card.

To add this to rows logged before the feature existed, go to Actions > Collect Musk posts > Run workflow
and tick `refresh_existing`. That run re-describes those rows and replaces their screenshots.

## Backfill

Run the workflow manually with `backfill_since` (and optionally `backfill_until`) as YYYY-MM-DD dates. It searches
GetXAPI one day at a time, newest first, skips posts already in the sheet, and stops after 5 hours. If it gets cut off,
run it again with the same dates and it picks up where it left off. New rows land at the bottom, so sort by Date afterwards.
`backfill_pages` (1 to 32) is the older timeline-based option, limited to his most recent ~3,200 posts.

## Flags and the review app

**Category** is a topic only. It never says whether a post is bigoted.

**Flag** and **Flag reason** are Claude's suggestions for a human reviewer, written in the same call as the topic and
description. Flag is zero or more of the labels in `collector/categories.py` (FLAGS), or "None". The prompt leans toward
flagging, reads short replies like "True" together with the post they answer, and looks for coded language.
Treat flags as a review queue, not findings.

**Finding, Harm type, Conduct, Rationale, Reviewed at** belong to the reviewer. Only the Apps Script `review` action
writes them. Collection, `refresh_existing` and `relabel_existing` never touch them. The review app posts:

```json
{"secret": "...", "action": "review", "post_id": "1849...", "finding": "Approved",
 "harm_type": "...", "conduct": "Endorsed another post", "rationale": "...", "reviewed_at": "2026-10-24 15:02"}
```

Finding must be Approved, Rejected or empty. Conduct must be Original statement, Endorsed another post,
Quoted with comment, Retweeted or empty. Fields left out are left unchanged. The response is `{"ok": true, "row": N, "written": [...]}`.
The app can read rows with `{"action": "rows"}`, or read the sheet directly.

**Screenshot source** says "X embed" (X's own rendering, fit for public evidence) or "Reconstructed" (a card built from API
data when the embed wouldn't load; don't publish these as evidence).

**Status** is set by a daily check at 10:37 UTC that asks X's free oEmbed endpoint whether each post from the last 14 days
still exists; on Sundays it checks every row. Missing posts get "Deleted (noticed YYYY-MM-DD)". X answers the same way for
deleted posts and posts withheld or hidden from logged-out viewers, so "Deleted" means "no longer publicly visible".
Retweets are skipped. Run it any time with the `check_deleted` box.

To re-run topics and flags on every existing row (after changing the category list or the prompt), tick `relabel_existing`.
It uses the text already in the sheet and costs one Claude call per 40 rows.

## Costs and limits (checked Oct 24, 2026)

- GetXAPI: $0.001 per page of about 20 posts. Hourly runs come to roughly $1 to $3 a month.
  It's a scraping-backed service, so it can miss posts when X changes its site, and X's terms ban scraping.
  The official X API costs about $0.005 per post ($25 to $45 a month at his volume) but is complete and first-party.
- Claude: one low-effort call per hourly run. Expect a few dollars to roughly $15 a month at his volume.
- GitHub Actions: an hourly run takes about 2 minutes, roughly 1,400 of the 2,000 free minutes a month on a private repo.
- Drive: 2x PNGs run about 100 KB to 1 MB each. At 100 to 200 posts a day, that's roughly 10 to 20 GB a year.
- Wayback: x.com captures often fail or save a login wall. Treat the screenshot as the reliable record.

## Switching to the official X API

Add an `X_BEARER_TOKEN` secret, then create a repository variable `SOURCE` with the value `x`
(Settings > Secrets and variables > Actions > Variables). Each source is one fetch function in `collector/collect.py`
that returns X API v2-shaped data, so nothing downstream changes.

GetXAPI caveats: replies don't include the parent post's text, only its author. Its docs don't say whether
retweets come through `tweets_and_replies`, so check the first runs against his profile.

## Not covered yet

- Edits after a post is logged.
- Community Notes and live engagement counts, which the embed doesn't show reliably.
