// JM Download Manager — browser side (Chrome, Edge, Firefox).
//  * catches downloads the browser starts and hands them to the app
//  * watches what media each tab plays (MP4/WebM, HLS .m3u8, DASH .mpd)
//    so the "Download video" button can offer it
//  * starts the app through native messaging when it isn't running

const APP = "http://127.0.0.1:8721";
const HOST = "com.jmbuilds.jmdm";
const HDR = { "X-JMDM": "1" };
// Sites yt-dlp understands: send the page itself, it finds the best quality.
const VIDEO_SITES = ["youtube.com", "youtu.be", "youtube-nocookie.com", "facebook.com",
  "fb.watch", "tiktok.com", "instagram.com", "twitter.com", "x.com", "vimeo.com",
  "dailymotion.com", "twitch.tv", "reddit.com", "bilibili.com", "soundcloud.com"];
// Pieces of streams, never useful on their own.
const CHUNK_RE = /googlevideo\.com\/videoplayback|\.ts(\?|$)|\.m4s(\?|$)|\.aac(\?|$)|[?&](range|bytestart|byterange)=|\/seg-?\d+|\/chunk-?\d+|\/frag/i;

const api = globalThis.browser ?? chrome;

function host(url) { try { return new URL(url).hostname.toLowerCase(); } catch (e) { return ""; } }
function isVideoSite(url) {
  const h = host(url);
  return VIDEO_SITES.some(d => h === d || h.endsWith("." + d));
}

// ---------------------------------------------------------------------------
// talking to the app
// ---------------------------------------------------------------------------
let aliveAt = 0;               // last time the app answered
async function appAlive(ms = 800) {
  if (Date.now() - aliveAt < 3000) return true;
  try {
    const c = new AbortController();
    const t = setTimeout(() => c.abort(), ms);
    const r = await fetch(APP + "/ping", { headers: HDR, signal: c.signal });
    clearTimeout(t);
    if (r.ok) { aliveAt = Date.now(); syncRules(); }
    return r.ok;
  } catch (e) { return false; }
}

// Ask the native host to start the app, then wait for it to answer.
async function launchApp() {
  const { autostart = true } = await api.storage.local.get("autostart");
  if (!autostart) return false;
  try {
    const r = await api.runtime.sendNativeMessage(HOST, { action: "launch" });
    if (!r || !r.ok) return false;
  } catch (e) { return false; }           // host not registered (app never opened yet)
  for (let i = 0; i < 30; i++) {
    if (await appAlive(500)) return true;
    await new Promise(r => setTimeout(r, 500));
  }
  return false;
}

async function ensureApp() {
  return (await appAlive()) || (await launchApp());
}

async function cookieHeader(url) {
  try {
    const cs = await api.cookies.getAll({ url });
    return cs.map(c => c.name + "=" + c.value).join("; ");
  } catch (e) { return ""; }
}

function flash(text, color) {
  api.action.setBadgeBackgroundColor({ color });
  api.action.setBadgeText({ text });
  setTimeout(() => api.action.setBadgeText({ text: "" }), 3000);
}

async function sendToApp(job, { launch = true } = {}) {
  if (!(launch ? await ensureApp() : await appAlive())) {
    flash("off", "#c62828");
    return false;
  }
  job.cookie = job.cookie ?? await cookieHeader(job.url);
  job.userAgent = navigator.userAgent;
  try {
    const r = await fetch(APP + "/add", {
      method: "POST",
      headers: { ...HDR, "Content-Type": "application/json" },
      body: JSON.stringify(job)
    });
    if (!r.ok) throw new Error(r.status);
    flash("✓", "#2e7d32");
    return true;
  } catch (e) {
    flash("off", "#c62828");
    return false;
  }
}

// ---------------------------------------------------------------------------
// 1) catch normal browser downloads
// ---------------------------------------------------------------------------
// Download buttons are often forms that send hidden data (file id, token) to a
// script like gate.php. That link alone gives "404", so remember what each
// form sent and pass it to the app with the download.
const posts = new Map();     // url -> { body, ts }

