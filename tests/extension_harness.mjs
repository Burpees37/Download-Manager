// Runs extension/background.js against a fake browser and prints one line per case.
import fs from "fs";
const store = {}; const listeners = {}; const calls = [];
const ev = (n) => ({ addListener: (f) => (listeners[n] = f) });
globalThis.chrome = {
  storage: { local: { get: async (k) => { const o = {}; for (const x of [].concat(k)) if (x in store) o[x] = store[x]; return o; },
                       set: async (o) => Object.assign(store, o) }, onChanged: ev("sc") },
  downloads: { onDeterminingFilename: ev("odf"), onCreated: ev("oc"),
               cancel: async (id) => calls.push("cancel"), erase: async () => calls.push("erase") },
  runtime: { onInstalled: ev("oi"), onMessage: ev("om"), getPlatformInfo: async () => ({}),
             sendNativeMessage: async () => ({ ok: false }) },
  webRequest: { onBeforeRequest: ev("obr"), onHeadersReceived: ev("ohr") },
  tabs: { onUpdated: ev("tu"), onRemoved: ev("tr"), sendMessage: async () => {} },
  webNavigation: { onCommitted: ev("wnc") },
  contextMenus: { onClicked: ev("cm"), removeAll: async () => {}, create() {} },
  action: { setBadgeBackgroundColor() {}, setBadgeText() {} },
  cookies: { getAll: async () => [] },
};
let appAnswer = true, appUp = true;
globalThis.fetch = async (url, o = {}) => {
  if (!appUp) throw new Error("down");
  if (url.endsWith("/ping")) return { ok: true };
  if (url.endsWith("/settings")) return { ok: true, json: async () => ({ capture_updated: 0 }) };
  if (url.endsWith("/add")) { const j = JSON.parse(o.body); calls.push("add:" + j.wait);
    return { ok: true, json: async () => (appAnswer ? { ok: true }
                                     : { ok: false, error: "cancelled in the app" }) }; }
};
eval(fs.readFileSync(process.argv[2], "utf8"));
const run = (item) => new Promise((res) => {
  calls.length = 0;
  listeners.odf(item, () => { calls.push("suggest"); res(calls.join(",")); });
  setTimeout(() => res(calls.join(",")), 300);
});
const base = { id: 1, url: "https://h.com/game.zip", finalUrl: "https://h.com/game.zip",
               referrer: "https://h.com/", filename: "game.zip", totalBytes: 5e6 };
const out = {};
out.firefoxHookUnused = !listeners.oc;
out.zip = await run(base);
out.png = await run({ ...base, filename: "pic.png", finalUrl: "https://h.com/pic.png" });
appAnswer = false; out.cancelled = await run(base); appAnswer = true;
listeners.om({ type: "bypass" }, {}, () => {}); out.alt = await run(base);
store.capture_exclude = "h.com"; out.excluded = await run(base); store.capture_exclude = "";
store.capture_min_kb = 10000; out.small = await run(base); store.capture_min_kb = 0;
store.capture_types = ""; out.anyType = await run({ ...base, filename: "pic.png" }); delete store.capture_types;
store.browser_capture = false; out.off = await run(base); delete store.browser_capture;
out.pdf = await run({ ...base, filename: "manual.pdf", finalUrl: "https://h.com/manual.pdf" });
out.split = await run({ ...base, filename: "game.7z.001", finalUrl: "https://h.com/game.7z.001" });
out.nameless = await run({ ...base, filename: "", finalUrl: "https://h.com/download.php?id=7",
                           mime: "application/octet-stream" });
out.namelessZip = await run({ ...base, filename: "", finalUrl: "https://h.com/get/7", mime: "application/zip" });
out.namelessHtml = await run({ ...base, filename: "", finalUrl: "https://h.com/get/8", mime: "text/html" });
out.drivePng = await run({ ...base, filename: "photo.png",
  url: "https://drive.usercontent.google.com/download?id=abc&export=download",
  finalUrl: "https://drive.usercontent.google.com/download?id=abc&export=download",
  referrer: "https://drive.google.com/" });
out.docsExport = await run({ ...base, filename: "Notes.txt",
  url: "https://docs.google.com/document/d/abc/export?format=txt",
  finalUrl: "https://doc-0k-2s-docs.googleusercontent.com/export/xyz", referrer: "https://docs.google.com/" });
out.blob = await run({ ...base, url: "blob:https://h.com/1", finalUrl: "blob:https://h.com/1" });
appAnswer = false; out.cancelled2 = await run(base); appAnswer = true;
const rec = await new Promise(r => listeners.om({ type: "recent" }, {}, r));
out.recent = rec.recent.slice(0, 4).map(x => x.result + ":" + (x.reason ? "why" : "-"));
out.recentNames = rec.recent.slice(0, 3).map(x => x.name);
const ch = await new Promise(r => listeners.om({ type: "choices", frameUrl: "" },
  { tab: { id: 9, url: "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view", title: "x" } }, r));
out.driveButton = ch.choices.map(c => c.label + "|" + c.video).join(";");
// episode sites: the player frame moves on to the next episode
const seen = (url, frameId, ct = "application/vnd.apple.mpegurl") => listeners.ohr(
  { tabId: 5, frameId, statusCode: 200, url, responseHeaders: [{ name: "content-type", value: ct }] });
const pick = async () => (await new Promise(r => listeners.om({ type: "choices", frameUrl: "https://ep.site/watch" },
  { tab: { id: 5, url: "https://ep.site/watch", title: "Show" } }, r))).choices.map(c => c.url.split("/").slice(-2).join("/"));
seen("https://cdn.x/ep1/master.m3u8", 3);
out.ep1 = await pick();
seen("https://cdn.x/ep2/master.m3u8", 3);                   // same player, new stream
out.ep2 = await pick();
listeners.wnc({ tabId: 5, frameId: 3 });                    // player frame reloaded for ep 3
out.reloaded = await pick();
seen("https://cdn.x/ep3/master.m3u8", 4);
out.ep3 = await pick();
appUp = false; await new Promise(r => setTimeout(r, 3100)); out.appDown = await run(base);
console.log(JSON.stringify(out));
