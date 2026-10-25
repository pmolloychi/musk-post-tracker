# Review app

A private web page for going through flagged posts and approving or rejecting each one. It runs as its own Apps Script
project under your Google account. It reads the Musk Post Tracker sheet directly and saves every decision through the
sheet script's `review` action, so the app and the sheet always show the same thing.

## Set it up (about 5 minutes)

1. Go to [script.google.com](https://script.google.com) and click **New project**. Name it "Musk Record review".
2. Replace the contents of `Code.gs` with [`Code.gs`](Code.gs).
3. Click **+** next to Files, choose **HTML**, name it `Index`, and replace its contents with [`Index.html`](Index.html).
4. Go to **Project Settings > Script properties** and add two properties:
   - `SHEET_ENDPOINT`: the sheet's web app URL (the same value as the `SHEET_ENDPOINT` repo secret).
   - `SHEET_SECRET`: the sheet script's `SECRET` (the same value as the `SHEET_SECRET` repo secret).
5. Click **Deploy > New deployment > Web app**. Set **Execute as: Me** and **Who has access: Only myself**, then click **Deploy**.
   Approve the permissions it asks for: reading the sheet, reading screenshots in Drive, and calling the sheet endpoint.
6. Open the web app URL and bookmark it. It only opens for you, signed in to your Google account.

After you paste a new version of either file: **Deploy > Manage deployments > edit > Version: New version > Deploy**.

## Using it

- **Views:** AI-flagged (flagged and not yet reviewed), All unreviewed, Approved, Rejected, Everything. Each one shows a count.
- **Search** looks through his text, the post he responded to, @accounts, Claude's flag reason and description, and your rationale.
- **Approve finding** saves whatever the form holds, and needs at least one harm type, what he did, and a rationale. **Reject** needs nothing. **Skip for now**
  moves on without saving anything.
- Saving writes Finding, Harm type, Conduct, Rationale and Reviewed at (Central time) to that post's row. An Undo link
  appears for 8 seconds after each save. **Clear decision** blanks a past decision.
- On a post with no decision yet, harm type, what he did and the rationale are filled in from Claude's flag and flag reason.
  Approve as is, or change any of them first. What he did comes from the post type; for replies it picks "Endorsed another post"
  when Claude's reason says the words came from the post he replied to.
- Keyboard shortcuts, when you're not typing in a box: A approves, R rejects, S skips, and J and K move down and up.
- Reconstructed screenshots get a warning. They're cards built from API data, not X's own embed, so don't publish them as evidence.
