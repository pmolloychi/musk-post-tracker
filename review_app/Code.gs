/**
 * Musk Record review app: a private page for approving or rejecting posts in the Musk Post Tracker sheet.
 *
 * This is its own Apps Script project, separate from the script bound to the sheet:
 *   1. script.google.com > New project. Paste this file as Code.gs, and add an HTML file named Index with Index.html.
 *   2. Project Settings > Script properties: SHEET_ENDPOINT (the sheet's web app URL) and SHEET_SECRET
 *      (the same SECRET the sheet script uses; it's also the SHEET_SECRET repo secret).
 *   3. Deploy > New deployment > Web app, Execute as: Me, Who has access: Only myself.
 *
 * It reads the sheet directly and sends every write through the sheet script's `review` action,
 * so the reviewer columns keep a single write path.
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

// Writes one post's reviewer columns through the sheet's `review` action. Returns the saved fields.
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
  callSheet_(Object.assign({ action: 'review', post_id: String(postId) }, saved));
  return saved;
}

function callSheet_(payload) {
  const props = PropertiesService.getScriptProperties();
  const url = props.getProperty('SHEET_ENDPOINT');
  if (!url || !props.getProperty('SHEET_SECRET')) throw new Error('Set SHEET_ENDPOINT and SHEET_SECRET in Project Settings > Script properties.');
  const res = UrlFetchApp.fetch(url, {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    payload: JSON.stringify(Object.assign({ secret: props.getProperty('SHEET_SECRET') }, payload)),
  });
  let data;
  try {
    data = JSON.parse(res.getContentText());
  } catch (err) {
    throw new Error('The sheet endpoint answered ' + res.getResponseCode() + ' without JSON. Check SHEET_ENDPOINT.');
  }
  if (!data.ok) throw new Error('The sheet endpoint refused the save: ' + data.error);
  return data;
}

// The screenshot as a data URL, read from Drive, so it shows without sharing the file.
function screenshot(driveUrl) {
  const m = String(driveUrl || '').match(/\/d\/([^/?#]+)/) || String(driveUrl || '').match(/[?&]id=([^&#]+)/);
  if (!m) return null;
  const blob = DriveApp.getFileById(m[1]).getBlob();
  return 'data:' + blob.getContentType() + ';base64,' + Utilities.base64Encode(blob.getBytes());
}
