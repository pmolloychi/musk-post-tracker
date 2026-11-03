/**
 * Musk Record review app: a private page for approving or rejecting posts in the Musk Post Tracker sheet.
 *
 * This is its own Apps Script project, separate from the script bound to the sheet:
 *   1. script.google.com > New project. Paste this file as Code.gs, and add an HTML file named Index with Index.html.
 *   2. Deploy > New deployment > Web app, Execute as: Me, Who has access: Only myself.
 *
 * It reads and writes the sheet directly. It only ever writes the five reviewer columns of one row,
 * which the collector never touches.
 */

const SHEET_ID = '13wqF9zWxmtZ73gkdRUmUOruUhOn8DRDs2PGOEci2J-I';
const SHEET_NAME = 'Posts';
const PAGE_SIZE = 50;

// Mirrors FLAGS in collector/categories.py and CONDUCTS in apps_script/Code.gs.
const HARM_TYPES = ['Antisemitism', 'Racism', 'Anti-immigrant/xenophobia', 'Islamophobia',
                    'Anti-trans', 'Homophobia', 'Misogyny', 'Ableism'];
const CONDUCTS = ['Original statement', 'Endorsed another post', 'Quoted with comment', 'Retweeted'];

// The random sample for the public site's upheld rate. Layout mirrors readSample_ in apps_script/Code.gs:
// B1:B5 hold drawn_at, seed, frame_size, frame_sha256, sample_sha256; row 7 is the header; posts start at row 8.
const SAMPLE_SHEET = 'Sample';
const SAMPLE_SIZE = 100;
const SAMPLE_FIRST_ROW = 8;

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Musk Record review')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function config() {
  const s = readSample_();
  return { harmTypes: HARM_TYPES, conducts: CONDUCTS,
           sample: { drawn: !!s, decided: s ? sampleState_(s).decided : 0, size: SAMPLE_SIZE } };
}

// Every row as {header: display value}, found by header name so column moves don't matter.
function readRows_() {
  const values = SpreadsheetApp.openById(SHEET_ID).getSheetByName(SHEET_NAME).getDataRange().getDisplayValues();
  const head = values.shift().map(h => String(h).trim());
  return values.map(v => {
    const o = {};
    head.forEach((h, i) => { if (h) o[h] = v[i]; });
    return o;
  }).filter(o => String(o['Post ID'] || '').trim());
}

function toPost_(o) {
  return {
    id: String(o['Post ID']).trim(), date: o['Date (CT)'], type: o['Type'], link: o['Link'], text: o['Text'],
    context: o['Responding to'], contextLink: o['Their post link'], category: o['Category'],
    flag: o['Flag'], flagReason: o['Flag reason'], description: o['Claude description'],
    archive: o['Archive link'], shot: o['Screenshot'], shotSource: o['Screenshot source'], status: o['Status'],
    finding: o['Finding'], harmType: o['Harm type'], conduct: o['Conduct'], rationale: o['Rationale'],
    reviewedAt: o['Reviewed at'],
  };
}

function isFlagged_(p) {
  const f = String(p.flag || '').trim();
  return f !== '' && f !== 'None';
}

const VIEWS = {
  flagged: p => isFlagged_(p) && !p.finding,
  unreviewed: p => !p.finding,
  approved: p => p.finding === 'Approved',
  rejected: p => p.finding === 'Rejected',
  all: () => true,
};

// One page of posts for a view, filtered by a search string and sorted by date.
// The sample view keeps sample order instead, and counts what's left to decide.
function list(view, query, sort, offset) {
  const posts = readRows_().map(toPost_);
  const counts = {};
  for (const k in VIEWS) counts[k] = posts.filter(VIEWS[k]).length;
  const s = readSample_();
  const state = s ? sampleState_(s, posts) : null;
  counts.sample = state ? state.posts.length - state.decided : '';
  const q = String(query || '').trim().toLowerCase();
  let out;
  if (view === 'sample') {
    out = state ? state.posts : [];
  } else {
    out = posts.filter(VIEWS[view] || VIEWS.flagged);
    // Dates are "YYYY-MM-DD HH:MM", so string order is time order; IDs break ties.
    const key = p => p.date + ' ' + ('0000000000000000000000' + p.id).slice(-22);
    out.sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
    if (sort !== 'oldest') out.reverse();
  }
  if (q) {
    out = out.filter(p => [p.text, p.context, p.flag, p.flagReason, p.description, p.category, p.rationale]
      .some(s => String(s || '').toLowerCase().indexOf(q) >= 0));
  }
  const start = Math.max(0, Number(offset) || 0);
  return { counts: counts, total: out.length, offset: start, posts: out.slice(start, start + PAGE_SIZE),
           sample: { drawn: !!s, decided: state ? state.decided : 0, size: SAMPLE_SIZE } };
}

