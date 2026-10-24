/**
 * Musk Post Tracker: sheet + Drive endpoint for the GitHub Action.
 * Paste into the sheet's Extensions > Apps Script, set the SECRET script property,
 * then Deploy > New deployment > Web app (Execute as: Me, Who has access: Anyone).
 */

const SHEET_NAME = 'Posts';
const FOLDER_NAME = 'Musk Post Tracker screenshots';
const ID_COL = 9; // column I, Post ID

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
    if (body.action === 'append') return json_({ ok: true, appended: append_(body.rows) });
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

function append_(rows) {
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
      r.archive, shot, "'" + r.post_id,
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

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
