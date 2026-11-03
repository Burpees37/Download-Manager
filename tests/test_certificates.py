"""HTTPS uses Windows' certificates plus the bundled certifi list."""
import ssl

import pytest

from jmdm_app import net

certifi = pytest.importorskip("certifi")


def test_bundled_list_is_added():
    plain = ssl.create_default_context().cert_store_stats()["x509_ca"]
    ours = net.make_ssl_context().cert_store_stats()["x509_ca"]
    assert ours >= 100 and ours >= plain


def test_every_connection_uses_it(monkeypatch):
    monkeypatch.setattr(ssl, "_create_default_https_context", ssl._create_default_https_context)
    net.use_bundled_certificates()
    import http.client
    conn = http.client.HTTPSConnection("example.com")         # no context given
    assert conn._context.cert_store_stats()["x509_ca"] >= 100
