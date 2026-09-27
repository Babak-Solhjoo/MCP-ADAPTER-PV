"""UI hardening: the session key never appears in the page, strict CSP with a nonce, exact Origin match,
strict Content-Length, security headers, one folder dialog at a time."""
import http.client
import threading

import pytest

from mcp_adapter.ui import mcp_service
from mcp_adapter.ui import server as ui


@pytest.fixture
def httpd(tmp_path):
    srv = ui.serve(port=0, open_browser=False, env_path=tmp_path / ".env", block=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _raw(srv, method, path, headers=None, body=b""):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    hdrs = {"Host": f"127.0.0.1:{port}"}
    hdrs.update(headers or {})
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in hdrs.items():
        conn.putheader(k, v)
    conn.endheaders(body or None)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


def test_page_has_no_key_and_a_nonce_csp(httpd):
    resp, page = _raw(httpd, "GET", "/")
    text = page.decode("utf-8")
    assert resp.status == 200 and httpd.session_token not in text and "__TOKEN__" not in text
    csp = resp.getheader("Content-Security-Policy")
    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert f'<script nonce="{nonce}">' in text and "unsafe-inline'; connect" not in csp.split("script-src")[1][:40]
    assert "default-src 'none'" in csp and "base-uri 'none'" in csp and "form-action 'none'" in csp
    assert resp.getheader("X-Content-Type-Options") == "nosniff" and resp.getheader("Referrer-Policy") == "no-referrer"
    resp2, page2 = _raw(httpd, "GET", "/")
    assert resp2.getheader("Content-Security-Policy") != csp, "a fresh nonce per response"


def test_origin_must_match_exactly_and_body_length_is_checked(httpd):
    token = httpd.session_token
    port = httpd.server_address[1]
    ok, _ = _raw(httpd, "GET", "/api/mcp/status", {ui.TOKEN_HEADER: token, "Origin": f"http://127.0.0.1:{port}"})
    assert ok.status == 200
    other, _ = _raw(httpd, "GET", "/api/mcp/status", {ui.TOKEN_HEADER: token, "Origin": "http://localhost:1"})
    assert other.status == 403, "another local port is another origin"
    neg, _ = _raw(httpd, "POST", "/api/save", {ui.TOKEN_HEADER: token, "Content-Length": "-1"})
    assert neg.status == 413
    bad, _ = _raw(httpd, "POST", "/api/save", {ui.TOKEN_HEADER: token, "Content-Length": "abc"})
    assert bad.status == 400
    weird, _ = _raw(httpd, "GET", "/api/mcp/status", {ui.TOKEN_HEADER: "tök€n".encode().decode("latin-1")})
    assert weird.status == 403, "non-ASCII tokens are refused cleanly"


def test_folder_dialog_is_not_stacked(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow(initial, timeout):
        started.set()
        release.wait(5)
        return {"ok": True, "path": "", "cancelled": True}

    monkeypatch.setattr(mcp_service, "_pick_folder", slow)
    t = threading.Thread(target=mcp_service.pick_folder)
    t.start()
    assert started.wait(5)
    assert mcp_service.pick_folder()["error"] == "a folder dialog is already open"
    release.set()
    t.join(5)
    assert mcp_service.pick_folder()["ok"] is True