function formBody(rb) {
  if (!rb) return "";
  if (rb.formData) {
    const p = new URLSearchParams();
    for (const [k, vals] of Object.entries(rb.formData)) for (const v of vals) p.append(k, v);
    return p.toString();
  }
  if (rb.raw && rb.raw.length) {
    try {
      return rb.raw.map(r => r.bytes ? new TextDecoder().decode(r.bytes) : "").join("");
    } catch (e) { return ""; }
  }
  return "";
}

api.webRequest.onBeforeRequest.addListener((d) => {
  if (d.method !== "POST") return;
  const body = formBody(d.requestBody);
  if (!body || body.length > 100000) return;
  const now = Date.now();
  for (const [u, p] of posts) if (now - p.ts > 120000) posts.delete(u);
  posts.set(d.url.split("#")[0], { body, ts: now });
}, { urls: ["<all_urls>"], types: ["main_frame", "sub_frame", "other", "xmlhttprequest"] },
   ["requestBody"]);

// ---------------------------------------------------------------------------
// which downloads to catch (IDM-style rules, shared with the app's Settings)
// ---------------------------------------------------------------------------
const RULE_DEFAULTS = {
  browser_capture: true,
  capture_types: "zip rar 7z tar gz bz2 xz zst cab iso img bin cue wim esd vhd vhdx " +
    "exe msi msix appx apk xapk apks obb jar dmg deb rpm " +
    "mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp ts " +
    "mp3 m4a aac flac wav ogg opus wma " +
    "pdf epub mobi azw3 cbz cbr doc docx xls xlsx ppt pptx odt ods odp rtf psd",
  capture_min_kb: 0,
  capture_exclude: "",
  // every file type is caught from these (Google Drive and Docs)
  capture_always: "drive.google.com docs.google.com usercontent.google.com",
  capture_updated: 0
};
// The list shipped before 1.8.1: a stored copy of it means "the default".
const OLD_TYPES = "zip rar 7z tar gz bz2 xz zst iso img bin cue exe msi msix appx apk xapk " +
  "apks obb jar dmg deb rpm mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp mp3 m4a aac " +
  "flac wav ogg opus wma";

async function getRules() {
  const v = await api.storage.local.get([...Object.keys(RULE_DEFAULTS), "capture"]);
  const r = { ...RULE_DEFAULTS };
  for (const k of Object.keys(RULE_DEFAULTS)) if (v[k] !== undefined) r[k] = v[k];
  if (v.browser_capture === undefined && v.capture === false) r.browser_capture = false; // old switch
  if (words(r.capture_types).join(" ") === OLD_TYPES) r.capture_types = RULE_DEFAULTS.capture_types;
  return r;
}

// Whichever side changed the rules last wins.
let syncedAt = 0;
async function syncRules(force = false) {
  if (!force && Date.now() - syncedAt < 30000) return;
  syncedAt = Date.now();
  try {
    const local = await getRules();
    let r = await fetch(APP + "/settings", { headers: HDR });
    if (!r.ok) return;                               // older app without rules
    const app = await r.json();
    if ((app.capture_updated || 0) > (local.capture_updated || 0)) {
      const copy = {};
      for (const k of Object.keys(RULE_DEFAULTS)) if (app[k] !== undefined) copy[k] = app[k];
      await api.storage.local.set(copy);
    } else if ((local.capture_updated || 0) > (app.capture_updated || 0)) {
      await fetch(APP + "/settings", { method: "POST",
        headers: { ...HDR, "Content-Type": "application/json" }, body: JSON.stringify(local) });
    }
  } catch (e) { /* app closed: the local copy is used */ }
}

