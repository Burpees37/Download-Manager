"""What the app hands to yt-dlp for a video, without touching the network."""
import pytest

from jmdm_app import video


class FakeYDL:
    seen = []

    def __init__(self, opts):
        FakeYDL.seen.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        raise RuntimeError("stop here")                        # we only want the options


class FakeModule:
    YoutubeDL = FakeYDL


@pytest.fixture
def opts_for(mgr, monkeypatch, tmp_path):
    monkeypatch.setattr(video, "load_ytdlp", lambda: FakeModule)

    def run(url, cookie="SID=secret; HSID=x", ua="Mozilla/5.0 Brave"):
        FakeYDL.seen.clear()
        d = video.VideoDownload(mgr, url, str(tmp_path), "", 4, "", cookie, ua)
        d._run()
        return FakeYDL.seen[-1]["http_headers"]
    return run


def test_youtube_from_the_browser_is_sent_signed_out(opts_for):
    # The Download video button passes the browser's YouTube cookies; with them
    # yt-dlp needs Deno and fails. It must look exactly like a pasted link.
    for url in ("https://www.youtube.com/watch?v=C61_2MutmAU",
                "https://youtu.be/C61_2MutmAU",
                "https://www.youtube-nocookie.com/embed/C61_2MutmAU"):
        h = opts_for(url)
        assert "Cookie" not in h, url
        assert h["User-Agent"] == video.UA, url


def test_other_sites_keep_the_login(opts_for):
    h = opts_for("https://www.facebook.com/watch?v=123")
    assert h["Cookie"] == "SID=secret; HSID=x"
    assert h["User-Agent"] == "Mozilla/5.0 Brave"
