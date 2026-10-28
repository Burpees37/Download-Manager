// JM Download Manager — browser side.
// Catches downloads the browser starts and hands them to the desktop app
// (with your cookies + referrer so file-host links keep working).

const APP = "http://127.0.0.1:8721";
const HDR = { "X-JMDM": "1" };
const VIDEO_SITES = ["youtube.com", "youtu.be", "facebook.com", "fb.watch", "tiktok.com",
  "instagram.com", "twitter.com", "x.com", "vimeo.com", "dailymotion.com", "twitch.tv"];

function isVideoSite(url) {
  try {
    const h = new URL(url).hostname.toLowerCase();
    return VIDEO_SITES.some(d => h === d || h.endsWith("." + d));
  } catch (e) { return false; }
}

async function appAlive() {
  try {
    const c = new AbortController();
    const t = setTimeout(() => c.abort(), 800);
    const r = await fetch(APP + "/ping", { headers: HDR, signal: c.signal });
    clearTimeout(t);
    return r.ok;
  } catch (e) { return false; }
}

async function cookieHeader(url) {
  try {
    const cs = await chrome.cookies.getAll({ url });
    return cs.map(c => c.name + "=" + c.value).join("; ");
  } catch (e) { return ""; }
}

function flash(text, color) {
  chrome.action.setBadgeBackgroundColor({ color });
  chrome.action.setBadgeText({ text });
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 3000);
}

async function sendToApp(job) {
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
    flash("off", "#c62828");   // app isn't running
    return false;
  }
}

// ---- catch normal browser downloads ------------------------------------
chrome.downloads.onCreated.addListener(async (item) => {
  const { capture = true } = await chrome.storage.local.get("capture");
  if (!capture) return;
  const url = item.finalUrl || item.url;
  if (!/^https?:/i.test(url)) return;            // blob:/data: can't be re-downloaded
  if (item.byExtensionId) return;
  if (!(await appAlive())) return;               // app closed -> let the browser handle it
  try {
    await chrome.downloads.cancel(item.id);
    await chrome.downloads.erase({ id: item.id });
  } catch (e) { /* already finished */ }
  const filename = (item.filename || "").split(/[\\/]/).pop();
  sendToApp({ url, referer: item.referrer || "", filename, video: false });
});

// ---- right-click menus -------------------------------------------------
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "jm-link", title: "Download with JM Download Manager",
      contexts: ["link"] });
    chrome.contextMenus.create({ id: "jm-video", title: "Download video with JM Download Manager",
      contexts: ["page", "video", "link"] });
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "jm-link") {
    sendToApp({ url: info.linkUrl, referer: info.pageUrl || "", video: isVideoSite(info.linkUrl) });
  } else if (info.menuItemId === "jm-video") {
    const url = (info.linkUrl && isVideoSite(info.linkUrl)) ? info.linkUrl
              : (tab && tab.url) || info.pageUrl;
    sendToApp({ url, referer: info.pageUrl || "", video: true });
  }
});

// ---- popup messages ----------------------------------------------------
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === "status") { appAlive().then(ok => reply({ ok })); return true; }
  if (msg.type === "video") {
    sendToApp({ url: msg.url, referer: msg.url, video: true }).then(ok => reply({ ok }));
    return true;
  }
});
