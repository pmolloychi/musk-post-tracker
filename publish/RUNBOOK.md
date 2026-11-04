# Public site runbook

The public site is built once a day by **Publish public site** (`.github/workflows/publish.yml`) from the sheet's
`export` action. When something goes wrong, the live site keeps its last good version. GitHub emails a failed scheduled
run to whoever last edited the workflow's schedule line.

## When a run fails

Open the failed run on GitHub (Actions → Publish public site). Its summary has one line like
`FAILED stale-data: newest post is from 2026-11-04 09:00 UTC`. Find that check below.

### no-rows
The export came back empty. Usually the sheet endpoint is broken or the secret changed. Check the collector's last
run too; if it failed the same way, see [Redeploy the sheet script](#redeploy-the-sheet-script).

### required-field
A post has no Post ID, date, type or link. Find it in the sheet and fill it in, or ask Claude to look.

### duplicate-id
The same Post ID is in the sheet twice. Delete the newer copy of the row.

### unknown-type, unknown-topic, unknown-status, bad-date
A value the site doesn't recognize. Either the collector changed what it writes or someone edited a cell. If the
collector changed on purpose, update `publish/config.py` to match.

### post-count-drop
The sheet has more than 1% fewer posts than the last published snapshot. Rows were probably deleted by mistake: undo
it in the sheet (File → Version history). Nothing is published until the count recovers.

### stale-data
The newest post is more than 48 hours old, so the collector has probably stopped. Check its runs (Actions → Collect
Musk posts).

### sample-missing, sample-size, sample-hash, sample-undecided
Something is wrong with the random sample: a sample post left the sheet, or the Sample tab was edited. Don't change
the Sample tab by hand. Ask Claude to look before doing anything else.

### allow-list
The build found a field it isn't allowed to publish and stopped before writing anything. This is the privacy guard
working. Ask Claude to look; don't turn it off.

## Run it again

Actions → Publish public site → Run workflow.

## Roll back

On GitHub, open the `site` branch's commits, open the bad snapshot commit, and choose **Revert** (or ask Claude to
revert it). Pages serves the reverted version within a few minutes. The next daily run builds a fresh snapshot, so fix
the cause first.

## Redeploy the sheet script

1. Open the sheet → Extensions → Apps Script.
2. Paste the new `apps_script/Code.gs` over the old one and save.
3. Deploy → Manage deployments → the pencil → Version: New version → Deploy. The URL stays the same.

The review app is a separate project: paste `review_app/Code.gs` and `review_app/Index.html` into it, then publish a
new version the same way.

## Replace the sheet secret

1. Make a new secret (for example `openssl rand -hex 32`).
2. In the sheet's Apps Script: Project Settings → Script properties → `SECRET` → the new value.
3. On GitHub: Settings → Secrets and variables → Actions → `SHEET_SECRET` → the new value.

## Lock the random sample

The draw takes three steps, so its random number comes from a public source nobody controls. Don't review any sample
post until step 3 is done; decisions made before the draw don't count anyway.

1. In the review app's **Sample** view, click **Freeze the pool**. It shows `frame_size` and `frame_sha256`.
2. Copy `sample/LOCK.template.md` to `sample/LOCK.md` and fill in `frozen_at_ct`, those two values, and a
   `beacon_pulse_utc` minute at least an hour after you'll commit (for example `2026-11-04T21:00:00Z`). Commit it to
   `main`, then save its GitHub page at https://web.archive.org/save and put the archive link in `publish/config.py` as
   `LOCK_WAYBACK_URL`. All of this must happen before that minute.
3. After that minute, click **Draw sample**. The app checks LOCK.md against the frozen pool, refuses if LOCK.md changed
   at or after the pulse minute, and fetches the beacon value itself.

## Launch

Don't launch until all three are done:

1. You've re-read every approved finding, and you stand behind each rationale, including any kept from Claude's draft.
2. A run with publishing off has passed, and you've looked over the built site.
3. The legal read is done.

Then, on GitHub:

1. Settings → Secrets and variables → Actions → Variables → New variable: `PUBLISH_ENABLED` = `true`.
2. Actions → Publish public site → Run workflow. Wait for it to finish; it creates the `site` branch.
3. Settings → Pages → Build and deployment → Deploy from a branch → `site` / `(root)` → Save.

## Takedowns and contact

Requests to remove a post or a finding go to the contact address on the methods page. Record any change on the
corrections page (`publish/content/corrections.md`), then fix the data and run the workflow.
