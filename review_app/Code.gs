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
           sample: Object.assign(sampleInfo_(s), { decided: s ? sampleState_(s).decided : 0 }) };
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
           sample: Object.assign(sampleInfo_(s), { decided: state ? state.decided : 0 }) };
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

// The draw has two steps, so its random number comes from a public source nobody controls:
//   1. freezeFrame() fixes the frame (every post Claude has flagged right now) and records its size and SHA-256.
//   2. Those two values, plus a future minute, are committed publicly in sample/LOCK.md on GitHub.
//   3. Once that minute has passed, drawSample() reads LOCK.md, checks it was committed before that minute and
//      matches the frozen frame, fetches the NIST Randomness Beacon pulse for that minute, and uses its output
//      as the seed. Posts are ranked by SHA-256 of "seed:id" and the first 100 taken, so anyone with the seed and
//      the frame can redo the draw.
// The frame's IDs are kept in column G of the Sample tab, from row 8 down, so the draw uses exactly that frame.
const FRAME_COL = 7;
const LOCK_RAW_URL = 'https://raw.githubusercontent.com/pmolloychi/musk-post-tracker/main/sample/LOCK.md';
const LOCK_COMMITS_URL = 'https://api.github.com/repos/pmolloychi/musk-post-tracker/commits?path=sample/LOCK.md&per_page=100';
const BEACON_URL = 'https://beacon.nist.gov/beacon/2.0/pulse/time/';

// The frozen frame, or null. Present between freezeFrame() and drawSample(), and kept after the draw.
function readFrozen_() {
  const sh = sampleSheet_();
  if (!sh || String(sh.getRange(1, 4).getValue()).trim() !== 'frozen_at') return null;
  const n = Number(sh.getRange(3, 2).getValue());
  const ids = n ? sh.getRange(SAMPLE_FIRST_ROW, FRAME_COL, n, 1).getDisplayValues().map(r => String(r[0]).trim()) : [];
  return { frozenAt: ctMinute_(sh.getRange(1, 5).getValue()), frameSize: n,
           frameSha: String(sh.getRange(4, 2).getValue()).trim(), ids: ids };
}

function sampleInfo_(s) {
  const f = readFrozen_();
  return { drawn: !!s, size: SAMPLE_SIZE,
           frozen: f ? { frozenAt: f.frozenAt, frameSize: f.frameSize, frameSha: f.frameSha } : null };
}

// Step 1. An earlier draw (made without a public lock) is kept, renamed, so nothing is lost.
function freezeFrame() {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    if (readSample_() && readFrozen_()) throw new Error('The sample has already been drawn.');
    if (readFrozen_()) throw new Error('The frame is already frozen. Publish the lock, then draw.');
    const ss = SpreadsheetApp.openById(SHEET_ID);
    const old = ss.getSheetByName(SAMPLE_SHEET);
    if (old) {
      const drawnAt = ctMinute_(old.getRange(1, 2).getValue()) || 'undated';
      old.setName('Sample (superseded ' + drawnAt.slice(0, 10) + ')');
    }
    const sh = ss.insertSheet(SAMPLE_SHEET);
    const frame = readRows_().map(toPost_).filter(isFlagged_).map(p => p.id).sort();
    if (frame.length < SAMPLE_SIZE) throw new Error('Only ' + frame.length + ' flagged posts; the sample needs ' + SAMPLE_SIZE + '.');
    const frozenAt = Utilities.formatDate(new Date(), 'America/Chicago', 'yyyy-MM-dd HH:mm');
    const frameSha = sha256Hex_(frame.join('\n'));
    const head = [
      ['drawn_at', '', '', 'frozen_at', frozenAt],
      ['seed', '', '', 'beacon_pulse_utc', ''],
      ['frame_size', String(frame.length), '', 'beacon_pulse_url', ''],
      ['frame_sha256', frameSha, '', '', ''],
      ['sample_sha256', '', '', '', ''],
      ['', '', '', '', ''],
      ['sample_no', 'post_id', 'flag_at_draw', 'prior_finding', 'prior_reviewed_at'],
    ];
    sh.getRange(1, 1, head.length, 5).setNumberFormat('@').setValues(head);
    sh.getRange(SAMPLE_FIRST_ROW - 1, FRAME_COL).setValue('frame_post_id');
    sh.getRange(SAMPLE_FIRST_ROW, FRAME_COL, frame.length, 1).setNumberFormat('@').setValues(frame.map(id => [id]));
    return { frozenAt: frozenAt, frameSize: frame.length, frameSha: frameSha };
  } finally {
    lock.releaseLock();
  }
}

