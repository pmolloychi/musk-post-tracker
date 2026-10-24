/**
 * Musk Post Tracker: sheet + Drive endpoint for the GitHub Action and the review app.
 * Paste into the sheet's Extensions > Apps Script, set the SECRET script property,
 * then Deploy > New deployment > Web app (Execute as: Me, Who has access: Anyone).
 * After pasting a new version: Deploy > Manage deployments > edit > Version: New version > Deploy.
 *
 * Columns are found by header name, so they can be reordered in the sheet without breaking anything.
 */

const SHEET_NAME = 'Posts';
const FOLDER_NAME = 'Musk Post Tracker screenshots';

// Desired order. Missing columns are inserted after the one before them, so existing data never shifts rows.
const HEADERS = [
  'Date (CT)', 'Type', 'Link', 'Text', 'Responding to', 'Their post link',
  'Category', 'Flag', 'Flag reason', 'Claude description',
  'Archive link', 'Screenshot', 'Screenshot source', 'Status', 'Post ID',
  'Finding', 'Harm type', 'Conduct', 'Rationale', 'Reviewed at',
];

// Written only by the `review` action (the review app). Collector actions never touch these.
const REVIEW_FIELDS = { finding: 'Finding', harm_type: 'Harm type', conduct: 'Conduct',
                        rationale: 'Rationale', reviewed_at: 'Reviewed at' };
const FINDINGS = ['Approved', 'Rejected', ''];
const CONDUCTS = ['Original statement', 'Endorsed another post', 'Quoted with comment', 'Retweeted', ''];

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
    switch (body.action) {
      case 'latest_id': return json_({ ok: true, latest_id: latestId_() });
      case 'known_ids': return json_({ ok: true, ids: knownIds_() });
      case 'rows': return json_({ ok: true, rows: rows_() });
      case 'append': return json_({ ok: true, appended: append_(body.rows, body.wayback) });
      case 'update': return json_({ ok: true, updated: update_(body.rows) });
      case 'relabel': return json_({ ok: true, updated: relabel_(body.rows) });
      case 'set_status': return json_({ ok: true, updated: setStatus_(body.rows) });
      case 'review': return json_(review_(body));
      case 'check_archives': return json_(Object.assign({ ok: true }, checkArchives_(body.wayback)));
      default: return json_({ ok: false, error: 'unknown action' });
    }
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

function sheet_() {
  return SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
}

// {header: 1-based column}, read from the sheet itself.
function cols_() {
  const sh = sheet_();
  const row = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0];
  const c = {};
  row.forEach((h, i) => { if (h) c[String(h).trim()] = i + 1; });
  return c;
}

function ensureLayout_() {
  const sh = sheet_();
  let c = cols_();
  for (let i = 0; i < HEADERS.length; i++) {
    const h = HEADERS[i];
    if (c[h]) continue;
    const after = i === 0 ? 0 : c[HEADERS[i - 1]];
    if (after) sh.insertColumnAfter(after); else sh.insertColumnBefore(1);
    const col = after + 1;
    sh.getRange(1, col).setValue(h).setFontWeight('bold');
    c = cols_();
    if (h === 'Screenshot source') fillScreenshotSource_(c);
  }
}

// One-time fill for rows logged before the column existed: reconstructed cards carry a Drive description.
function fillScreenshotSource_(c) {
  const shots = dataRange_(c['Screenshot']);
  if (!shots) return;
  const out = shots.getValues().map(r => {
    const m = String(r[0]).match(/\/d\/([^/]+)/);
    if (!m) return [''];
    try {
      return [/reconstruct/i.test(DriveApp.getFileById(m[1]).getDescription() || '') ? 'Reconstructed' : 'X embed'];
    } catch (err) {
      return ['Unknown'];
    }
  });
  dataRange_(c['Screenshot source']).setValues(out);
}

function dataRange_(col, width) {
  const sh = sheet_();
  const n = sh.getLastRow() - 1;
  return n < 1 ? null : sh.getRange(2, col, n, width || 1);
}

function knownIds_() {
  const r = dataRange_(cols_()['Post ID']);
  return r ? r.getDisplayValues().map(x => String(x[0]).trim()).filter(Boolean) : [];
}

