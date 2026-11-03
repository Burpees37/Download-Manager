"""
JM Download Manager — an IDM-style download accelerator.

How it beats per-connection speed caps:
  * Each file is split into parts that download in parallel over separate
    HTTP connections (HTTP Range requests).
  * Dynamic segmentation: when a connection finishes its part, it takes over
    half of the biggest part that is still downloading, so every connection
    stays busy until the very end (this is IDM's main trick).
  * Pause / resume, even after closing the app or rebooting.

Also:
  * Video sites (YouTube, Facebook, TikTok, ...) through yt-dlp, with
    parallel fragment downloads.
  * A local listener on 127.0.0.1 that the browser extension sends
    downloads to (with your cookies and referrer, so links from file hosts work).
  * Download queue, global speed limiter, clipboard watcher.

Run:   python jmdm.py                -> GUI
       python jmdm.py <url>          -> GUI with the Add dialog filled in
       python jmdm.py --cli <url> [-o folder] [-n connections] [--limit KB/s]
"""
