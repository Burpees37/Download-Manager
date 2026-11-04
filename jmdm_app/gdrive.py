"""Google Drive and Google Docs links.

Turns every kind of Drive link into the link that sends the file itself, and
recognises Drive's in-between pages (virus-scan warning, quota, no access) so
they are answered properly instead of being saved as a "file".

Links handled:
  drive.google.com/file/d/ID/view|edit|preview (also /file/u/0/d/ID/…)
  drive.google.com/open?id=ID, drive.google.com/uc?id=ID&export=download
  docs.google.com/uc?id=ID, docs.google.com/file/d/ID/…
  drive.usercontent.google.com/download?id=ID…  (already direct)
  docs.google.com/document|spreadsheets|presentation|drawings/d/ID/…  -> an export
  drive.google.com/drive/folders/ID  -> explained: download it from the browser
Old shared links carry ?resourcekey=…, which is passed on.
"""
import re
import urllib.parse

DIRECT = "https://drive.usercontent.google.com/download"
_ID = r"([-\w]{20,})"
# Google's own file types have no file to download; pick the usual format.
DOCS_EXPORT = {"document": "docx", "spreadsheets": "xlsx", "presentation": "pptx",
               "drawings": "png"}
# Sites whose downloads are caught whatever the file type (extension + app share this).
DRIVE_SITES = "drive.google.com docs.google.com usercontent.google.com"
MAX_CONNECTIONS = 8          # Drive starts refusing ("too many requests") above this


def _host(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def is_drive_host(url):
    h = _host(url)
    return h in ("drive.google.com", "docs.google.com") or \
        h.endswith(".usercontent.google.com") or h.endswith("-docs.googleusercontent.com")


def drive_id(url):
    """(file id, resourcekey) from any Drive file link, or (None, None)."""
    p = urllib.parse.urlsplit(url)
    h = (p.hostname or "").lower()
    if h not in ("drive.google.com", "docs.google.com", "drive.usercontent.google.com"):
        return None, None
    q = urllib.parse.parse_qs(p.query)
    key = (q.get("resourcekey") or [None])[0]
    m = re.match(r"^/file(?:/u/\d+)?/d/" + _ID, p.path)
    if m:
        return m.group(1), key
    if p.path in ("/open", "/uc", "/download") and q.get("id"):
        fid = q["id"][0]
        return (fid, key) if re.fullmatch(r"[-\w]{20,}", fid) else (None, None)
    return None, None


def is_folder(url):
    p = urllib.parse.urlsplit(url)
    return (p.hostname or "").lower() == "drive.google.com" and \
        bool(re.match(r"^/drive(?:/u/\d+)?/(?:folders|mobile/folders)/", p.path))


def docs_export(url):
    """docs.google.com/document/d/ID/edit -> its .docx export link (etc.), or None."""
    p = urllib.parse.urlsplit(url)
    if (p.hostname or "").lower() != "docs.google.com":
        return None
    m = re.match(r"^/(document|spreadsheets|presentation|drawings)(?:/u/\d+)?/d/" + _ID
                 + r"(/export)?", p.path)
    if not m:
        return None
    if m.group(3):
        return url                                  # already an export link
    return (f"https://docs.google.com/{m.group(1)}/d/{m.group(2)}/export"
            f"?format={DOCS_EXPORT[m.group(1)]}")


def direct_link(url):
    """The link that sends the file itself, or None if this isn't a Drive/Docs
    file link. confirm=t skips Drive's "can't scan for viruses" page."""
    exp = docs_export(url)
    if exp:
        return exp
    fid, key = drive_id(url)
    if not fid:
        return None
    if _host(url) == "drive.usercontent.google.com" and "confirm=" in url:
        return url                                  # what the browser used: keep it
    q = {"id": fid, "export": "download", "confirm": "t"}
    if key:
        q["resourcekey"] = key
    return DIRECT + "?" + urllib.parse.urlencode(q)


def looks_like_file_link(url):
    return bool(drive_id(url)[0] or docs_export(url) or is_folder(url))


# ---- Drive's in-between pages ------------------------------------------------
FOLDER_MSG = ("This is a Google Drive folder. Open it in your browser and use "
              "Download (⋮ → Download): Drive packs it into a ZIP and the extension "
              "catches that.")
QUOTA_MSG = ("Google Drive: download limit reached for this file (too many people "
             "downloaded it recently). Try again in a few hours, or add it to your own "
             "Drive (Add shortcut / Make a copy) and download your copy.")
ACCESS_MSG = ("Google Drive: this file is private. Open it in your browser while signed in "
              "and click Download — the extension catches it with your login.")
GONE_MSG = "Google Drive: the file was deleted, or the link is wrong."


def page_problem(url, text, started_from=""):
    """If a Drive/Docs answer is one of Drive's pages instead of the file,
    the message to show; else None. Only for Google links, so other sites'
    pages are never misread."""
    if not (is_drive_host(url) or is_drive_host(started_from)
            or _host(url) == "accounts.google.com"):
        return None
    low = text[:400000].lower()
    if _host(url) == "accounts.google.com" or "servicelogin" in low[:20000] and \
            "accounts.google.com" in low[:20000]:
        return ACCESS_MSG
    if "too many users have viewed or downloaded this file" in low or \
            "download quota" in low and "exceeded" in low:
        return QUOTA_MSG
    if "you need access" in low or "request access" in low and "google drive" in low or \
            "you need permission" in low:
        return ACCESS_MSG
    if "sorry, the file you have requested does not exist" in low or \
            ("404" in low[:3000] and "that’s an error" in low) or "file does not exist" in low:
        return GONE_MSG
    return None


def scan_form(text, page_url):
    """Drive's "can't scan this file for viruses" page has a GET form
    (id="download-form") whose hidden fields lead to the file. Returns that
    link, or None."""
    m = re.search(r"<form[^>]*id=[\"']download-form[\"'][^>]*>(.*?)</form>", text, re.S | re.I)
    if not m:
        return None
    form = m.group(0)
    am = re.search(r"action=[\"']([^\"']+)[\"']", form, re.I)
    action = urllib.parse.urljoin(page_url, am.group(1).replace("&amp;", "&")) if am else DIRECT
    fields = []
    for tag in re.findall(r"<input[^>]*>", form, re.I):
        if not re.search(r"type=[\"']hidden[\"']", tag, re.I):
            continue
        n = re.search(r"name=[\"']([^\"']+)[\"']", tag, re.I)
        v = re.search(r"value=[\"']([^\"']*)[\"']", tag, re.I)
        if n:
            fields.append((n.group(1), (v.group(1) if v else "").replace("&amp;", "&")))
    if not fields:
        return None
    sep = "&" if "?" in action else "?"
    return action + sep + urllib.parse.urlencode(fields)
