// Floating "Download video" button over video players (like IDM's panel).
(() => {
  if (window.__jmdmButton) return;
  window.__jmdmButton = true;
  const api = globalThis.browser ?? chrome;

  let enabled = true;
  const dismissed = new WeakSet();     // videos you closed the button on (✕)
  let host = null, root = null, btn = null, menu = null, current = null;
  let hovered = null;                  // the video under the mouse pointer
  let overButton = false;

  api.storage.local.get("button").then(({ button = true }) => { enabled = button; place(); });
  api.storage.onChanged.addListener((c) => {
    if (c.button) { enabled = c.button.newValue !== false; place(); }
  });

  function build() {
    host = document.createElement("div");
    host.id = "jmdm-video-button";
    root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        :host { all: initial; }
        .wrap { font: 600 13px/1.2 "Segoe UI", system-ui, sans-serif; }
        .row { display: flex; gap: 2px; }
        button { all: unset; cursor: pointer; background: rgba(23, 92, 196, .92); color: #fff;
                 padding: 7px 12px; border-radius: 7px; box-shadow: 0 2px 10px rgba(0,0,0,.35);
                 white-space: nowrap; }
        button:hover { background: #2b7de9; }
        .x { padding: 7px 9px; background: rgba(0,0,0,.55); }
        .menu { margin-top: 4px; background: #fff; color: #222; border-radius: 8px;
                box-shadow: 0 4px 18px rgba(0,0,0,.35); overflow: hidden; min-width: 230px; }
        .menu div { padding: 8px 12px; cursor: pointer; font-weight: 500; }
        .menu div:hover { background: #e8f0fe; }
        .menu .note { cursor: default; color: #666; font-size: 12px; }
        .menu .note:hover { background: none; }
      </style>
      <div class="wrap">
        <div class="row"><button class="go">⬇ Download video</button><button class="x" title="Hide on this page">✕</button></div>
        <div class="menu" hidden></div>
      </div>`;
    btn = root.querySelector(".go");
    menu = root.querySelector(".menu");
    root.querySelector(".x").addEventListener("click", (e) => {
      e.stopPropagation();
      if (current) dismissed.add(current);
      if (hovered === current) hovered = null;
      place();
    });
    btn.addEventListener("click", onClick);
    host.addEventListener("pointerenter", () => { overButton = true; });
    host.addEventListener("pointerleave", () => { overButton = false; });
    Object.assign(host.style, { position: "fixed", zIndex: 2147483647, display: "none" });
    document.documentElement.appendChild(host);
  }

  function usable(v, minW, minH) {
    const r = v.getBoundingClientRect();
    return r.width >= minW && r.height >= minH && r.bottom > 40 && r.top < innerHeight - 40
      && !dismissed.has(v) && getComputedStyle(v).visibility !== "hidden";
  }

  function bestVideo() {
    let best = null, area = 0;
    for (const v of document.querySelectorAll("video")) {
      const r = v.getBoundingClientRect();
      const a = r.width * r.height;
      if (usable(v, 220, 120) && a > area) { best = v; area = a; }
    }
    return best;
  }

  // The video under the pointer. Sites like Facebook cover their players with
  // see-through layers, so the mouse is never "on" the video itself: look for
  // a video whose box holds the pointer instead.
  function videoAt(x, y) {
    let best = null, area = Infinity;
    for (const v of document.querySelectorAll("video")) {
      const r = v.getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom && usable(v, 140, 90)) {
        const a = r.width * r.height;
        if (a < area) { best = v; area = a; }      // the innermost one
      }
    }
    return best;
  }

  let moveQueued = false;
  addEventListener("pointermove", (e) => {
    if (moveQueued || overButton) return;
    moveQueued = true;
    const x = e.clientX, y = e.clientY;
    requestAnimationFrame(() => {
      moveQueued = false;
      const v = videoAt(x, y);
      if (v !== hovered) { hovered = v; place(); }
    });
  }, { passive: true, capture: true });

  function place() {
    if (hovered && !(hovered.isConnected && usable(hovered, 140, 90))) hovered = null;
    const v = !enabled ? null : (hovered || (overButton && current) || bestVideo());
    if (!v) {
      if (host) host.style.display = "none";
      current = null;
      return;
    }
    if (!host) build();
    if (v !== current && menu) menu.hidden = true;
    current = v;
    const r = v.getBoundingClientRect();
    const narrow = r.width < 260;
    if (!btn.dataset.busy) btn.textContent = narrow ? "⬇ Download" : "⬇ Download video";
    const w = narrow ? 140 : 200;
    host.style.display = "block";
    // inside the video's visible part, under any fixed top bar
    host.style.top = Math.max(64, Math.min(r.bottom - 44, r.top + 10)) + "px";
    host.style.left = Math.max(6, Math.min(innerWidth - w - 20, r.right - w - 10)) + "px";
  }

  // The video's own address, for players inside a feed (the tab's address is
  // the feed, which the video finder can't use).
  const POST_LINK = /^https:\/\/(www\.|m\.|web\.)?facebook\.com\/(reel\/\d+|[^?#]*\/videos\/(\w+\/)?\d+|watch\/?\?v=\d+)/;
  function videoLink(v) {
    if (!v || !/(^|\.)facebook\.com$/.test(location.hostname)) return "";
    const tagged = v.closest("[data-video-id]");
    const id = tagged && tagged.getAttribute("data-video-id");
    if (id && /^\d+$/.test(id)) return "https://www.facebook.com/watch/?v=" + id;
    let el = v.parentElement;
    for (let i = 0; el && i < 12; i++, el = el.parentElement) {
      const found = new Set();
      for (const a of el.querySelectorAll("a[href]")) {
        const m = POST_LINK.exec(a.href);
        if (m) found.add(m[0]);
      }
      if (found.size === 1) return [...found][0];
      if (found.size > 1) return "";              // several videos: can't tell which
    }
    return "";
  }

  function say(text) {
    btn.textContent = text;
    btn.dataset.busy = "1";
    setTimeout(() => { delete btn.dataset.busy; place(); }, 2500);
  }

  function grab(choice, title) {
    menu.hidden = true;
    btn.textContent = "Sending…";
    btn.dataset.busy = "1";
    api.runtime.sendMessage({ type: "grab", url: choice.url, referer: choice.referer || location.href,
                              video: choice.video, title })
      .then(r => say(r && r.ok ? "Sent to app ✓" : "App not running"))
      .catch(() => say("App not running"));
  }

  async function onClick(e) {
    e.stopPropagation();
    if (!menu.hidden) { menu.hidden = true; return; }
    const src = current && current.currentSrc;
    const videoUrl = videoLink(current);
    let r;
    try {
      r = await api.runtime.sendMessage({ type: "choices", frameUrl: location.href, src, videoUrl });
    } catch (err) { return say("Reload the page"); }
    const choices = (r && r.choices) || [];
    if (choices.length === 1) return grab(choices[0], r.title);
    menu.replaceChildren();
    if (!choices.length) {
      const n = document.createElement("div");
      n.className = "note";
      n.textContent = "No downloadable stream found yet — press play, then try again.";
      menu.appendChild(n);
    }
    for (const c of choices) {
      const d = document.createElement("div");
      d.textContent = c.label;
      d.addEventListener("click", (ev) => { ev.stopPropagation(); grab(c, r.title); });
      menu.appendChild(d);
    }
    menu.hidden = false;
  }

  api.runtime.onMessage.addListener((msg, sender, reply) => {
    if (msg.type === "media-found") place();
    if (msg.type === "links" && window === window.top) {
      reply([...document.links].slice(0, 3000).map(l =>
        [l.href, (l.textContent || l.title || "").trim().slice(0, 100)]));
    }
  });
  document.addEventListener("click", () => { if (menu) menu.hidden = true; });
  // Alt + click on a link: let the browser download it instead of the app.
  for (const ev of ["mousedown", "click"]) {
    addEventListener(ev, (e) => {
      if (e.altKey && e.button === 0) api.runtime.sendMessage({ type: "bypass" }).catch(() => {});
    }, true);
  }
  addEventListener("scroll", place, { passive: true, capture: true });
  addEventListener("resize", place);
  setInterval(place, 800);
})();
