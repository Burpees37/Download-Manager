"""Categories, queues and the scheduler."""
import datetime
import os
import time

from conftest import content, wait_for

from jmdm_app import manager, schedule

MB = 1024 * 1024
T = datetime.datetime


def test_categories():
    c = schedule.category_of
    assert c("game.part03.rar") == "Compressed"
    assert c("game.part3") == "Compressed"
    assert c("", "https://x.com/dl/setup.exe?x=1") == "Programs"
    assert c("app.apk") == "Programs"
    assert c("song.flac") == "Music"
    assert c("manual.pdf") == "Documents"
    assert c("clip.mkv") == "Videos"
    assert c("", "https://youtube.com/watch?v=1", video=True) == "Videos"
    assert c("readme") == "Other" and c("x.weird") == "Other"


def test_queues_are_cleaned():
    qs = schedule.clean_queues([{"id": "t", "name": "Tonight", "max_active": "99",
                                 "days": [0, 9, "x"], "when_done": "explode",
                                 "start_time": "25:00"}, "junk"])
    assert [q["id"] for q in qs] == ["main", "t"]
    t = qs[1]
    assert t["max_active"] == 10 and t["days"] == [0]
    assert t["when_done"] == "none" and t["start_time"] == "01:00"
    assert schedule.clean_queues(None)[0]["id"] == "main"


def test_schedule_fires_once_at_the_right_time():
    q = schedule.new_queue("Night")
    q.update(id="n", start_enabled=True, start_time="01:00", days=[0],   # Mondays
             stop_enabled=True, stop_time="07:00")
    mon = T(2026, 11, 2)                                                 # a Monday
    ev = schedule.schedule_events
    assert ev([q], mon.replace(hour=0, minute=59, second=30),
              mon.replace(hour=1, minute=0, second=1)) == [("n", "start")]
    assert ev([q], mon.replace(hour=1, minute=0, second=1),
              mon.replace(hour=1, minute=0, second=2)) == []             # not twice
    assert ev([q], mon.replace(hour=6, minute=59), mon.replace(hour=7, minute=1)) == \
        [("n", "stop")]
    tue = mon + datetime.timedelta(days=1)
    assert ev([q], tue.replace(hour=0, minute=59), tue.replace(hour=1, minute=1)) == []
    # the PC slept through it: don't fire hours late
    assert ev([q], mon.replace(hour=0), mon.replace(hour=8)) == []
    # across midnight
    q["start_time"] = "00:00"
    sun = mon - datetime.timedelta(days=1)
    assert ev([q], sun.replace(hour=23, minute=59, second=59), mon.replace(second=1)) == \
        [("n", "start")]


def test_describe():
    q = schedule.new_queue("Q")
    assert schedule.describe(q) == "starts when you press Start"
    q.update(start_enabled=True, days=[0, 1, 2, 3, 4], start_time="02:30")
    assert schedule.describe(q) == "weekdays at 02:30"


def add_queue(mgr, name, limit=1):
    q = schedule.new_queue(name, limit)
    mgr.settings["queues"].append(q)
    return q["id"]


def test_other_queue_waits_until_started(mgr, server):
    qid = add_queue(mgr, "Tonight", limit=1)
    a = mgr.add(server.url(f"/file/a.bin?size={MB}&cap=2097152&seed=a"), queue=qid)
    b = mgr.add(server.url(f"/file/b.bin?size={MB}&cap=2097152&seed=b"), queue=qid)
    m = mgr.add(server.url(f"/file/m.bin?size={MB}&seed=m"))                # main queue
    assert wait_for(m) == "Complete"
    assert a.status == "Queued" and b.status == "Queued"                   # still waiting
    mgr.start_queue(qid)
    t = time.time()
    while a.status == "Queued" and b.status == "Queued" and time.time() - t < 10:
        time.sleep(0.05)
    time.sleep(0.3)
    assert [a.status, b.status].count("Downloading") == 1                   # 1 at a time
    assert wait_for(a) == "Complete" and wait_for(b) == "Complete"
    for d, seed in ((a, "a"), (b, "b")):
        with open(d.path, "rb") as fh:
            assert fh.read() == content(MB, seed)
    t = time.time()
    while mgr.events.empty() and time.time() - t < 5:
        time.sleep(0.05)
    assert mgr.events.get_nowait() == ("queue_done", qid)
    assert not mgr.queue_running.get(qid)


def test_starting_an_empty_queue_does_nothing(mgr):
    qid = add_queue(mgr, "Empty")
    mgr.start_queue(qid)
    time.sleep(1.2)
    assert mgr.events.empty() and not mgr.queue_running.get(qid)


def test_stop_queue_puts_files_back(mgr, server):
    qid = add_queue(mgr, "Night", limit=2)
    d = mgr.add(server.url(f"/file/s.bin?size={4 * MB}&cap=262144&seed=s"), queue=qid,
                connections=2)
    mgr.start_queue(qid)
    assert wait_for(d, ("Downloading",), timeout=10)
    while d.downloaded == 0:
        time.sleep(0.05)
    mgr.stop_queue(qid)
    t = time.time()
    while d.status != "Queued" and time.time() - t < 10:
        time.sleep(0.05)
    assert d.status == "Queued" and not mgr.queue_running.get(qid)
    time.sleep(1)
    assert d.status == "Queued"                                            # stays waiting
    mgr.start_queue(qid)
    assert wait_for(d) == "Complete", d.error
    with open(d.path, "rb") as fh:
        assert fh.read() == content(4 * MB, "s")


def test_schedule_starts_queue(mgr, server):
    qid = add_queue(mgr, "Timed")
    q = mgr.queue(qid)
    q.update(start_enabled=True, start_time="03:00", days=list(range(7)))
    d = mgr.add(server.url(f"/file/t.bin?size={MB}&seed=t"), queue=qid)
    day = T(2026, 11, 4)
    mgr._sched_last = day.replace(hour=2, minute=59, second=59)
    mgr._tick_schedule(day.replace(hour=3, minute=0, second=1))
    assert mgr.queue_running.get(qid)
    assert wait_for(d) == "Complete"


def test_deleting_a_queue_moves_files_to_main(mgr, server):
    qid = add_queue(mgr, "Gone")
    d = mgr.add(server.url(f"/file/g.bin?size={MB}"), queue=qid)
    mgr.delete_queue(qid)
    assert d.queue == "main" and wait_for(d) == "Complete"
    mgr.delete_queue("main")
    assert mgr.queue("main")                                                # can't delete


def test_queue_and_category_folders_saved(tmp_path, server):
    m = manager.Manager(data_dir=str(tmp_path / "data"), persist=True)
    m.settings["download_dir"] = str(tmp_path / "dl")
    m.settings["category_folders"] = True
    qid = add_queue(m, "Later")
    d = m.add(server.url("/file/setup.exe?size=1000"), queue=qid, start=False)
    assert d.folder == os.path.join(str(tmp_path / "dl"), "Programs")
    m.save()
    m.shutdown()
    m2 = manager.Manager(data_dir=str(tmp_path / "data"), persist=True)
    try:
        again = next(x for x in m2.downloads if x.id == d.id)
        assert again.queue == qid and m2.queue(qid)["name"] == "Later"
    finally:
        m2.shutdown()


def test_split_archive_parts_are_compressed():
    for name in ("game.7z.001", "game.zip.002", "game.r00", "game.z01", "game.part12"):
        assert schedule.category_of(name) == "Compressed", name