// ---------- Random sample ----------

// Sheets may turn a typed 'yyyy-MM-dd HH:mm' into a Date, so normalize both kinds to the same CT string.
function ctMinute_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, 'America/Chicago', 'yyyy-MM-dd HH:mm');
  return String(v == null ? '' : v).trim();
}

// Same rule as isDecided_ in apps_script/Code.gs: only a decision made at or after the draw counts.
function isDecided_(finding, reviewedAt, drawnAt) {
  const at = ctMinute_(reviewedAt);
  return String(finding || '').trim() !== '' && at !== '' && at >= drawnAt;
}

function sampleSheet_() {
  return SpreadsheetApp.openById(SHEET_ID).getSheetByName(SAMPLE_SHEET);
}

// Null when the tab is missing or the sample hasn't been drawn.
function readSample_() {
  const sh = sampleSheet_();
  if (!sh || sh.getLastRow() < SAMPLE_FIRST_ROW) return null;
  const drawnAt = ctMinute_(sh.getRange(1, 2).getValue());
  if (!drawnAt) return null;
  const rows = sh.getRange(SAMPLE_FIRST_ROW, 1, SAMPLE_SIZE, 4).getValues()
    .map(r => ({ sampleNo: Number(r[0]), id: String(r[1]).trim(), priorFinding: String(r[3]).trim() }))
    .filter(x => x.id);
  return { drawnAt: drawnAt, rows: rows };
}

// Raw Reviewed at by Post ID. Display values depend on the cell's format, so read the values themselves.
function reviewedAtById_() {
  const sh = SpreadsheetApp.openById(SHEET_ID).getSheetByName(SHEET_NAME);
  const head = sh.getRange(1, 1, 1, sh.getLastColumn()).getDisplayValues()[0].map(h => String(h).trim());
  const idCol = head.indexOf('Post ID') + 1, atCol = head.indexOf('Reviewed at') + 1;
  const n = sh.getLastRow() - 1;
  const out = {};
  if (n < 1 || !idCol || !atCol) return out;
  const ids = sh.getRange(2, idCol, n, 1).getDisplayValues();
  const ats = sh.getRange(2, atCol, n, 1).getValues();
  ids.forEach((r, i) => { out[String(r[0]).trim()] = ctMinute_(ats[i][0]); });
  return out;
}

// The sample's posts in sample order. A post decided before the draw needs a fresh decision, so its old one is
// hidden here and the form starts from Claude's flag, as for a post nobody has reviewed.
function sampleState_(s, posts) {
  const byId = {};
  (posts || readRows_().map(toPost_)).forEach(p => { byId[p.id] = p; });
  const at = reviewedAtById_();
  let decided = 0;
  const out = s.rows.filter(x => byId[x.id]).map(x => {
    const p = Object.assign({}, byId[x.id]);
    p.sampleNo = x.sampleNo;
    p.sampleDecided = isDecided_(p.finding, at[p.id], s.drawnAt);
    if (p.sampleDecided) decided++;
    p.needsRereview = !p.sampleDecided && !!(x.priorFinding || p.finding);
    if (!p.sampleDecided) Object.assign(p, { finding: '', harmType: '', conduct: '', rationale: '', reviewedAt: '' });
    return p;
  });
  return { posts: out, decided: decided };
}

function sha256Hex_(text) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8)
    .map(b => ('0' + (b & 255).toString(16)).slice(-2)).join('');
}

