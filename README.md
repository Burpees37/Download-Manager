# JM Download Manager

An IDM-style download accelerator for Windows, with an extension for Chrome, Edge, Brave and Firefox.

- **Beats per-connection caps.** Each file is split into parts that download over up to 32 connections at once.
- **Dynamic segmentation that follows the network.** A free connection helps whichever part will take *longest* to finish, measured by that part's own speed. The split is sized so both halves finish together. Connections that stall or run far slower than the rest are dropped and reopened, often landing on a faster server.
- **Connection reuse.** Each connection stays open and carries part after part, with no new handshake each time. The final address after redirects is remembered, so later parts skip the redirect chain.
- **Beats per-host caps with mirrors.** It can download one file from several hosts at the same time. Each host caps you separately, so their speeds add up.
- **Downloads YouTube playlists and channels.** It also handles single videos from YouTube, Facebook, TikTok, Instagram, X and Vimeo. Videos are saved as H.264 + AAC, which plays in every Windows player.
- **Video button on any site.** A *Download video* button appears over video players. It also picks up the streams a page plays: MP4/WebM files and HLS (`.m3u8`) / DASH (`.mpd`) streams.

Measured in testing:

| Test | Result |
|---|---|
| Host caps each connection at 1 MB/s (24 MB file) | 1 connection: 25 s, 16 connections: **3 s** |
| Host caps your total at 2 MB/s | 1 host: 12.5 s, 2 mirrors: 7.5 s, 3 mirrors: **5.5 s** |
| Unreliable server (some connections stall or crawl), 8 connections | v1.1: 40 s, now: **16.5 s** |

Every downloaded file was checked byte-for-byte against the original.

## Get the app

**Easiest:** open this repo's **Releases** page and download **JM-Download-Manager-Windows.zip** from *Latest build*. It contains the app, `jmdm-host.exe` (lets the browser start the app), ffmpeg and the extension. Keep them in the same folder. Unzip it anywhere, for example `D:\JM-Download-Manager`.

To publish a proper release, push a tag such as `v1.1.0`. The zip then appears on the **Releases** page.

**Build it yourself:** install Python 3.10+ (tick *Add to PATH*), then double-click `build.bat`.

### Add the browser extension
**Chrome, Edge, Brave:**
1. Open `chrome://extensions` (or `edge://extensions`, `brave://extensions`).
2. Turn on **Developer mode**.
3. Click **Load unpacked** and pick the `extension` folder.

**Firefox (121 or newer):**
1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on…** and pick `extension\manifest.json`.
3. In `about:addons` → JM Download Manager → *Permissions*, allow access to all websites.

Firefox removes temporary add-ons when it closes. For a permanent install, the extension has to be signed (free, through addons.mozilla.org as an unlisted add-on).

**Open the app once after installing.** That registers it with your browsers. After that, the browser starts the app by itself whenever it has something to download. The browser sends your cookies and the page the download came from, so file-host links keep working. If the app can't start, the browser downloads normally.

The extension popup has switches for catching downloads, the video button, and starting the app automatically.

### Catching browser downloads (like IDM)
In Chrome, Edge and Brave the extension takes a download **before the browser saves anything or shows "Save as"**, so nothing lands twice in your Downloads folder. Firefox pauses the download the moment it starts instead.

- **Catch rules.** Only the file types in the list are caught (archives, installers, disk images, APKs, video and audio by default). You can also skip small files and list sites that should never be caught. Edit them in the popup under *Catch rules* or in the app's *Settings*. Both share the same rules, and whichever you changed last wins.
- **Hold Alt** while clicking a link to let the browser download that one file normally.
- **Cancel gives it back.** With *Ask before starting* on, the browser waits while the app's Add dialog is open. Press *Cancel* (or leave it for 4 minutes) and the browser downloads the file as usual.
- If the app can't start or can't open the link, the browser keeps the download.

## Updates
- **yt-dlp updates itself** once a day in the background, so YouTube and Facebook downloads keep working without rebuilding the app. If an update ever fails to load, the app goes back to the yt-dlp built into it.
- **The app checks for a new version** once a day and asks before installing. It downloads the new build, closes, swaps the files and opens again. Your download list and settings in `jmdm_data` are kept. If the browser extension changed, it tells you to click Reload on it.
- Turn either off, or press **Check now**, in *Settings → Updates*. Copies built with `build.bat` only update yt-dlp; get app updates with `git pull` and `build.bat`.

## How to download…

