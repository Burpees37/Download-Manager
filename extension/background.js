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
async function appAlive(ms = 800) {
  try {
    const c = new AbortController();
    const t = setTimeout(() => c.abort(), ms);
    const r = await fetch(APP + "/ping", { headers: HDR, signal: c.signal });
    clearTimeout(t);
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
api.downloads.onCreated.addListener(async (item) => {
  const { capture = true } = await api.storage.local.get("capture");
  if (!capture) return;
  const url = item.finalUrl || item.url;
  if (!/^https?:/i.test(url)) return;             // blob:/data: can't be re-downloaded
  if (item.byExtensionId) return;
  // If the app is closed, start it (fast); if that doesn't work in time,
  // let the browser keep the download.
  if (!(await appAlive()) && !(await launchApp())) return;
  try {
    await api.downloads.cancel(item.id);
    await api.downloads.erase({ id: item.id });
  } catch (e) { /* already finished */ }
  const filename = (item.filename || "").split(/[\\/]/).pop();
  sendToApp({ url, referer: item.referrer || "", filename, video: false }, { launch: false });
});

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
});

api.contextMenus.onClicked.addListener((info, tab) => {
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
  if (msg.type === "status") {
    appAlive().then(ok => reply({ ok }));
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
  if (msg.type === "grab") {
    sendToApp({ url: msg.url, referer: msg.referer || "", video: !!msg.video,
                title: msg.title || "" }).then(ok => reply({ ok }));
    return true;
  }
});
