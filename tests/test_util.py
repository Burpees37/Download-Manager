import base64

from jmdm_app import gates, series, util


def test_human_size_and_time():
    assert util.human_size(None) == "?"
    assert util.human_size(512) == "512 B"
    assert util.human_size(1536) == "1.50 KB"
    assert util.human_size(5 * 1024 ** 3) == "5.00 GB"
    assert util.human_time(0) == ""
    assert util.human_time(59) == "59s"
    assert util.human_time(130) == "2m 10s"
    assert util.human_time(3725) == "1h 02m"


def test_sanitize():
    assert util.sanitize("Show: Season 1") == "Show - Season 1"
    assert util.sanitize('a/b\\c*?"<>|.mp4') == "a_b_c_.mp4"
    assert util.sanitize("  ...  ") == "download"


def test_filename_from_headers():
    f = util.filename_from_headers
    assert f({"Content-Disposition": 'attachment; filename="game.part1.rar"'}, "http://x/dl") \
        == "game.part1.rar"
    assert f({"Content-Disposition": "attachment; filename*=UTF-8''caf%C3%A9.zip"}, "http://x") \
        == "café.zip"
    assert f({}, "http://x/files/My%20Setup.exe?token=1") == "My Setup.exe"
    assert f({}, "http://x/") == "download"


def test_unique_path(tmp_path):
    (tmp_path / "a.zip").write_bytes(b"")
    (tmp_path / "a (1).zip.part").write_bytes(b"")
    assert util.unique_path(str(tmp_path), "a.zip").endswith("a (2).zip")


def test_video_and_playlist_links():
    assert util.is_video_url("https://www.youtube.com/watch?v=abc")
    assert util.is_video_url("https://m.facebook.com/x/videos/1")
    assert not util.is_video_url("https://notyoutube.com/watch?v=abc")
    assert util.is_playlist_url("https://www.youtube.com/playlist?list=PL1")
    assert util.is_playlist_url("https://www.youtube.com/@someone/videos")
    assert util.is_whole_playlist_url("https://www.youtube.com/playlist?list=PL1")
    assert not util.is_whole_playlist_url("https://www.youtube.com/watch?v=a&list=PL1")


def test_looks_like_download():
    assert gates.looks_like_download("https://host.com/files/game.part2.rar")
    assert gates.looks_like_download("https://youtu.be/abc")
    assert gates.looks_like_download("https://site.com/gate.php?id=4")
    assert not gates.looks_like_download("https://site.com/about")
    assert not gates.looks_like_download("not a link")


def test_gate_links():
    real = "https://cdn.host.com/file.zip"
    b64 = base64.b64encode(real.encode()).decode()
    assert gates.gate_target(f"https://s.com/go.php?url={real}") == real
    assert gates.gate_target(f"https://s.com/out.php?id=1&l={b64}") == real
    assert gates.gate_target(f"https://s.com/out.php?x={real.encode().hex()}") == real
    assert gates.is_gate_url("https://s.com/gate.php?id=9")
    assert not gates.is_gate_url("https://s.com/files/a.zip")


def test_episodes_and_items():
    base = "https://anime.example/show/"
    links = [(f"/watch.php?ep={n}", str(n), {}) for n in range(1, 7)]
    links += [("/tag/2024", "2024", {}), ("/comments?page=2", "2", {})]
    eps = series.find_episodes(links, base)
    assert [n for n, _ in eps] == [1, 2, 3, 4, 5, 6]
    assert eps[0][1] == "https://anime.example/watch.php?ep=1"
    assert series.parse_items("1-3,5", range(1, 7)) == {1, 2, 3, 5}
    assert series.parse_items("", [1, 2]) == {1, 2}
