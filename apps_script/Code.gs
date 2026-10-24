/**
 * Musk Post Tracker: sheet + Drive endpoint for the GitHub Action.
 * Paste into the sheet's Extensions > Apps Script, set the SECRET script property,
 * then Deploy > New deployment > Web app (Execute as: Me, Who has access: Anyone).
 */

const SHEET_NAME = 'Posts';
const FOLDER_NAME = 'Musk Post Tracker screenshots';
const ID_COL = 9; // column I, Post ID
const LINK_COL = 3; // column C
const ARCHIVE_COL = 7; // column G
const MAX_SUBMITS_PER_RUN = 15; // Save Page Now allows a handful of captures per minute per account

function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  const props = PropertiesService.getScriptProperties();
  if (!body.secret || body.secret !== props.getProperty('SECRET')) {
    return json_({ ok: false, error: 'bad secret' });
  }
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    if (body.action === 'latest_id') return json_({ ok: true, latest_id: latestId_() });
    if (body.action === 'known_ids') return json_({ ok: true, ids: knownIds_() });
    if (body.action === 'append') return json_({ ok: true, appended: append_(body.rows, body.wayback) });
    if (body.action === 'check_archives') return json_(Object.assign({ ok: true }, checkArchives_(body.wayback)));
    return json_({ ok: false, error: 'unknown action' });
  } finally {
    lock.releaseLock();
  }
}

function sheet_() {
  return SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
}

function knownIds_() {
  const sh = sheet_();
  const n = sh.getLastRow() - 1;
  if (n < 1) return [];
  return sh.getRange(2, ID_COL, n, 1).getDisplayValues().map(r => String(r[0]).trim()).filter(Boolean);
}

// Post IDs are 19 digits, past what a spreadsheet number can hold exactly, so compare them as strings.
function latestId_() {
  let best = null;
  for (const id of knownIds_()) {
    if (!best || id.length > best.length || (id.length === best.length && id > best)) best = id;
  }
  return best;
}

function folderFor_(dateCt) {
  const props = PropertiesService.getScriptProperties();
  let rootId = props.getProperty('FOLDER_ID');
  let root;
  if (rootId) {
    root = DriveApp.getFolderById(rootId);
  } else {
    root = DriveApp.createFolder(FOLDER_NAME);
    props.setProperty('FOLDER_ID', root.getId());
  }
  const month = dateCt.slice(0, 7); // YYYY-MM subfolders keep each folder browsable
  const it = root.getFoldersByName(month);
  return it.hasNext() ? it.next() : root.createFolder(month);
}

// Keep tweet text from being read as a formula or number.
function safe_(s) {
  s = String(s == null ? '' : s);
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

function append_(rows, wayback) {
  let submits = 0;
  const sh = sheet_();
  const known = new Set(knownIds_());
  const out = [];
  for (const r of rows) {
    if (known.has(r.post_id)) continue;
    let shot = '';
    if (r.screenshot_b64) {
      const blob = Utilities.newBlob(Utilities.base64Decode(r.screenshot_b64), 'image/png', r.screenshot_name);
      const file = folderFor_(r.date_ct).createFile(blob);
      if (r.screenshot_method && r.screenshot_method !== 'X embed') file.setDescription(r.screenshot_method);
      shot = file.getUrl();
    }
    out.push([
      r.date_ct, r.type, r.link, safe_(r.text), r.category, safe_(r.description),
      (wayback && submits++ < MAX_SUBMITS_PER_RUN) ? waybackSubmit_(r.link, wayback) : 'not submitted',
      shot, "'" + r.post_id,
    ]);
    known.add(r.post_id);
  }
  if (out.length) {
    const start = sh.getLastRow() + 1;
    sh.getRange(start, 1, out.length, out[0].length).setValues(out);
    sh.getRange(start, 4, out.length, 1).setWrap(true);
  }
  return out.length;
}

// ---------- Wayback Machine ----------
// Runs here rather than in the GitHub Action because archive.org refuses connections from GitHub's runners.

function waybackSubmit_(url, keys) {
  try {
    const res = UrlFetchApp.fetch('https://web.archive.org/save', {
      method: 'post',
      headers: { Accept: 'application/json', Authorization: 'LOW ' + keys.access + ':' + keys.secret },
      payload: { url: url, capture_outlinks: '0', skip_first_archive: '1' },
      muteHttpExceptions: true,
    });
    const data = JSON.parse(res.getContentText() || '{}');
    if (data.job_id) return 'PENDING job:' + data.job_id;
    return 'not submitted (' + (data.message || data.status_ext || res.getResponseCode()) + ')';
  } catch (err) {
    return 'not submitted (' + String(err).slice(0, 80) + ')';
  }
}

// Each hourly run: turn finished jobs into snapshot links, and submit older rows that never got archived.
function checkArchives_(keys) {
  const sh = sheet_();
  const n = sh.getLastRow() - 1;
  if (n < 1 || !keys) return { resolved: 0, pending: 0 };
  const cells = sh.getRange(2, ARCHIVE_COL, n, 1).getValues();
  const links = sh.getRange(2, LINK_COL, n, 1).getValues();
  let resolved = 0, pending = 0, submits = 0;
  for (let i = 0; i < n; i++) {
    const v = String(cells[i][0]);
    if (v.indexOf('PENDING job:') === 0) {
      const job = v.slice('PENDING job:'.length);
      try {
        const s = JSON.parse(UrlFetchApp.fetch('https://web.archive.org/save/status/' + job,
          { muteHttpExceptions: true, headers: { Authorization: 'LOW ' + keys.access + ':' + keys.secret } }).getContentText());
        if (s.status === 'success') {
          cells[i][0] = 'https://web.archive.org/web/' + s.timestamp + '/' + s.original_url; resolved++;
        } else if (s.status === 'error') {
          cells[i][0] = 'FAILED (' + (s.status_ext || 'error') + ')'; resolved++;
        } else {
          pending++;
        }
      } catch (err) {
        pending++;
      }
    } else if (v.indexOf('not submitted') === 0 && submits < MAX_SUBMITS_PER_RUN) {
      cells[i][0] = waybackSubmit_(String(links[i][0]), keys); submits++; pending++;
    }
  }
  sh.getRange(2, ARCHIVE_COL, n, 1).setValues(cells);
  return { resolved: resolved, pending: pending };
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
