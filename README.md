# Musk Post Tracker

Every hour, a GitHub Action pulls new @elonmusk posts from the X API. It logs each post to the
[Musk Post Tracker sheet](https://docs.google.com/spreadsheets/d/13wqF9zWxmtZ73gkdRUmUOruUhOn8DRDs2PGOEci2J-I/edit)
with a category and a description written by Claude, submits the post to the Wayback Machine, and saves a
screenshot to Drive under `Musk Post Tracker screenshots/YYYY-MM/`.

## How a run works

1. The Action asks the sheet for the newest Post ID it already has.
2. It fetches newer posts from `GET /2/users/44196397/tweets`, the official API, billed pay-per-use.
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
2. **X API**: at console.x.com, create an app, add credit, and set the **monthly spend cap to $50**. Copy the
   app's Bearer Token.
3. **Anthropic**: create an API key at console.anthropic.com and set a monthly spend limit there.
4. **Sheet endpoint**:
   - Open the sheet and go to Extensions > Apps Script. Replace the code there with `apps_script/Code.gs`.
   - Under Project Settings > Script properties, add `SECRET`, set to a long random string.
   - Click Deploy > New deployment > Web app, with Execute as **Me** and Who has access **Anyone**. Authorize it, then copy the web app URL.
5. **Wayback (optional but recommended)**: log in at archive.org and copy the S3 keys from
   https://archive.org/account/s3.php.
6. **Repo secrets** (Settings > Secrets and variables > Actions): `X_BEARER_TOKEN`, `ANTHROPIC_API_KEY`,
   `SHEET_ENDPOINT` (the web app URL), `SHEET_SECRET`, `WAYBACK_ACCESS_KEY`, `WAYBACK_SECRET_KEY`.
7. Go to Actions > Collect Musk posts > Run workflow, and leave the input at 0 for the first run.

## Backfill

Run the workflow manually with `backfill_pages` set to a value from 1 to 32. Each page holds 100 posts, newest first. X only returns
a user's most recent ~3,200 posts, so a backfill can't reach further back than that. Posts already in the sheet are skipped.

## Costs and limits (checked Oct 24, 2026)

- X API: about $0.005 per post returned, deduped per UTC day. Posts he quotes or replies to may also count as
  returned resources, so budget for up to about 2x his post count. The $50 cap stops it from running away.
- Claude: one low-effort call per hourly run. Expect a few dollars to roughly $15 a month at his volume.
- GitHub Actions: an hourly run takes about 2 minutes, roughly 1,400 of the 2,000 free minutes a month on a private repo.
- Drive: 2x PNGs run about 100 KB to 1 MB each. At 100 to 200 posts a day, that's roughly 10 to 20 GB a year.
- Wayback: x.com captures often fail or save a login wall. Treat the screenshot as the reliable record.

## Swapping the data source

All X-specific fetching lives in `fetch_posts()` in `collector/collect.py`. It returns X API v2-shaped `data` and
`includes`. Pointing the collector at another provider (for example twitterapi.io) only means writing a replacement
function that returns the same shape. Nothing downstream changes.

## Not covered yet

- Edits and deletions after a post is logged, which would need a re-check job.
- Community Notes and live engagement counts, which the embed doesn't show reliably.