function words(text) {
  return String(text || "").toLowerCase().split(/[\s,;]+/).filter(Boolean);
}
function extOf(name) {
  const m = /\.([a-z0-9]{1,8})$/i.exec(name || "");
  return m ? m[1].toLowerCase() : "";
}
function siteList(text) {
  return words(text).map(x => x.replace(/^[a-z]+:\/\//, "").replace(/\/.*$/, "").replace(/^\*?\./, ""));
}
function onSite(url, sites) {
  const h = host(url);
  return !!h && sites.some(d => h === d || h.endsWith("." + d));
}
// game.7z.001, game.r00, game.z01, game.part3 -> pieces of a split archive
function isSplitPart(ext) {
  return /^(\d{3}|r\d{2}|z\d{2}|part\d+)$/.test(ext);
}
// When neither the file name nor the link says what the file is (download.php?id=…).
const MIME_EXT = {
  "application/zip": "zip", "application/x-zip-compressed": "zip", "application/x-rar-compressed": "rar",
  "application/vnd.rar": "rar", "application/x-7z-compressed": "7z", "application/gzip": "gz",
  "application/x-tar": "tar", "application/x-iso9660-image": "iso",
  "application/x-msdownload": "exe", "application/x-msdos-program": "exe",
  "application/x-dosexec": "exe", "application/x-msi": "msi", "application/x-ms-installer": "msi",
  "application/vnd.android.package-archive": "apk", "application/java-archive": "jar",
  "application/pdf": "pdf", "application/epub+zip": "epub", "application/msword": "doc",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
  "application/vnd.ms-excel": "xls",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
  "application/vnd.ms-powerpoint": "ppt",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
  "video/mp4": "mp4", "video/x-matroska": "mkv", "video/webm": "webm", "video/quicktime": "mov",
  "video/x-msvideo": "avi", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/flac": "flac",
  "audio/wav": "wav", "audio/ogg": "ogg"
};
const UNKNOWN_MIME = ["", "application/octet-stream", "binary/octet-stream",
                      "application/force-download", "application/download", "application/x-download"];

const SCRIPT_EXT = ["php", "php3", "php5", "asp", "aspx", "jsp", "cgi", "pl", "py", "do",
                    "action", "html", "htm", "shtml"];

// Hold Alt while clicking a link: that one download stays in the browser.
let bypassUntil = 0;

// What the download is, for the rules and the recent list.
function describeItem(item, url) {
  const name = (item.filename || "").split(/[\\/]/).pop();
  let ext = extOf(name);
  if (!ext) { try { ext = extOf(new URL(url).pathname); } catch (e) {} }
  // download.php?id=7, get.aspx … : that's the site's script, not the file's type
  if (SCRIPT_EXT.includes(ext)) ext = "";
  return { name, ext, mime: (item.mime || "").toLowerCase().split(";")[0].trim() };
}

// Why a download should stay in the browser, or "" when the app should take it.
async function skipReason(item, url) {
  const rules = await getRules();
  if (!rules.browser_capture) return "catching is off";
  if (!/^https?:/i.test(url)) return "made by the page itself (blob link), can't be re-downloaded";
  if (item.byExtensionId) return "started by an extension";
  if (Date.now() < bypassUntil) { bypassUntil = 0; return "Alt was held"; }
  const from = [url, item.url, item.finalUrl, item.referrer].filter(Boolean);
  if (from.some(u => onSite(u, siteList(rules.capture_exclude)))) return "site is on the never-catch list";
  const always = from.some(u => onSite(u, siteList(rules.capture_always)));
  const types = words(rules.capture_types).map(x => x.replace(/^\*?\./, ""));
  if (types.length && !always) {
    let { ext, mime } = describeItem(item, url);
    if (!ext && MIME_EXT[mime]) ext = MIME_EXT[mime];
    const archives = ["zip", "rar", "7z"].some(a => types.includes(a));
    if (ext && isSplitPart(ext)) {
      if (!archives) return "split archive parts aren't in the list";
    } else if (ext) {
      if (!types.includes(ext)) return `.${ext} isn't in the file type list`;
    } else if (!UNKNOWN_MIME.includes(mime) && !/^(video|audio)\//.test(mime)) {
      return `no file name, and the type (${mime}) isn't one to catch`;
    }
    // no name and an unknown type (download.php?id=…): a real file, so take it
  }
  const size = item.totalBytes > 0 ? item.totalBytes : (item.fileSize > 0 ? item.fileSize : 0);
  const min = Number(rules.capture_min_kb) || 0;
  if (min > 0 && size > 0 && size < min * 1024) return "smaller than the minimum size";
  return "";
}

// ---- recent downloads: what happened to each, shown in the popup ------------
const RECENT_MAX = 30;
async function remember(item, url, result, reason) {
  try {
    const { recent = [] } = await api.storage.local.get("recent");
    const { name } = describeItem(item, url);
    recent.unshift({ ts: Date.now(), name: name || url.split("?")[0].split("/").pop() || url,
                     url, referer: item.referrer || "", result, reason: reason || "",
                     size: item.totalBytes > 0 ? item.totalBytes : 0 });
    await api.storage.local.set({ recent: recent.slice(0, RECENT_MAX) });
  } catch (e) { /* the list is only a help */ }
}

// Ask the app to take a download, and wait until it has really reached the
// file — and, with "Ask before starting" on, until the Add dialog is answered.
// Only then does the browser drop its own copy.
async function checkWithApp(job) {
  job.cookie = job.cookie ?? await cookieHeader(job.url);
  job.userAgent = navigator.userAgent;
  job.verify = true;
  job.wait = true;
  // A service worker that looks idle for 30 s gets stopped; touching an
  // extension API keeps it awake while the dialog is open.
  const keep = setInterval(() => api.runtime.getPlatformInfo().catch(() => {}), 20000);
  try {
    const c = new AbortController();
    const t = setTimeout(() => c.abort(), 270000);   // the app gives up after 240 s
    const r = await fetch(APP + "/add", {
      method: "POST", signal: c.signal,
      headers: { ...HDR, "Content-Type": "application/json" },
      body: JSON.stringify(job)
    });
    clearTimeout(t);
    if (!r.ok) return { ok: false, error: `the app answered ${r.status}` };
    return await r.json();
  } catch (e) { return { ok: false, error: "no answer from the app" }; }
  finally { clearInterval(keep); }
}

// Offer a held download to the app: { taken, result, reason }.
async function offerToApp(item) {
  const url = item.finalUrl || item.url;
  const skip = await skipReason(item, url);
  if (skip) return { taken: false, result: "skipped", reason: skip };
  // If the app is closed, start it; if that doesn't work, the browser keeps it.
  if (!(await appAlive()) && !(await launchApp()))
    return { taken: false, result: "browser", reason: "the app isn't running and couldn't be started" };
  const form = posts.get(url.split("#")[0]) || posts.get((item.url || "").split("#")[0]);
  const filename = (item.filename || "").split(/[\\/]/).pop();
  const res = await checkWithApp({ url, referer: item.referrer || "", filename, video: false,
                                   post: form ? form.body : "" });
  if (res && res.ok) return { taken: true, result: "taken", reason: "" };
  const why = (res && res.error) || "the app couldn't open the link";
  return { taken: false, result: why === "cancelled in the app" ? "cancelled" : "browser",
           reason: why };
}

async function dropBrowserCopy(id) {
  try { await api.downloads.cancel(id); } catch (e) { /* already gone */ }
  try { await api.downloads.erase({ id }); } catch (e) {}
}

if (api.downloads.onDeterminingFilename) {
  // Chrome, Edge, Brave: the browser asks for a file name before it saves
  // anything or shows "Save as". Holding the answer holds the download, so the
  // app takes it before a file lands in your Downloads folder.
  api.downloads.onDeterminingFilename.addListener((item, suggest) => {
    const url = item.finalUrl || item.url;
    offerToApp(item).then(async (o) => {
      remember(item, url, o.result, o.reason);
      if (o.taken) {
        await dropBrowserCopy(item.id);
        flash("✓", "#2e7d32");
      } else {
        suggest();                                   // carry on as a normal download
        if (o.result !== "skipped") flash("↓", "#f9a825");
      }
    }).catch((e) => { remember(item, url, "browser", String(e)); suggest(); });
    return true;                                     // we answer later
  });
} else {
  // Firefox has no such hook: pause the download as soon as it starts instead.
  api.downloads.onCreated.addListener(async (item) => {
    const url = item.finalUrl || item.url;
    const skip = await skipReason(item, url);
    if (skip) { remember(item, url, "skipped", skip); return; }
    let paused = false;
    try { await api.downloads.pause(item.id); paused = true; } catch (e) { /* already done */ }
    const o = await offerToApp(item);
    remember(item, url, o.result, o.reason);
    if (o.taken) {
      await dropBrowserCopy(item.id);
      flash("✓", "#2e7d32");
    } else {
      // The app couldn't get it or you cancelled: the browser keeps it.
      if (paused) { try { await api.downloads.resume(item.id); } catch (e) {} }
      flash("↓", "#f9a825");
    }
  });
}

// ---------------------------------------------------------------------------
// 2) remember the media each tab loads
// ---------------------------------------------------------------------------
const media = new Map();     // tabId -> Map(url -> item)

function mediaFor(tabId) {
  if (!media.has(tabId)) media.set(tabId, new Map());
  return media.get(tabId);
}

api.webRequest.onHeadersReceived.addListener((d) => {
  if (d.tabId < 0 || d.statusCode >= 400) return;
  const h = {};
  for (const x of d.responseHeaders || []) h[x.name.toLowerCase()] = x.value || "";
  const ct = (h["content-type"] || "").toLowerCase().split(";")[0].trim();
  const url = d.url;
  let kind = null;
  if (ct.includes("mpegurl") || /\.m3u8(\?|$)/i.test(url)) kind = "HLS";
  else if (ct === "application/dash+xml" || /\.mpd(\?|$)/i.test(url)) kind = "DASH";
  else if (/^(video|audio)\//.test(ct) && !/mp2t/.test(ct)) kind = ct.startsWith("audio") ? "Audio" : "Video";
  if (!kind || CHUNK_RE.test(url)) return;
  const total = parseInt((h["content-range"] || "").split("/")[1] || h["content-length"] || "0", 10) || 0;
  if ((kind === "Video" || kind === "Audio") && total && total < 512 * 1024) return;  // ads, previews
  const list = mediaFor(d.tabId);
  const key = url.split("#")[0];
  // HLS: keep the master playlist; per-quality playlists repeat it
  if (kind === "HLS" && [...list.values()].some(m => m.kind === "HLS" && host(m.url) === host(url))
      && /chunklist|index_\d|_\d{3,4}p|\/\d{3,4}\//i.test(url)) return;
  if (!list.has(key)) {
    const stream = kind === "HLS" || kind === "DASH";
    if (stream) {
      // a new stream in the same player = the player moved on (next episode):
      // the old one's stream is no longer what's playing
      for (const [k, m] of list) {
        if ((m.kind === "HLS" || m.kind === "DASH") && m.frameId === d.frameId) list.delete(k);
      }
    }
    list.set(key, { url, kind, size: total, ext: (ct.split("/")[1] || "").replace("x-", ""),
                    referer: d.initiator || d.documentUrl || "", ts: Date.now(),
                    frameId: d.frameId });
    if (list.size > 30) list.delete(list.keys().next().value);
    api.tabs.sendMessage(d.tabId, { type: "media-found", count: list.size }).catch(() => {});
  }
}, { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"] }, ["responseHeaders"]);

api.tabs.onUpdated.addListener((tabId, info) => {
  if (info.url) media.delete(tabId);              // new page: forget the old one's media
});
// Episode sites often swap only the player frame for the next episode (the
// tab's address stays the same): forget what that frame played before.
if (api.webNavigation) {
  api.webNavigation.onCommitted.addListener((d) => {
    if (d.frameId === 0) { media.delete(d.tabId); return; }
    const list = media.get(d.tabId);
    if (!list) return;
    for (const [k, m] of list) if (m.frameId === d.frameId) list.delete(k);
  });
}
api.tabs.onRemoved.addListener((tabId) => media.delete(tabId));

// Pick what the "Download video" button should download.
function choicesFor(tab, frameUrl, src, videoUrl) {
  const out = [];
  const page = tab && tab.url;
  if (videoUrl && isVideoSite(videoUrl)) {
    // the player's own video inside a feed (Facebook): not the feed's address
    out.push({ label: "This video (best quality)", url: videoUrl, video: true });
    return out;
  }
  if (frameUrl && isVideoSite(frameUrl) && frameUrl !== page) {
    out.push({ label: "Embedded video (best quality)", url: frameUrl, video: true });
  }
  if (page && /^https:\/\/(drive|docs)\.google\.com\/(file\/(u\/\d+\/)?d\/|open\?|uc\?)/.test(page)) {
    out.push({ label: "Original file from Google Drive", url: page, video: false });
    return out;                                   // Drive's player only streams small pieces
  }
  if (page && isVideoSite(page)) {
    out.push({ label: "This video (best quality)", url: page, video: true });
    return out;                                   // yt-dlp handles these sites fully
  }
  // streams first, then the newest (what's playing now), then the biggest
  const found = [...mediaFor(tab.id).values()].sort((a, b) =>
    (a.kind === "HLS" || a.kind === "DASH" ? -1 : 0) - (b.kind === "HLS" || b.kind === "DASH" ? -1 : 0)
    || b.ts - a.ts || b.size - a.size);
  for (const m of found) {
    const stream = m.kind === "HLS" || m.kind === "DASH";
    const size = m.size && !stream ? ` · ${(m.size / 1048576).toFixed(1)} MB` : "";
    out.push({ label: stream ? `${m.kind} stream (best quality)` : `${m.kind} ${m.ext.toUpperCase()}${size}`,
               url: m.url, referer: m.referer || page,
               video: m.kind === "HLS" || m.kind === "DASH" });
  }
  if (src && /^https?:/.test(src) && !out.some(o => o.url === src)) {
    out.push({ label: "Video file", url: src, referer: page, video: false });
  }
  if (!out.length && page) {
    out.push({ label: "Find the video on this page", url: page, video: true });
  }
  return out;
}

// ---------------------------------------------------------------------------
// 3) right-click menus
// ---------------------------------------------------------------------------
api.runtime.onInstalled.addListener(async () => {
  await api.contextMenus.removeAll();
  api.contextMenus.create({ id: "jm-link", title: "Download with JM Download Manager",
    contexts: ["link"] });
  api.contextMenus.create({ id: "jm-video", title: "Download video with JM Download Manager",
    contexts: ["page", "video", "link"] });
  api.contextMenus.create({ id: "jm-season", title: "Download all episodes on this page (whole season)",
    contexts: ["page"] });
});

// Whole season: send the page plus the links as the browser shows them
// (so episode lists built by JavaScript are included too).
async function sendSeason(tab) {
  let links = [];
  try {
    links = await api.tabs.sendMessage(tab.id, { type: "links" }, { frameId: 0 });
  } catch (e) { /* page without the content script: the app reads the HTML itself */ }
  return sendToApp({ url: tab.url, referer: tab.url, series: true, links: links || [],
                     title: tab.title });
}

api.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "jm-season") {
    sendSeason(tab);
    return;
  }
  if (info.menuItemId === "jm-link") {
    sendToApp({ url: info.linkUrl, referer: info.pageUrl || "", video: isVideoSite(info.linkUrl) });
  } else if (info.menuItemId === "jm-video") {
    if (info.linkUrl && isVideoSite(info.linkUrl)) {
      return sendToApp({ url: info.linkUrl, referer: info.pageUrl || "", video: true });
    }
    const c = choicesFor(tab, info.frameUrl, info.srcUrl)[0];
    if (c) sendToApp({ url: c.url, referer: c.referer || info.pageUrl || "", video: c.video,
                       title: tab && tab.title });
  }
});

// ---------------------------------------------------------------------------
// 4) messages from the popup and the in-page button
// ---------------------------------------------------------------------------
api.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === "bypass") {                    // Alt + click on a page
    bypassUntil = Date.now() + 4000;
    return;
  }
  if (msg.type === "rules-changed") {             // edited in the popup
    syncRules(true);
    return;
  }
  if (msg.type === "recent") {
    api.storage.local.get("recent").then(v => reply({ recent: v.recent || [] }));
    return true;
  }
  if (msg.type === "clear-recent") {
    api.storage.local.set({ recent: [] }).then(() => reply({ ok: true }));
    return true;
  }
  if (msg.type === "status") {                    // popup opened: also pull the app's rules
    appAlive().then(async ok => { if (ok) await syncRules(true); reply({ ok }); });
    return true;
  }
  if (msg.type === "launch") {
    ensureApp().then(ok => reply({ ok }));
    return true;
  }
  if (msg.type === "choices") {                   // from the page button / popup
    const tabP = sender.tab ? Promise.resolve(sender.tab)
                            : api.tabs.get(msg.tabId);
    tabP.then(tab => reply({ choices: choicesFor(tab, msg.frameUrl, msg.src, msg.videoUrl),
                               title: msg.videoUrl ? "" : tab.title }));
    return true;
  }
  if (msg.type === "season") {
    api.tabs.get(msg.tabId).then(sendSeason).then(ok => reply({ ok }));
    return true;
  }
  if (msg.type === "grab") {
    sendToApp({ url: msg.url, referer: msg.referer || "", video: !!msg.video,
                title: msg.title || "" }).then(ok => reply({ ok }));
    return true;
  }
});
