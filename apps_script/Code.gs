/**
 * Musk Post Tracker: sheet + Drive endpoint for the GitHub Action.
 * Paste into the sheet's Extensions > Apps Script, set the SECRET script property,
 * then Deploy > New deployment > Web app (Execute as: Me, Who has access: Anyone).
 * After pasting a new version: Deploy > Manage deployments > edit > Version: New version > Deploy.
 */

const SHEET_NAME = 'Posts';
const FOLDER_NAME = 'Musk Post Tracker screenshots';
const HEADERS = ['Date (CT)', 'Type', 'Link', 'Text', 'Responding to', 'Their post link', 'Category',
                 'Claude description', 'Archive link', 'Screenshot', 'Post ID'];
const COL = {};
HEADERS.forEach((h, i) => { COL[h] = i + 1; });
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
    ensureLayout_();
    if (body.action === 'latest_id') return json_({ ok: true, latest_id: latestId_() });
    if (body.action === 'known_ids') return json_({ ok: true, ids: knownIds_() });
    if (body.action === 'rows') return json_({ ok: true, rows: rows_() });
    if (body.action === 'append') return json_({ ok: true, appended: append_(body.rows, body.wayback) });
    if (body.action === 'update') return json_({ ok: true, updated: update_(body.rows) });
    if (body.action === 'check_archives') return json_(Object.assign({ ok: true }, checkArchives_(body.wayback)));
    return json_({ ok: false, error: 'unknown action' });
  } finally {
    lock.releaseLock();
  }
}

function sheet_() {
  return SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
}

// One-time migration: the first version had no context columns. Insert them after Text.
function ensureLayout_() {
  const sh = sheet_();
  if (sh.getRange(1, COL['Responding to']).getValue() === 'Responding to') return;
  sh.insertColumnsAfter(COL['Text'], 2);
  sh.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]).setFontWeight('bold');
  sh.setColumnWidth(COL['Responding to'], 360);
}

function dataRange_(col, width) {
  const sh = sheet_();
  const n = sh.getLastRow() - 1;
  return n < 1 ? null : sh.getRange(2, col, n, width || 1);
}

function knownIds_() {
  const r = dataRange_(COL['Post ID']);
  return r ? r.getDisplayValues().map(x => String(x[0]).trim()).filter(Boolean) : [];
}

// Post IDs are 19 digits, past what a spreadsheet number can hold exactly, so compare them as strings.
function latestId_() {
  let best = null;
  for (const id of knownIds_()) {
    if (!best || id.length > best.length || (id.length === best.length && id > best)) best = id;
  }
  return best;
}

function rows_() {
  const r = dataRange_(1, HEADERS.length);
  if (!r) return [];
  return r.getDisplayValues().map(v => ({
    date_ct: v[COL['Date (CT)'] - 1], type: v[COL['Type'] - 1], link: v[COL['Link'] - 1],
    post_id: String(v[COL['Post ID'] - 1]).trim(),
  })).filter(x => x.post_id);
}

function folderFor_(dateCt) {
  const props = PropertiesService.getScriptProperties();
  const rootId = props.getProperty('FOLDER_ID');
  let root;
  if (rootId) {
    root = DriveApp.getFolderById(rootId);
  } else {
    root = DriveApp.createFolder(FOLDER_NAME);
    props.setProperty('FOLDER_ID', root.getId());
  }
  const month = String(dateCt).slice(0, 7); // YYYY-MM subfolders keep each folder browsable
  const it = root.getFoldersByName(month);
  return it.hasNext() ? it.next() : root.createFolder(month);
}

function saveShot_(r) {
  if (!r.screenshot_b64) return '';
  const blob = Utilities.newBlob(Utilities.base64Decode(r.screenshot_b64), 'image/png', r.screenshot_name);
  const file = folderFor_(r.date_ct).createFile(blob);
  if (r.screenshot_method && r.screenshot_method !== 'X embed') file.setDescription(r.screenshot_method);
  return file.getUrl();
}

// Keep tweet text from being read as a formula or number.
function safe_(s) {
  s = String(s == null ? '' : s);
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

function append_(rows, wayback) {
  const sh = sheet_();
  const known = new Set(knownIds_());
  const out = [];
  let submits = 0;
  for (const r of rows) {
    if (known.has(r.post_id)) continue;
    const row = new Array(HEADERS.length).fill('');
    row[COL['Date (CT)'] - 1] = r.date_ct;
    row[COL['Type'] - 1] = r.type;
    row[COL['Link'] - 1] = r.link;
    row[COL['Text'] - 1] = safe_(r.text);
    row[COL['Responding to'] - 1] = safe_(r.context);
    row[COL['Their post link'] - 1] = r.context_link || '';
    row[COL['Category'] - 1] = r.category || 'UNCATEGORIZED';
    row[COL['Claude description'] - 1] = safe_(r.description);
    row[COL['Archive link'] - 1] =
      (wayback && submits++ < MAX_SUBMITS_PER_RUN) ? waybackSubmit_(r.link, wayback) : 'not submitted';
    row[COL['Screenshot'] - 1] = saveShot_(r);
    row[COL['Post ID'] - 1] = "'" + r.post_id;
    out.push(row);
    known.add(r.post_id);
  }
  if (out.length) {
    const start = sh.getLastRow() + 1;
    sh.getRange(start, 1, out.length, HEADERS.length).setValues(out);
    sh.getRange(start, COL['Text'], out.length, 2).setWrap(true);
  }
  return out.length;
}

// Refresh rows already in the sheet. Only fields that are present get overwritten.
function update_(rows) {
  const sh = sheet_();
  const rowOf = {};
  knownIds_().forEach((id, i) => { rowOf[id] = i + 2; });
  let updated = 0;
  for (const r of rows) {
    const rowNum = rowOf[r.post_id];
    if (!rowNum) continue;
    const set = (h, v) => sh.getRange(rowNum, COL[h]).setValue(v);
    set('Responding to', safe_(r.context));
    set('Their post link', r.context_link || '');
    if (r.category) set('Category', r.category);
    if (r.description) set('Claude description', safe_(r.description));
    if (r.screenshot_b64) {
      const old = String(sh.getRange(rowNum, COL['Screenshot']).getValue());
      const m = old.match(/\/d\/([^/]+)/);
      set('Screenshot', saveShot_(r));
      if (m) { try { DriveApp.getFileById(m[1]).setTrashed(true); } catch (err) { /* already gone */ } }
    }
    sh.getRange(rowNum, COL['Responding to']).setWrap(true);
    updated++;
  }
  return updated;
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
  const cellsRange = dataRange_(COL['Archive link']);
  if (!cellsRange || !keys) return { resolved: 0, pending: 0 };
  const cells = cellsRange.getValues();
  const links = dataRange_(COL['Link']).getValues();
  let resolved = 0, pending = 0, submits = 0;
  for (let i = 0; i < cells.length; i++) {
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
  cellsRange.setValues(cells);
  return { resolved: resolved, pending: pending };
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
