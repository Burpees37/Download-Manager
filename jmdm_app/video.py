"""Video sites through yt-dlp, made playable with ffmpeg."""
import os
import re
from .config import UA
from .util import find_ffmpeg, sanitize
from .updates import load_ytdlp
from .downloader import Download

class _Abort(Exception):
    pass


class VideoDownload(Download):
    kind = "video"

    def __init__(self, *a, quality="best", name_prefix="", **kw):
        super().__init__(*a, **kw)
        self.quality = quality
        self.name_prefix = name_prefix      # "007 - " for playlist items
        # a name chosen by the user, or the page title for streams caught on
        # sites yt-dlp doesn't know (otherwise they'd be called "index.mp4")
        name = sanitize(self.filename) if self.filename else ""
        if re.search(r"\.(mp4|mkv|webm|m4v|mov|m4a|mp3)$", name, re.I):
            name = name.rsplit(".", 1)[0]
        self.custom_name = name

    def to_dict(self):
        d = super().to_dict()
        d["quality"] = self.quality
        d["name_prefix"] = self.name_prefix
        d["custom_name"] = self.custom_name
        return d

    @classmethod
    def from_dict(cls, manager, d):
        obj = super().from_dict(manager, d)
        obj.quality = d.get("quality", "best")
        obj.name_prefix = d.get("name_prefix", "")
        obj.custom_name = d.get("custom_name", "")
        return obj

    def add_mirror(self, *a, **kw):
        return False

    def eta(self):
        return getattr(self, "_eta", None)

    def _run(self):
        try:
            yt_dlp = load_ytdlp()
        except Exception:
            self.status = "Error"
            self.error = "yt-dlp is not installed (pip install yt-dlp)"
            self.m.mark_dirty()
            return
        ffmpeg = find_ffmpeg()
        q = self.quality
        height = {"best": 1080, "1080p": 1080, "720p": 720, "480p": 480,
                  "8K": 4320, "4K": 2160, "1440p": 1440}.get(q)
        if q == "audio":
            fmt = "ba[ext=m4a]/ba/b"
        elif ffmpeg:
            # Prefer H.264 video + AAC (m4a) audio: plays in every Windows player.
            # YouTube's top picks are VP9/AV1 + Opus, and Opus inside .mp4 is the
            # "audio not supported" error in Movies & TV / Windows Media Player.
            # "max" takes the highest resolution (4K/8K are VP9/AV1 only) but
            # still uses AAC audio.
            h = f"[height<={height}]" if height else ""
            if q in ("max", "8K", "4K", "1440p"):
                # Above 1080p YouTube only has VP9/AV1 video (8K is AV1).
                fmt = f"bv*{h}+ba[ext=m4a]/bv*{h}+ba/b{h}/b"
            else:
                fmt = (f"bv*{h}[vcodec^=avc1]+ba[ext=m4a]/bv*{h}+ba[ext=m4a]/"
                       f"bv*{h}+ba/b{h}/b")
        else:
            # without ffmpeg we can only take files that already have sound
            fmt = (f"b[height<={height}][ext=mp4]/b[height<={height}]/b" if height
                   else "b[ext=mp4]/b")
        headers = {"User-Agent": self.user_agent or UA}
        if self.referer:
            headers["Referer"] = self.referer
        if self.cookie:
            headers["Cookie"] = self.cookie

        def hook(d):
            if self._stop.is_set():
                raise _Abort()
            st = d.get("status")
            if st == "downloading":
                self.status = "Downloading"
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                part_done = d.get("downloaded_bytes") or 0
                fname = d.get("filename") or ""
                if fname:
                    self.filename = os.path.basename(fname)
                # several streams (video + audio) are downloaded one after the other
                key = fname
                self._parts[key] = (part_done, total or 0)
                self.downloaded = sum(p[0] for p in self._parts.values())
                tot = sum(p[1] for p in self._parts.values())
                self.size = tot if tot else -1
                self._eta = d.get("eta")
                self.active_conns = self.connections
            elif st == "finished":
                self.status = "Processing"
                self.active_conns = 0

        def pp_hook(d):
            if d.get("status") == "finished":
                fp = d.get("info_dict", {}).get("filepath")
                if fp:
                    self.path = fp
                    self.filename = os.path.basename(fp)

        self._parts = {}
        self._eta = None
        opts = {
            "outtmpl": os.path.join(self.folder, self.name_prefix.replace("%", "%%")
                                   + (self.custom_name.replace("%", "%%") if self.custom_name
                                      else "%(title).150B [%(id)s]") + ".%(ext)s"),
            "format": fmt,
            "concurrent_fragment_downloads": self.connections,
            "http_chunk_size": 10 * 1024 * 1024,
            "continuedl": True,
            "noplaylist": True,
            "retries": 10,
            "fragment_retries": 10,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "windowsfilenames": True,
            "http_headers": headers,
            "progress_hooks": [hook],
            "postprocessor_hooks": [pp_hook],
        }
        if self.m.limiter.rate > 0:
            opts["ratelimit"] = self.m.limiter.rate
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg
            if q != "audio":
                opts["merge_output_format"] = "mp4"
        try:
            os.makedirs(self.folder, exist_ok=True)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=True)
                if info and not self.path:
                    self.path = ydl.prepare_filename(info)
                    self.filename = os.path.basename(self.path)
            if ffmpeg and self.path and os.path.isfile(self.path):
                self._make_playable(ffmpeg)
            if self.size is None or self.size < 0:
                self.size = self.downloaded
            self.downloaded = self.size
            self.status = "Complete"
        except _Abort:
            self.status = "Paused"
        except Exception as e:
            if self._stop.is_set():
                self.status = "Paused"
            else:
                msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).replace("ERROR: ", "")
                self.status, self.error = "Error", msg
        finally:
            self.speed = 0.0
            self.active_conns = 0
            self.m.mark_dirty()

    def update_speed(self):
        super().update_speed()

    def _make_playable(self, ffmpeg):
        """Safety net: if the saved .mp4 still has Opus/Vorbis audio (some sites
        offer nothing else), convert just the audio to AAC so Windows players
        can play it. The video is copied untouched, so this takes seconds."""
        import subprocess
        if not self.path.lower().endswith((".mp4", ".m4v", ".mov")):
            return
        flags = 0x08000000 if os.name == "nt" else 0      # no console window
        try:
            probe = subprocess.run([ffmpeg, "-hide_banner", "-i", self.path],
                                   capture_output=True, text=True, errors="replace",
                                   creationflags=flags, timeout=60)
            codecs = re.findall(r"Audio: (\w+)", probe.stderr)
            if not codecs or not any(c in ("opus", "vorbis") for c in codecs):
                return
            self.status = "Processing"
            tmp = self.path + ".aac.mp4"
            r = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                                "-i", self.path, "-map", "0", "-c", "copy",
                                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                                tmp], capture_output=True, creationflags=flags, timeout=3600)
            if r.returncode == 0 and os.path.getsize(tmp) > 0:
                os.replace(tmp, self.path)
            elif os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
