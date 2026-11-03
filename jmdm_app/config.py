"""Names, versions and fixed settings used across the app."""

APP_NAME = "JM Download Manager"
VERSION = "1.8.0"
BUILD = 0          # set by the GitHub build (run number); 0 = built by hand
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
CHUNK = 64 * 1024
MIN_SPLIT = 256 * 1024          # never split a part smaller than 2x this
MAX_CONNECTIONS = 32            # per host
MAX_TOTAL_CONNECTIONS = 96      # across all mirrors of one file
STALL_SECONDS = 5               # no data this long = reconnect
SOCKET_TIMEOUT = 30
LISTEN_PORT = 8721
# Which browser downloads the extension hands to the app (IDM-style rules).
# The extension keeps a copy; whichever side changed last wins.
DEFAULT_CAPTURE_TYPES = ("zip rar 7z tar gz bz2 xz zst iso img bin cue exe msi msix appx "
                         "apk xapk apks obb jar dmg deb rpm mp4 mkv avi mov wmv flv webm "
                         "m4v mpg mpeg 3gp mp3 m4a aac flac wav ogg opus wma")
CAPTURE_KEYS = ("browser_capture", "capture_types", "capture_min_kb", "capture_exclude",
                "capture_updated")
# How long a caught download waits for the Add dialog before the browser keeps it.
DECISION_WAIT = 240
NATIVE_HOST = "com.jmbuilds.jmdm"
CHROME_EXTENSION_ID = "lmaohoejkhjffbfcngbcjhidlcefefdi"     # fixed by the "key" in extension/manifest.json
FIREFOX_EXTENSION_ID = "jmdm@jmbuilds"

VIDEO_SITES = ("youtube.com", "youtu.be", "facebook.com", "fb.watch", "fb.com",
               "tiktok.com", "instagram.com", "twitter.com", "x.com",
               "vimeo.com", "dailymotion.com", "twitch.tv", "reddit.com",
               "bilibili.com", "soundcloud.com")

FILE_EXTS = {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "exe", "msi", "iso",
             "img", "bin", "apk", "xapk", "dmg", "deb", "rpm", "jar", "mp4",
             "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v", "mp3", "m4a",
             "flac", "wav", "aac", "ogg", "pdf", "epub", "docx", "xlsx", "pptx",
             "psd", "torrent", "001", "002", "part1"}
