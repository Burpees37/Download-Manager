"""Google Drive / Docs links and Drive's in-between pages."""
import pytest

from conftest import content, wait_for

from jmdm_app import gates, gdrive

ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
D = gdrive.DIRECT


@pytest.mark.parametrize("link", [
    f"https://drive.google.com/file/d/{ID}/view?usp=sharing",
    f"https://drive.google.com/file/d/{ID}/view?usp=drive_link",
    f"https://drive.google.com/file/d/{ID}/edit",
    f"https://drive.google.com/file/d/{ID}/preview",
    f"https://drive.google.com/file/u/1/d/{ID}/view",
    f"https://drive.google.com/open?id={ID}",
    f"https://drive.google.com/uc?id={ID}&export=download",
    f"https://drive.google.com/uc?export=download&id={ID}",
    f"https://docs.google.com/uc?id={ID}",
    f"https://docs.google.com/file/d/{ID}/edit",
    f"https://drive.usercontent.google.com/download?id={ID}&export=download",
])
def test_every_file_link_becomes_the_direct_one(link):
    assert gdrive.direct_link(link) == f"{D}?id={ID}&export=download&confirm=t"
    assert gdrive.looks_like_file_link(link) and gates.looks_like_download(link)


def test_resourcekey_and_browser_links_are_kept():
    old = f"https://drive.google.com/file/d/{ID}/view?resourcekey=0-abcDEF"
    assert gdrive.direct_link(old) == f"{D}?id={ID}&export=download&confirm=t&resourcekey=0-abcDEF"
    caught = f"{D}?id={ID}&export=download&authuser=0&confirm=t&uuid=1234-ab"
    assert gdrive.direct_link(caught) == caught                     # what the browser used


def test_google_docs_exports():
    e = gdrive.direct_link
    assert e(f"https://docs.google.com/document/d/{ID}/edit") == \
        f"https://docs.google.com/document/d/{ID}/export?format=docx"
    assert e(f"https://docs.google.com/spreadsheets/d/{ID}/edit#gid=0").endswith("format=xlsx")
    assert e(f"https://docs.google.com/presentation/d/{ID}/edit").endswith("format=pptx")
    pdf = f"https://docs.google.com/document/d/{ID}/export?format=pdf"
    assert e(pdf) == pdf


def test_not_drive():
    assert gdrive.direct_link("https://example.com/file/d/xyz") is None
    assert gdrive.direct_link("https://drive.google.com/drive/my-drive") is None
    assert not gdrive.looks_like_file_link("https://drive.google.com/drive/my-drive")


def test_folder_is_explained():
    url = f"https://drive.google.com/drive/folders/{ID}?usp=sharing"
    assert gdrive.is_folder(url) and gates.looks_like_download(url)
    with pytest.raises(IOError, match="folder"):
        gates.resolve_link(url)


@pytest.fixture
def drive_server(server, monkeypatch):
    """The local test server, treated as if it were Google Drive."""
    real = gdrive.is_drive_host
    monkeypatch.setattr(gdrive, "is_drive_host",
                        lambda u: "127.0.0.1" in u or real(u))
    return server


SCAN = """<html><body><div>Google Drive can't scan this file for viruses.</div>
<form id="download-form" action="/file/big.zip" method="get">
<input type="submit" id="uc-download-link" class="goog-inline-block jfk-button" value="Download anyway"/>
<input type="hidden" name="size" value="300000"><input type="hidden" name="seed" value="g">
<input type="hidden" name="name" value="Big Game.zip"><input type="hidden" name="confirm" value="t">
</form><a href="https://support.google.com/drive">Learn more</a></body></html>"""


def test_virus_scan_page_leads_to_the_file(drive_server, mgr):
    drive_server.httpd.pages["scan"] = SCAN
    r = gates.resolve_link(drive_server.url("/page?p=scan"))
    assert "/file/big.zip?" in r["url"] and r["info"]["size"] == 300000
    d = mgr.add(drive_server.url("/page?p=scan"))
    assert wait_for(d) == "Complete", d.error
    assert d.filename == "Big Game.zip"
    with open(d.path, "rb") as fh:
        assert fh.read() == content(300000, "g")


@pytest.mark.parametrize("page,expect", [
    ("<html><title>Google Drive - Quota exceeded</title><p>Sorry, you can't view or download "
     "this file at this time.</p><p>Too many users have viewed or downloaded this file "
     "recently.</p></html>", "limit"),
    ("<html><body>You need access<br>Request access, or switch to an account with access. "
     "Google Drive</body></html>", "private"),
    ("<html><body>Sorry, the file you have requested does not exist.</body></html>", "deleted"),
])
def test_drive_pages_are_never_saved_as_files(drive_server, mgr, page, expect):
    drive_server.httpd.pages["p"] = page
    with pytest.raises(IOError, match=expect):
        gates.resolve_link(drive_server.url("/page?p=p"))
    d = mgr.add(drive_server.url("/page?p=p"))
    assert wait_for(d) == "Error" and expect in d.error


def test_other_sites_pages_are_not_misread(server):
    server.httpd.pages["q"] = "<html>Too many users have viewed or downloaded this file</html>"
    with pytest.raises(IOError, match="web page, not a file"):
        gates.resolve_link(server.url("/page?p=q"))          # not Drive: generic message


def test_drive_downloads_use_at_most_8_connections(mgr):
    d = mgr.add(f"https://drive.google.com/file/d/{ID}/view", connections=32, start=False)
    assert d.connections == 8
    assert d.url == f"{D}?id={ID}&export=download&confirm=t"
