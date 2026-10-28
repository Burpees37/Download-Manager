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

for (const [id, key] of [["cap", "capture"], ["btn", "button"], ["auto", "autostart"]]) {
  api.storage.local.get(key).then(v => { $(id).checked = v[key] !== false; });
  $(id).addEventListener("change", () => api.storage.local.set({ [key]: $(id).checked }));
}

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
