"""The download engine, against a local server, checked byte-for-byte."""
import os
import time

from conftest import content, wait_for

from jmdm_app import manager

MB = 1024 * 1024


def read(d):
    with open(d.path, "rb") as fh:
        return fh.read()


def test_multi_connection_download(mgr, server):
    url = server.url(f"/file/game.rar?size={3 * MB}&seed=g")
    d = mgr.add(url, connections=8)
    assert wait_for(d) == "Complete", d.error
    assert read(d) == content(3 * MB, "g")
    assert os.path.basename(d.path) == "game.rar"
    assert len([r for r in server.httpd.ranges_seen if r]) >= 2   # really used parts


def test_server_file_name(mgr, server):
    d = mgr.add(server.url(f"/file/dl?size={MB}&name=Setup%201.2.exe"))
    assert wait_for(d) == "Complete", d.error
    assert os.path.basename(d.path) == "Setup 1.2.exe"


def test_host_without_ranges(mgr, server):
    url = server.url(f"/file/one.bin?size={2 * MB}&ranges=0&seed=n")
    d = mgr.add(url, connections=8)
    assert wait_for(d) == "Complete", d.error
    assert read(d) == content(2 * MB, "n")


def test_pause_and_resume(mgr, server):
    url = server.url(f"/file/slow.bin?size={2 * MB}&cap=262144&seed=p")
    d = mgr.add(url, connections=4)
    t = time.time()
    while d.downloaded < 300 * 1024 and time.time() - t < 20:
        time.sleep(0.05)
    d.pause()
    assert wait_for(d, ("Paused", "Error", "Complete")) == "Paused", d.error
    got = d.downloaded
    assert 0 < got < 2 * MB
    d.start()
    assert wait_for(d) == "Complete", d.error
    assert read(d) == content(2 * MB, "p")


def test_stalled_connection_is_replaced(mgr, server):
    url = server.url(f"/file/stall.bin?size={2 * MB}&stall=1&seed=s")
    d = mgr.add(url, connections=4)
    assert wait_for(d, timeout=40) == "Complete", d.error
    assert read(d) == content(2 * MB, "s")


def test_mirrors_both_used(mgr, server, server2):
    q = f"?size={4 * MB}&cap=1048576&seed=m"
    d = mgr.add(server.url("/file/big.bin" + q), connections=4,
                mirrors=[server2.url("/file/big.bin" + q)])
    assert wait_for(d) == "Complete", d.error
    assert read(d) == content(4 * MB, "m")
    assert server.hits > 0 and server2.hits > 0


def test_mirror_with_other_size_is_skipped(mgr, server, server2):
    d = mgr.add(server.url(f"/file/a.bin?size={MB}&seed=x"), connections=2,
                mirrors=[server2.url(f"/file/a.bin?size={MB + 5}&seed=x")])
    assert wait_for(d) == "Complete", d.error
    assert read(d) == content(MB, "x")
    assert "mirror" in d.note.lower()


def test_saved_list_round_trip(tmp_path, server):
    m = manager.Manager(data_dir=str(tmp_path / "data"), persist=True)
    m.settings["download_dir"] = str(tmp_path / "dl")
    d = m.add(server.url(f"/file/k.bin?size={MB}"), start=False)
    m.save()
    m.shutdown()
    m2 = manager.Manager(data_dir=str(tmp_path / "data"), persist=True)
    try:
        again = [x for x in m2.downloads if x.id == d.id]
        assert again and again[0].url == d.url and again[0].status == "Paused"
    finally:
        m2.shutdown()