// Draws the sample once. The frame is every post Claude has flagged right now, by sorted Post ID. Posts are
// ranked by SHA-256 of "seed:id" and the first 100 taken, so anyone with the seed and the frame can redo the draw.
function drawSample() {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    if (readSample_()) throw new Error('The sample has already been drawn. It can only be drawn once.');
    const ss = SpreadsheetApp.openById(SHEET_ID);
    const sh = ss.getSheetByName(SAMPLE_SHEET) || ss.insertSheet(SAMPLE_SHEET);
    if (sh.getLastRow() > 0) throw new Error('The Sample tab already has something in it. Empty it first.');
    const posts = readRows_().map(toPost_);
    const frame = posts.filter(isFlagged_).map(p => p.id).sort();
    if (frame.length < SAMPLE_SIZE) throw new Error('Only ' + frame.length + ' flagged posts; the sample needs ' + SAMPLE_SIZE + '.');
    const seed = Utilities.getUuid().replace(/-/g, '').toLowerCase();
    const ranked = frame.map(id => ({ id: id, h: sha256Hex_(seed + ':' + id) }))
      .sort((a, b) => (a.h < b.h ? -1 : a.h > b.h ? 1 : 0)).slice(0, SAMPLE_SIZE);
    const ids = ranked.map(x => x.id);
    const byId = {};
    posts.forEach(p => { byId[p.id] = p; });
    const at = reviewedAtById_();
    const drawnAt = Utilities.formatDate(new Date(), 'America/Chicago', 'yyyy-MM-dd HH:mm');
    const head = [
      ['drawn_at', drawnAt, '', '', ''],
      ['seed', seed, '', '', ''],
      ['frame_size', String(frame.length), '', '', ''],
      ['frame_sha256', sha256Hex_(frame.join('\n')), '', '', ''],
      ['sample_sha256', sha256Hex_(ids.join('\n') + '\nseed:' + seed + '\n'), '', '', ''],
      ['', '', '', '', ''],
      ['sample_no', 'post_id', 'flag_at_draw', 'prior_finding', 'prior_reviewed_at'],
    ];
    const rows = ids.map((id, i) => [String(i + 1), id, byId[id].flag || '', byId[id].finding || '', at[id] || '']);
    const all = head.concat(rows);
    // Plain text first, so IDs and times are stored exactly as written.
    sh.getRange(1, 1, all.length, 5).setNumberFormat('@').setValues(all);
    return { drawnAt: drawnAt, frameSize: frame.length };
  } finally {
    lock.releaseLock();
  }
}

// Writes one post's reviewer columns, found by Post ID and header name. Returns the saved fields.
function save(postId, fields) {
  const finding = fields.finding || '';
  if (['Approved', 'Rejected', ''].indexOf(finding) < 0) throw new Error('Finding must be Approved, Rejected or empty.');
  const harm = (fields.harm_type || []).filter(h => HARM_TYPES.indexOf(h) >= 0);
  const conduct = fields.conduct || '';
  if (conduct && CONDUCTS.indexOf(conduct) < 0) throw new Error('Unknown conduct: ' + conduct);
  const rationale = String(fields.rationale || '').trim();
  if (finding === 'Approved' && (!harm.length || !conduct || !rationale)) {
    throw new Error('Approving needs a harm type, what he did, and a rationale.');
  }
  const reviewedAt = fields.reviewed_at != null ? fields.reviewed_at
    : (finding ? Utilities.formatDate(new Date(), 'America/Chicago', 'yyyy-MM-dd HH:mm') : '');
  const saved = { finding: finding, harm_type: harm.join('; '), conduct: conduct, rationale: rationale,
                  reviewed_at: reviewedAt };
  writeReview_(String(postId).trim(), saved);
  return saved;
}

const REVIEW_COLUMNS = { finding: 'Finding', harm_type: 'Harm type', conduct: 'Conduct',
                         rationale: 'Rationale', reviewed_at: 'Reviewed at' };

function writeReview_(postId, saved) {
  const sh = SpreadsheetApp.openById(SHEET_ID).getSheetByName(SHEET_NAME);
  const head = sh.getRange(1, 1, 1, sh.getLastColumn()).getDisplayValues()[0].map(h => String(h).trim());
  const col = h => {
    const i = head.indexOf(h);
    if (i < 0) throw new Error('The sheet has no "' + h + '" column.');
    return i + 1;
  };
  const n = sh.getLastRow() - 1;
  const ids = n > 0 ? sh.getRange(2, col('Post ID'), n, 1).getDisplayValues() : [];
  const i = ids.findIndex(r => String(r[0]).trim() === postId);
  if (i < 0) throw new Error('Post ' + postId + ' is no longer in the sheet.');
  for (const k in REVIEW_COLUMNS) sh.getRange(i + 2, col(REVIEW_COLUMNS[k])).setValue(safe_(saved[k]));
}

// Keep text from being read as a formula, as the sheet script does.
function safe_(s) {
  s = String(s == null ? '' : s);
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

// The screenshot as a data URL, read from Drive, so it shows without sharing the file.
function screenshot(driveUrl) {
  const m = String(driveUrl || '').match(/\/d\/([^/?#]+)/) || String(driveUrl || '').match(/[?&]id=([^&#]+)/);
  if (!m) return null;
  const blob = DriveApp.getFileById(m[1]).getBlob();
  return 'data:' + blob.getContentType() + ';base64,' + Utilities.base64Encode(blob.getBytes());
}
