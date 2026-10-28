const dot = document.getElementById("dot");
const state = document.getElementById("state");
const cap = document.getElementById("cap");
const vid = document.getElementById("vid");
const msg = document.getElementById("msg");

chrome.runtime.sendMessage({ type: "status" }, (r) => {
  const ok = r && r.ok;
  dot.className = "dot " + (ok ? "on" : "off");
  state.textContent = ok ? "App is running" : "App is not running — open JM Download Manager";
  vid.disabled = !ok;
});

chrome.storage.local.get("capture").then(({ capture = true }) => { cap.checked = capture; });
cap.addEventListener("change", () => chrome.storage.local.set({ capture: cap.checked }));

vid.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https?:/.test(tab.url || "")) { msg.textContent = "Open a video page first."; return; }
  vid.disabled = true;
  chrome.runtime.sendMessage({ type: "video", url: tab.url }, (r) => {
    msg.textContent = r && r.ok ? "Sent to the app ✓" : "Couldn't reach the app.";
    vid.disabled = false;
  });
});