function rowOf_() {
  const m = {};
  knownIds_().forEach((id, i) => { m[id] = i + 2; });
  return m;
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
  const sh = sheet_();
  const c = cols_();
  const r = dataRange_(1, sh.getLastColumn());
  if (!r) return [];
  const get = (v, h) => (c[h] ? v[c[h] - 1] : '');
  return r.getDisplayValues().map(v => ({
    date_ct: get(v, 'Date (CT)'), type: get(v, 'Type'), link: get(v, 'Link'), text: get(v, 'Text'),
    context: get(v, 'Responding to'), status: get(v, 'Status'), post_id: String(get(v, 'Post ID')).trim(),
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

function shotSource_(r) {
  if (!r.screenshot_b64) return '';
  return r.screenshot_method === 'X embed' ? 'X embed' : 'Reconstructed';
}

// Keep tweet text from being read as a formula or number.
function safe_(s) {
  s = String(s == null ? '' : s);
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

function append_(rows, wayback) {
  const sh = sheet_();
  const c = cols_();
  const width = sh.getLastColumn();
  const known = new Set(knownIds_());
  const out = [];
  let submits = 0;
  for (const r of rows) {
    if (known.has(r.post_id)) continue;
    const row = new Array(width).fill('');
    const put = (h, v) => { row[c[h] - 1] = v; };
    put('Date (CT)', r.date_ct);
    put('Type', r.type);
    put('Link', r.link);
    put('Text', safe_(r.text));
    put('Responding to', safe_(r.context));
    put('Their post link', r.context_link || '');
    put('Category', r.category || 'UNCATEGORIZED');
    put('Flag', r.flag || '');
    put('Flag reason', safe_(r.flag_reason));
    put('Claude description', safe_(r.description));
    put('Archive link', (wayback && submits++ < MAX_SUBMITS_PER_RUN) ? waybackSubmit_(r.link, wayback) : 'not submitted');
    put('Screenshot', saveShot_(r));
    put('Screenshot source', shotSource_(r));
    put('Status', 'Live');
    put('Post ID', "'" + r.post_id);
    // Reviewer columns stay blank on new rows.
    out.push(row);
    known.add(r.post_id);
  }
  if (out.length) {
    const start = sh.getLastRow() + 1;
    sh.getRange(start, 1, out.length, width).setValues(out);
    sh.getRange(start, c['Text'], out.length, 2).setWrap(true);
  }
  return out.length;
}

// Collector refresh of existing rows. Only collector fields that are present get overwritten.
function update_(rows) {
  const sh = sheet_();
  const c = cols_();
  const rowOf = rowOf_();
  let updated = 0;
  for (const r of rows) {
    const n = rowOf[r.post_id];
    if (!n) continue;
    const set = (h, v) => sh.getRange(n, c[h]).setValue(v);
    set('Responding to', safe_(r.context));
    set('Their post link', r.context_link || '');
    if (r.category) set('Category', r.category);
    if (r.flag) { set('Flag', r.flag); set('Flag reason', safe_(r.flag_reason)); }
    if (r.description) set('Claude description', safe_(r.description));
    if (r.screenshot_b64) {
      const old = String(sh.getRange(n, c['Screenshot']).getValue());
      const m = old.match(/\/d\/([^/]+)/);
      set('Screenshot', saveShot_(r));
      set('Screenshot source', shotSource_(r));
      if (m) { try { DriveApp.getFileById(m[1]).setTrashed(true); } catch (err) { /* already gone */ } }
    }
    sh.getRange(n, c['Responding to']).setWrap(true);
    updated++;
  }
  return updated;
}

// Topic, flag and description only. Writes whole columns at once so thousands of rows stay fast.
function relabel_(rows) {
  const c = cols_();
  const rowOf = rowOf_();
  const fields = { category: 'Category', flag: 'Flag', flag_reason: 'Flag reason', description: 'Claude description' };
  const ranges = {}, values = {};
  for (const k in fields) { ranges[k] = dataRange_(c[fields[k]]); values[k] = ranges[k].getValues(); }
  let updated = 0;
  for (const r of rows) {
    const n = rowOf[r.post_id];
    if (!n) continue;
    for (const k in fields) if (r[k] != null) values[k][n - 2][0] = safe_(r[k]);
    updated++;
  }
  for (const k in fields) ranges[k].setValues(values[k]);
  return updated;
}

function setStatus_(rows) {
  const c = cols_();
  const rowOf = rowOf_();
  const range = dataRange_(c['Status']);
  const values = range.getValues();
  let updated = 0;
  for (const r of rows) {
    const n = rowOf[r.post_id];
    if (n) { values[n - 2][0] = r.status; updated++; }
  }
  range.setValues(values);
  return updated;
}

// The review app's only write path: one row's reviewer columns, nothing else.
function review_(body) {
  const n = rowOf_()[String(body.post_id || '').trim()];
  if (!n) return { ok: false, error: 'unknown post_id' };
  if (body.finding != null && FINDINGS.indexOf(body.finding) < 0) return { ok: false, error: 'finding must be Approved, Rejected or empty' };
  if (body.conduct != null && CONDUCTS.indexOf(body.conduct) < 0) return { ok: false, error: 'conduct must be one of ' + CONDUCTS.filter(Boolean).join(', ') + ' or empty' };
  const sh = sheet_();
  const c = cols_();
  const written = [];
  for (const k in REVIEW_FIELDS) {
    if (body[k] == null) continue; // omitted fields are left as they are
    sh.getRange(n, c[REVIEW_FIELDS[k]]).setValue(safe_(body[k]));
    written.push(REVIEW_FIELDS[k]);
  }
  return { ok: true, row: n, written: written };
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
  const c = cols_();
  const cellsRange = dataRange_(c['Archive link']);
  if (!cellsRange || !keys) return { resolved: 0, pending: 0 };
  const cells = cellsRange.getValues();
  const links = dataRange_(c['Link']).getValues();
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
