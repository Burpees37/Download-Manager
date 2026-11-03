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
    return { ok: true, json: async () => ({ ok: appAnswer }) }; }
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
appUp = false; await new Promise(r => setTimeout(r, 3100)); out.appDown = await run(base);
console.log(JSON.stringify(out));