| What | How |
|---|---|
| **Direct link** | Copy it and the app offers to download it, or use **Add URL**. |
| **Gate / redirect links (`gate.php`, `go.php`, `out.php`, `download.php`…)** | Paste the link into the app, or just copy it — the app recognises these. It follows the gate to the real file: HTTP redirects (keeping the session cookies the gate hands out), meta-refresh and JavaScript redirects, the real link packed inside the gate link (`?url=…`, base64 or hex), and gate pages with a single Download link or button. If you pause and the file link expires, resuming goes through the gate again for a fresh one. Gates that ask for a captcha are left to the browser. **If the bare `gate.php` link gives 404**, it's the target of the site's Download button (a form with hidden data), so it can't be opened on its own: click the site's Download button in your browser instead. The extension passes the form's data to the app, and if the app still can't get the file (e.g. a one-time token), the browser simply keeps downloading it. |
| **File host (UploadHaven, Mediafire…)** | Open the link in your browser, finish the timer and click the site's Download button. The extension catches the real file. |
| **Same file on several hosts** | Start it from one host. Then click Download on the other host's page. The app notices the file has the same size and asks to use it as a mirror. You can also right-click a download → **Add mirror link…**, or paste extra links into the *Mirrors* box. |
| **YouTube / Facebook video** | Click **⬇ Download video** on the player, paste the link into the app, or use the extension popup. |
| **Video on any other site** | Press play, then click **⬇ Download video** on the player. If the page plays several streams, pick one from the list. |
| **YouTube playlist / channel** | Paste the link. The app recognises playlist links and ticks **Whole playlist / channel** for you. For a single video that sits inside a playlist, you choose. If a link turns out not to be a real playlist (for example a YouTube Mix), it downloads the single video instead. *Only items* takes `1-20` or `3,5,9`. Videos are numbered and saved in a folder named after the playlist. |
| **Whole season from a series page** | On a page with a cover and numbered episode links (1, 2, 3… or "Episode 1"…), right-click → **Download all episodes on this page**, or paste the page into the app and tick **Whole season**. The app opens each episode, finds its video (MP4, HLS stream or embedded player) and downloads them in order into a folder named after the series: `Show - E01.mp4`, `Show - E02.mp4`…, plus `cover.jpg`. *Only items* (e.g. `1-6`) limits which episodes. Stray numbered links, like a year or comment pages, are ignored. Using right-click also works when the episode list is built by JavaScript. |
| **Game in many parts** | Add every part. 3 download at a time and the rest queue (change this in Settings). |

## Scope and limits

**What it can't get around:**

- **Your own internet speed.** If your connection tops out at 50 Mbps, nothing here makes it faster.
- **Hosts that cap your total speed, with no mirrors available.** More connections to the same host don't help. The way past this is more hosts (mirrors), or that host's premium plan. A debrid service is the other option: a paid service that downloads from file hosts with its own premium accounts.
- **Timers, captchas and logins.** You do those in the browser and the app takes over afterwards. It doesn't bypass them, and it doesn't rotate IPs or accounts to dodge a host's limits.
- **Hosts that allow only one connection.** The app detects this and downloads normally.
- **One-time links.** If a caught download says "expired", click the site's Download button again.
- **Protected streams** (Netflix, Disney+, Spotify) can't be downloaded. Private Facebook videos work only when sent from the browser, which passes along your login cookies.
- **YouTube changes often.** If video downloads break, re-run the build (re-run the Action, or `build.bat`) to get the newest yt-dlp.
- **Mirrors must be the identical file.** A mirror is used only if its size matches exactly; anything else is skipped and the reason is shown.
- **Video quality:**
  - *best* gives up to 1080p in H.264 with AAC audio, which plays everywhere.
  - *1440p*, *4K* and *8K* (and *max*, the highest available) use VP9/AV1, because that's all YouTube offers above 1080p. 8K on YouTube is AV1 only.
  - To play these, use VLC or install the free *AV1 Video Extension* from the Microsoft Store. Smooth 8K playback needs a graphics card or CPU with AV1 hardware decoding (roughly 2021 or newer).
  - 8K files are large: about 200–400 MB per minute.
  - Any file that still ends up with Opus audio is converted to AAC automatically.
- **The video button can't work inside full-screen players.** Leave full screen to use it.
- **Not supported:** torrents, macOS.
- **Windows warning:** the `.exe` isn't code-signed, so SmartScreen may warn the first time. Click *More info → Run anyway*.

## Command line
```
"JM Download Manager.exe" --cli <url> -n 16 -o D:\Downloads
"JM Download Manager.exe" --cli <url> -m <mirror-url> -m <another-mirror>
"JM Download Manager.exe" --cli <playlist-url> --playlist --items 1-10 --quality 720p
```

Settings and the download list are stored in `jmdm_data` next to the app.

## For developers
`jmdm.py` only starts the app; the code is in the `jmdm_app` package, one job per file:

| File | What it does |
|---|---|
| `downloader.py` | The engine: parts, many connections, mirrors, pause/resume |
| `net.py` | Connections kept open between parts; reading a file's size and name |
| `gates.py` | Following gate.php-style links to the real file |
| `video.py` | YouTube and other video sites through yt-dlp, ffmpeg conversion |
| `series.py` | Whole seasons from a series page |
| `manager.py` | The download list, queue, settings and saving |
| `browser.py` | What the browser extension talks to (`/ping`, `/settings`, `/add`) |
| `integration.py` | Lets the browser start the app |
| `updates.py` | yt-dlp and app updates |
| `gui.py` / `cli.py` | The window / command-line mode |
| `config.py`, `util.py` | Shared names and small helpers |

**Tests:** `pip install pytest`, then `python -m pytest`. They run a local test server and check downloads byte-for-byte (many connections, no-range hosts, pause/resume, stalled connections, mirrors), gate links, the browser hand-off, the updater and the extension's catch rules (the last needs Node.js). GitHub runs them on every push and only publishes a build when they pass.

## Extra tool: page_media_grabber.py
`tools/page_media_grabber.py` is a standalone script for your own media server. It scans a page's HTML for `<video>`/`<source>` tags and `.m3u8`/`.mpd` links, then downloads what it finds one by one into a folder.

```
pip install requests beautifulsoup4
python tools/page_media_grabber.py https://myserver.local/show/ --list
python tools/page_media_grabber.py --from-file pages.txt -o D:\Videos\Show --numbered --ignore-robots
```

Pages that build their player with JavaScript won't show anything to it. Use the browser extension's video button for those.