function fetchText_(url) {
  const r = UrlFetchApp.fetch(url, { muteHttpExceptions: true, headers: { 'Cache-Control': 'no-cache' } });
  if (r.getResponseCode() !== 200) throw new Error('Could not read ' + url + ' (HTTP ' + r.getResponseCode() + ').');
  return r.getContentText();
}

// "- beacon_pulse_utc: 2026-11-04T20:00:00Z" style lines in LOCK.md.
function lockValue_(text, key) {
  const m = text.match(new RegExp('^\\s*[-*]?\\s*`?' + key + '`?\\s*:\\s*`?([^`\\s]+)`?\\s*$', 'm'));
  return m ? m[1].trim() : '';
}

// Step 3.
function drawSample() {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    if (readSample_()) throw new Error('The sample has already been drawn. It can only be drawn once.');
    const f = readFrozen_();
    if (!f) throw new Error('Freeze the frame first.');
    if (f.ids.length !== f.frameSize || sha256Hex_(f.ids.join('\n')) !== f.frameSha) {
      throw new Error('The frozen frame in the Sample tab has been changed. Don\'t edit that tab by hand.');
    }
    const lockText = fetchText_(LOCK_RAW_URL + '?t=' + Date.now());
    if (lockValue_(lockText, 'frame_sha256') !== f.frameSha || Number(lockValue_(lockText, 'frame_size')) !== f.frameSize) {
      throw new Error('sample/LOCK.md on GitHub doesn\'t match the frozen frame. Check the values in it.');
    }
    const pulseIso = lockValue_(lockText, 'beacon_pulse_utc');
    const pulseMs = Date.parse(pulseIso);
    if (!pulseIso || isNaN(pulseMs) || pulseMs % 60000 !== 0) throw new Error('LOCK.md has no valid beacon_pulse_utc minute.');
    if (Date.now() < pulseMs + 60000) {
      throw new Error('Too early. The draw opens one minute after ' + pulseIso + ' (' +
        Utilities.formatDate(new Date(pulseMs), 'America/Chicago', 'MMM d, h:mm a') + ' CT).');
    }
    // Every commit that touched LOCK.md must predate the pulse, so its values were public before the seed existed.
    // GitHub limits unsigned API calls by IP, and Google's IPs are shared, so a refused call is noted, not fatal:
    // the Wayback capture and GitHub's own history still show when LOCK.md went up.
    let commitCheck = 'skipped: GitHub API unavailable';
    const cr = UrlFetchApp.fetch(LOCK_COMMITS_URL, { muteHttpExceptions: true });
    if (cr.getResponseCode() === 200) {
      const commits = JSON.parse(cr.getContentText());
      if (!commits.length) throw new Error('GitHub shows no commit of sample/LOCK.md.');
      if (commits.some(c => Date.parse(c.commit.committer.date) >= pulseMs)) {
        throw new Error('sample/LOCK.md was changed at or after the pulse time, so it can\'t be used.');
      }
      commitCheck = 'passed';
    }
    const pulse = JSON.parse(fetchText_(BEACON_URL + pulseMs)).pulse;
    if (!pulse || Date.parse(pulse.timeStamp) !== pulseMs) throw new Error('The beacon has no pulse for exactly ' + pulseIso + '.');
    const seed = String(pulse.outputValue).toLowerCase();
    const ranked = f.ids.map(id => ({ id: id, h: sha256Hex_(seed + ':' + id) }))
      .sort((a, b) => (a.h < b.h ? -1 : a.h > b.h ? 1 : 0)).slice(0, SAMPLE_SIZE);
    const ids = ranked.map(x => x.id);
    const byId = {};
    readRows_().map(toPost_).forEach(p => { byId[p.id] = p; });
    const at = reviewedAtById_();
    const drawnAt = Utilities.formatDate(new Date(), 'America/Chicago', 'yyyy-MM-dd HH:mm');
    const sh = sampleSheet_();
    sh.getRange(1, 2, 2, 1).setNumberFormat('@').setValues([[drawnAt], [seed]]);
    sh.getRange(5, 2).setNumberFormat('@').setValue(sha256Hex_(ids.join('\n') + '\nseed:' + seed + '\n'));
    sh.getRange(2, 5, 2, 1).setNumberFormat('@').setValues([[pulseIso], [pulse.uri || (BEACON_URL + pulseMs)]]);
    sh.getRange(4, 4, 1, 2).setNumberFormat('@').setValues([['lock_commit_check', commitCheck]]);
    const rows = ids.map((id, i) => [String(i + 1), id, (byId[id] && byId[id].flag) || '',
                                     (byId[id] && byId[id].finding) || '', at[id] || '']);
    sh.getRange(SAMPLE_FIRST_ROW, 1, rows.length, 5).setNumberFormat('@').setValues(rows);
    return { drawnAt: drawnAt, frameSize: f.frameSize };
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
