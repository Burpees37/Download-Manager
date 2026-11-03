"""Gate links: redirects with cookies, meta refresh, JavaScript redirects."""
from conftest import content, wait_for

from jmdm_app import gates

KB = 1024


def test_redirect_keeps_session_cookie(server):
    target = server.url(f"/file/x.zip?size={64 * KB}&cookie=session=abc123")
    r = gates.resolve_link(server.url("/redirect?to=" + target.replace("&", "%26")))
    assert r["url"] == target
    assert "session=abc123" in r["cookie"]
    assert r["info"]["size"] == 64 * KB


def test_meta_refresh_and_js(server):
    target = server.url(f"/file/y.zip?size={32 * KB}")
    for page in ("/meta", "/js"):
        r = gates.resolve_link(server.url(page + "?to=" + target.replace("&", "%26")))
        assert r["url"] == target, page


def test_gate_link_downloads(mgr, server):
    target = server.url(f"/file/z.zip?size={256 * KB}&seed=z")
    d = mgr.add(server.url("/redirect?to=" + target.replace("&", "%26")))
    assert wait_for(d) == "Complete", d.error
    with open(d.path, "rb") as fh:
        assert fh.read() == content(256 * KB, "z")
