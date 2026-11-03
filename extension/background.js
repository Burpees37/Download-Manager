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
  capture_types: "zip rar 7z tar gz bz2 xz zst iso img bin cue exe msi msix appx apk xapk " +
    "apks obb jar dmg deb rpm mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp mp3 m4a aac " +
    "flac wav ogg opus wma",
  capture_min_kb: 0,
  capture_exclude: "",
  capture_updated: 0
};

async function getRules() {
  const v = await api.storage.local.get([...Object.keys(RULE_DEFAULTS), "capture"]);
  const r = { ...RULE_DEFAULTS };
  for (const k of Object.keys(RULE_DEFAULTS)) if (v[k] !== undefined) r[k] = v[k];
  if (v.browser_capture === undefined && v.capture === false) r.browser_capture = false; // old switch
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
function onSite(url, sites) {
  const h = host(url);
  return !!h && sites.some(d => h === d || h.endsWith("." + d));
}

// Hold Alt while clicking a link: that one download stays in the browser.
let bypassUntil = 0;

// Why a download should stay in the browser, or "" when the app should take it.
async function skipReason(item, url) {
  const rules = await getRules();
  if (!rules.browser_capture) return "catching is off";
  if (!/^https?:/i.test(url)) return "not a web link";     // blob:/data: can't be re-opened
  if (item.byExtensionId) return "started by an extension";
  if (Date.now() < bypassUntil) { bypassUntil = 0; return "Alt was held"; }
  const sites = words(rules.capture_exclude)
    .map(x => x.replace(/^[a-z]+:\/\//, "").replace(/\/.*$/, "").replace(/^\*?\./, ""));
  if ([url, item.url, item.referrer].some(u => u && onSite(u, sites))) return "excluded site";
  const types = words(rules.capture_types).map(x => x.replace(/^\*?\./, ""));
  if (types.length) {
    const name = (item.filename || "").split(/[\\/]/).pop();
    let ext = extOf(name);
    if (!ext) { try { ext = extOf(new URL(url).pathname); } catch (e) {} }
    if (!types.includes(ext)) return "file type not in the list";
  }
  const size = item.totalBytes > 0 ? item.totalBytes : (item.fileSize > 0 ? item.fileSize : 0);
  const min = Number(rules.capture_min_kb) || 0;
  if (min > 0 && size > 0 && size < min * 1024) return "smaller than the minimum";
  return "";
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
    if (!r.ok) return { ok: false };
    return await r.json();
  } catch (e) { return { ok: false }; }
  finally { clearInterval(keep); }
}

// Offer a held download to the app. True = the app took it.
async function offerToApp(item) {
  const url = item.finalUrl || item.url;
  if (await skipReason(item, url)) return false;
  // If the app is closed, start it; if that doesn't work, the browser keeps it.
  if (!(await appAlive()) && !(await launchApp())) return false;
  const form = posts.get(url.split("#")[0]) || posts.get((item.url || "").split("#")[0]);
  const filename = (item.filename || "").split(/[\\/]/).pop();
  const res = await checkWithApp({ url, referer: item.referrer || "", filename, video: false,
                                   post: form ? form.body : "" });
  return !!(res && res.ok);
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
    offerToApp(item).then(async (taken) => {
      if (taken) {
        await dropBrowserCopy(item.id);
        flash("✓", "#2e7d32");
      } else {
        suggest();                                   // carry on as a normal download
      }
    }).catch(() => suggest());
    return true;                                     // we answer later
  });
} else {
  // Firefox has no such hook: pause the download as soon as it starts instead.
  api.downloads.onCreated.addListener(async (item) => {
    const url = item.finalUrl || item.url;
    if (await skipReason(item, url)) return;
    let paused = false;
    try { await api.downloads.pause(item.id); paused = true; } catch (e) { /* already done */ }
    if (await offerToApp(item)) {
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
    list.set(key, { url, kind, size: total, ext: (ct.split("/")[1] || "").replace("x-", ""),
                    referer: d.initiator || d.documentUrl || "", ts: Date.now() });
    if (list.size > 30) list.delete(list.keys().next().value);
    api.tabs.sendMessage(d.tabId, { type: "media-found", count: list.size }).catch(() => {});
  }
}, { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"] }, ["responseHeaders"]);

api.tabs.onUpdated.addListener((tabId, info) => {
  if (info.url) media.delete(tabId);              // new page: forget the old one's media
});
api.tabs.onRemoved.addListener((tabId) => media.delete(tabId));

// Pick what the "Download video" button should download.
function choicesFor(tab, frameUrl, src) {
  const out = [];
  const page = tab && tab.url;
  if (frameUrl && isVideoSite(frameUrl) && frameUrl !== page) {
    out.push({ label: "Embedded video (best quality)", url: frameUrl, video: true });
  }
  if (page && isVideoSite(page)) {
    out.push({ label: "This video (best quality)", url: page, video: true });
    return out;                                   // yt-dlp handles these sites fully
  }
  const found = [...mediaFor(tab.id).values()].sort((a, b) =>
    (a.kind === "HLS" || a.kind === "DASH" ? -1 : 0) - (b.kind === "HLS" || b.kind === "DASH" ? -1 : 0)
    || b.size - a.size);
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
    tabP.then(tab => reply({ choices: choicesFor(tab, msg.frameUrl, msg.src), title: tab.title }));
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
