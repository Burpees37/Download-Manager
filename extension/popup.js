const api = globalThis.browser ?? chrome;
const $ = (id) => document.getElementById(id);

function showStatus(ok) {
  $("dot").className = "dot " + (ok ? "on" : "off");
  $("state").textContent = ok ? "App is running" : "App is not running";
  $("start").hidden = ok;
}

api.runtime.sendMessage({ type: "status" }).then(r => showStatus(r && r.ok));

$("start").addEventListener("click", async () => {
  $("start").disabled = true;
  $("state").textContent = "Starting…";
  const r = await api.runtime.sendMessage({ type: "launch" });
  $("start").disabled = false;
  showStatus(r && r.ok);
  if (!(r && r.ok)) $("msg").textContent = "Couldn't start it. Open JM Download Manager once by hand — after that the browser can start it.";
});

for (const [id, key] of [["btn", "button"], ["auto", "autostart"]]) {
  api.storage.local.get(key).then(v => { $(id).checked = v[key] !== false; });
  $(id).addEventListener("change", () => api.storage.local.set({ [key]: $(id).checked }));
}

// Catch rules: shared with the app's Settings (the background page syncs them).
const DEFAULT_TYPES = "zip rar 7z tar gz bz2 xz zst iso img bin cue exe msi msix appx apk xapk " +
  "apks obb jar dmg deb rpm mp4 mkv avi mov wmv flv webm m4v mpg mpeg 3gp mp3 m4a aac " +
  "flac wav ogg opus wma";
const tidy = (t) => t.split(/[\s,;]+/).filter(Boolean).join(" ");

async function loadRules() {
  const v = await api.storage.local.get(["browser_capture", "capture", "capture_types",
                                         "capture_min_kb", "capture_exclude"]);
  $("cap").checked = v.browser_capture ?? (v.capture !== false);
  $("types").value = v.capture_types ?? DEFAULT_TYPES;
  $("min").value = v.capture_min_kb ?? 0;
  $("excl").value = v.capture_exclude ?? "";
}

async function saveRules() {
  await api.storage.local.set({
    browser_capture: $("cap").checked,
    capture_types: tidy($("types").value),
    capture_min_kb: Math.max(0, parseInt($("min").value, 10) || 0),
    capture_exclude: tidy($("excl").value),
    capture_updated: Date.now() / 1000
  });
  api.runtime.sendMessage({ type: "rules-changed" }).catch(() => {});
}

// Show what the app has, if it changed the rules more recently.
api.runtime.sendMessage({ type: "status" }).then(loadRules);
loadRules();
$("cap").addEventListener("change", saveRules);
for (const id of ["types", "min", "excl"]) $(id).addEventListener("change", saveRules);
$("reset").addEventListener("click", () => { $("types").value = DEFAULT_TYPES; saveRules(); });

function send(choice, title) {
  api.runtime.sendMessage({ type: "grab", url: choice.url, referer: choice.referer || "",
                            video: choice.video, title })
    .then(r => { $("msg").textContent = r && r.ok ? "Sent to the app ✓" : "Couldn't reach the app."; });
}

$("vid").addEventListener("click", async () => {
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https?:/.test(tab.url || "")) { $("msg").textContent = "Open a video page first."; return; }
  const r = await api.runtime.sendMessage({ type: "choices", tabId: tab.id });
  const choices = (r && r.choices) || [];
  if (choices.length === 1) return send(choices[0], r.title);
  $("list").innerHTML = "";
  for (const c of choices) {
    const d = document.createElement("div");
    d.textContent = c.label;
    d.addEventListener("click", () => send(c, r.title));
    $("list").appendChild(d);
  }
  $("msg").textContent = choices.length ? "Pick one:" : "Nothing found yet — play the video first.";
});

$("season").addEventListener("click", async () => {
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https?:/.test(tab.url || "")) { $("msg").textContent = "Open the series page first."; return; }
  const r = await api.runtime.sendMessage({ type: "season", tabId: tab.id });
  $("msg").textContent = r && r.ok ? "Sent — the app is reading the episodes ✓" : "Couldn't reach the app.";
});
