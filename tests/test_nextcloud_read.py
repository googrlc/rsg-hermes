"""Read-side WebDAV helpers on NextcloudClient (list_dir / read_file)."""
from __future__ import annotations

from hermes.integrations.nextcloud_client import NextcloudClient, NextcloudError

# A realistic PROPFIND multistatus for Clients/Acme Trucking with Depth: 1 —
# the folder itself (dropped) plus one subfolder and one file. Note the space in
# the client name is percent-encoded in the hrefs, as Nextcloud returns them.
_MULTISTATUS = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/root/Clients/Acme%20Trucking/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/root/Clients/Acme%20Trucking/COIs/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>
      <d:getlastmodified>Tue, 21 Jul 2026 10:00:00 GMT</d:getlastmodified></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/root/Clients/Acme%20Trucking/readme.txt</d:href>
    <d:propstat><d:prop><d:resourcetype/>
      <d:getcontentlength>42</d:getcontentlength></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
</d:multistatus>"""


class _Resp:
    def __init__(self, status_code=207, content=b"", ok=True):
        self.status_code = status_code
        self.content = content
        self.ok = ok
        self.text = content.decode("utf-8", "replace")


class _FakeSession:
    def __init__(self, propfind=None, get=None):
        self._propfind = propfind
        self._get = get
        self.requests = []

    def request(self, method, url, **kw):
        self.requests.append((method, url))
        return self._propfind

    def get(self, url, **kw):
        self.requests.append(("GET", url))
        return self._get


def _client(session):
    return NextcloudClient(url="https://nc.example", user="root",
                           app_password="pw", session=session)


def test_list_dir_parses_children_and_drops_self():
    c = _client(_FakeSession(propfind=_Resp(207, _MULTISTATUS)))
    out = c.list_dir("Clients/Acme Trucking")
    names = {e["name"]: e for e in out}
    assert set(names) == {"COIs", "readme.txt"}          # self entry dropped
    assert names["COIs"]["is_dir"] is True
    assert names["readme.txt"]["is_dir"] is False
    assert names["readme.txt"]["size"] == 42
    # path is relative to base_path, in the form read_file/put_file accept
    assert names["readme.txt"]["path"] == "Clients/Acme Trucking/readme.txt"
    assert names["COIs"]["path"] == "Clients/Acme Trucking/COIs"


def test_list_dir_missing_folder_returns_empty():
    c = _client(_FakeSession(propfind=_Resp(404, b"")))
    assert c.list_dir("Clients/Nobody") == []


def test_read_file_returns_bytes():
    c = _client(_FakeSession(get=_Resp(200, b"PDFDATA", ok=True)))
    assert c.read_file("Clients/Acme/COIs/x.pdf") == b"PDFDATA"


def test_read_file_404_raises():
    c = _client(_FakeSession(get=_Resp(404, b"", ok=False)))
    try:
        c.read_file("Clients/Acme/missing.pdf")
    except NextcloudError as exc:
        assert "Not found" in str(exc)
    else:
        raise AssertionError("expected NextcloudError")
