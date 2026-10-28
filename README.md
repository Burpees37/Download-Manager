# JM Download Manager

An IDM-style download accelerator for Windows, with a Chrome/Edge extension.

- **Beats per-connection caps.** Each file is split into parts that download over up to 32 connections at once. When one connection finishes early, it takes over half of the slowest part, so every connection stays busy until the end.
- **Beats per-host caps with mirrors.** It can download one file from several hosts at the same time. Each host caps you separately, so their speeds add up.
- **Downloads YouTube playlists and channels.** It also handles single videos from YouTube, Facebook, TikTok, Instagram, X and Vimeo.

Measured in testing:

| Test | Result |
|---|---|
| Host caps each connection at 1 MB/s (24 MB file) | 1 connection: 25 s, 16 connections: **3 s** |
| Host caps your total at 2 MB/s | 1 host: 12.5 s, 2 mirrors: 7.5 s, 3 mirrors: **5.5 s** |

Every downloaded file was checked byte-for-byte against the original.

## Get the app

**Easiest:** open this repo's **Releases** page and download **JM-Download-Manager-Windows.zip** from *Latest build*. It contains the `.exe`, ffmpeg and the extension. Unzip it anywhere, for example `D:\JM-Download-Manager`.

To publish a proper release, push a tag such as `v1.1.0`. The zip then appears on the **Releases** page.

**Build it yourself:** install Python 3.10+ (tick *Add to PATH*), then double-click `build.bat`.

### Add the browser extension (Chrome or Edge)
1. Open `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode**.
3. Click **Load unpacked** and pick the `extension` folder.

While the app is open, the browser sends it every download, along with your cookies and the page it came from, so file-host links keep working. If the app is closed, the browser downloads normally.

## How to download…

| What | How |
|---|---|
| **Direct link** | Copy it and the app offers to download it, or use **Add URL**. |
| **File host (UploadHaven, Mediafire…)** | Open the link in your browser, finish the timer and click the site's Download button. The extension catches the real file. |
| **Same file on several hosts** | Start it from one host. Then click Download on the other host's page. The app notices the file has the same size and asks to use it as a mirror. You can also right-click a download → **Add mirror link…**, or paste extra links into the *Mirrors* box. |
| **YouTube video** | Paste the link, or click the extension's **Download video on this tab**. |
| **YouTube playlist / channel** | Paste the playlist link and tick **Whole playlist / channel**. To get only some videos, use *Only items*, e.g. `1-20` or `3,5,9`. Videos are numbered and saved in a folder named after the playlist. |
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
- **Not supported:** torrents, Firefox, macOS.
- **Windows warning:** the `.exe` isn't code-signed, so SmartScreen may warn the first time. Click *More info → Run anyway*.

## Command line
```
"JM Download Manager.exe" --cli <url> -n 16 -o D:\Downloads
"JM Download Manager.exe" --cli <url> -m <mirror-url> -m <another-mirror>
"JM Download Manager.exe" --cli <playlist-url> --playlist --items 1-10 --quality 720p
```

Settings and the download list are stored in `jmdm_data` next to the app.
