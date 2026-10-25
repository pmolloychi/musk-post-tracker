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

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Musk Record review')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function config() {
  return { harmTypes: HARM_TYPES, conducts: CONDUCTS };
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
function list(view, query, sort, offset) {
  const posts = readRows_().map(toPost_);
  const counts = {};
  for (const k in VIEWS) counts[k] = posts.filter(VIEWS[k]).length;
  const q = String(query || '').trim().toLowerCase();
  let out = posts.filter(VIEWS[view] || VIEWS.flagged);
  if (q) {
    out = out.filter(p => [p.text, p.context, p.flag, p.flagReason, p.description, p.category, p.rationale]
      .some(s => String(s || '').toLowerCase().indexOf(q) >= 0));
  }
  // Dates are "YYYY-MM-DD HH:MM", so string order is time order; IDs break ties.
  const key = p => p.date + ' ' + ('0000000000000000000000' + p.id).slice(-22);
  out.sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
  if (sort !== 'oldest') out.reverse();
  const start = Math.max(0, Number(offset) || 0);
  return { counts: counts, total: out.length, offset: start, posts: out.slice(start, start + PAGE_SIZE) };
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
