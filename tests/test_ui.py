"""Configuration UI: state/save round-trip and the loopback/token/Host hardening of its HTTP API."""
import http.client
import importlib
import json
import threading

import pytest

from mcp_adapter import config
from mcp_adapter.ui import server as ui


def test_config_store_state_and_save(tmp_path):
    env_file = tmp_path / ".env"
    store = ui.ConfigStore(env_file)
    st = store.state()
    assert len(st["apps"]) == 14 and {a["id"] for a in st["apps"]} >= {"matlab", "vivado", "drawio"}
    assert all(a["enabled"] for a in st["apps"])
    payload = {
        "apps": [{"id": "vivado", "enabled": False, "paths": [{"env": "VIVADO_EXE", "configured": "C:/x/vivado.bat"}]},
                 {"id": "matlab", "enabled": True, "paths": [{"env": "MATLAB_EXE", "configured": ""}]},
                 {"id": "bogus", "enabled": False, "paths": [{"env": "EVIL", "configured": "x"}]}],
        "settings": {"MCP_ADAPTER_NETWORK_MODE": "network", "MCP_ADAPTER_ALLOWED_HOSTS": "10.0.0.2:8000",
                     "MCP_ADAPTER_ALLOW_INTERNET": "true", "MCP_ADAPTER_TIMEOUT": "abc", "TAVILY_API": "k-123"},
    }
    st = store.save(payload)
    text = env_file.read_text(encoding="utf-8")
    assert "MCP_ADAPTER_ENABLE_VIVADO=false" in text and "VIVADO_EXE=C:/x/vivado.bat" in text
    assert "MCP_ADAPTER_ENABLE_MATLAB=true" in text and "MATLAB_EXE=" in text
    assert "EVIL" not in text and "MCP_ADAPTER_ENABLE_BOGUS" not in text
    assert "MCP_ADAPTER_NETWORK_MODE=network" in text and "MCP_ADAPTER_TIMEOUT=600" in text
    assert "TAVILY_API=k-123" in text
    vivado = next(a for a in st["apps"] if a["id"] == "vivado")
    assert vivado["enabled"] is False and vivado["paths"][0]["configured"] == "C:/x/vivado.bat"
    assert st["settings"]["TAVILY_API_SET"] is True
    # saving the mask must not overwrite the stored key
    store.save({"apps": [], "settings": {"TAVILY_API": ui.MASK}})
    assert "TAVILY_API=k-123" in env_file.read_text(encoding="utf-8")


@pytest.fixture
def ui_server(tmp_path):
    httpd = ui.serve(port=0, open_browser=False, env_path=tmp_path / ".env", block=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _request(httpd, method, path, headers=None, body=None, host=None):
    port = httpd.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)  # first call extracts program icons
    hdrs = {"Host": host or f"127.0.0.1:{port}"}
    hdrs.update(headers or {})
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8")
    conn.close()
    return resp.status, data


def _token(httpd):
    status, page = _request(httpd, "GET", "/")
    assert status == 200 and "MCP Adapter" in page
    assert httpd.session_token not in page, "the page must not carry the session key"
    return httpd.session_token


def test_http_api_requires_token_and_localhost(ui_server):
    token = _token(ui_server)
    assert len(token) > 20
    status, body = _request(ui_server, "GET", "/api/state")
    assert status == 403 and "token" in body
    status, body = _request(ui_server, "GET", "/api/state", headers={ui.TOKEN_HEADER: "wrong"})
    assert status == 403
    status, body = _request(ui_server, "GET", "/api/state", headers={ui.TOKEN_HEADER: token})
    assert status == 200 and len(json.loads(body)["apps"]) == 14
    status, _ = _request(ui_server, "GET", "/api/state", headers={ui.TOKEN_HEADER: token}, host="evil.example:80")
    assert status == 403
    status, _ = _request(ui_server, "GET", "/api/state",
                         headers={ui.TOKEN_HEADER: token, "Origin": "http://evil.example"})
    assert status == 403
    status, _ = _request(ui_server, "OPTIONS", "/api/save")
    assert status == 403
    assert ui_server.server_address[0] == "127.0.0.1"


def test_http_save_round_trip(ui_server, tmp_path):
    token = _token(ui_server)
    payload = {"apps": [{"id": "hfss", "enabled": False, "paths": []}],
               "settings": {"MCP_ADAPTER_NETWORK_MODE": "local"}}
    status, body = _request(ui_server, "POST", "/api/save", headers={ui.TOKEN_HEADER: token}, body=payload)
    assert status == 200
    data = json.loads(body)
    assert data["ok"] and next(a for a in data["state"]["apps"] if a["id"] == "hfss")["enabled"] is False
    assert "MCP_ADAPTER_ENABLE_HFSS=false" in (tmp_path / ".env").read_text(encoding="utf-8")
    status, body = _request(ui_server, "POST", "/api/check-path", headers={ui.TOKEN_HEADER: token},
                            body={"path": str(tmp_path / ".env")})
    assert status == 200 and json.loads(body)["exists"] is True


def test_app_switch_hides_tools(monkeypatch):
    monkeypatch.setenv("MCP_ADAPTER_ENABLE_MATLAB", "false")
    assert config.app_enabled("matlab") is False and config.app_enabled("vivado") is True
    from mcp_adapter import server as srv

    mod = importlib.reload(srv)
    names = {t.name for t in __import__("asyncio").new_event_loop().run_until_complete(mod.server.list_tools())}
    assert not any(n.startswith("matlab_") for n in names)
    hidden = mod.HIDDEN_TOOLS.get("matlab", [])
    assert hidden and all("switched off" in h["reason"] for h in hidden)
    monkeypatch.delenv("MCP_ADAPTER_ENABLE_MATLAB")
    importlib.reload(srv)


def test_icon_png_encoding_and_extraction(tmp_path):
    import os
    import struct
    import sys

    from mcp_adapter.ui import icons

    png = icons._png_from_rgba(2, 2, bytes([255, 0, 0, 255] * 4))
    assert png.startswith(b"\x89PNG") and struct.unpack(">II", png[16:24]) == (2, 2)
    assert icons.extract_icon_png(tmp_path / "missing.exe") is None
    assert icons.icon_data_uri(tmp_path / "missing.exe") is None
    if os.name == "nt":
        uri = icons.icon_data_uri(sys.executable)
        assert uri is None or uri.startswith("data:image/png;base64,")
    else:
        assert icons.extract_icon_png(sys.executable) is None


def test_state_carries_icon_and_color(tmp_path):
    st = ui.ConfigStore(tmp_path / ".env").state()
    for app in st["apps"]:
        assert app["color"].startswith("#")
        assert app["icon_uri"], f"{app['id']} has no icon"
        assert app["icon_source"] in ("program", "bundled")
        if app["icon_source"] == "program":
            assert app["icon_uri"].startswith("data:image/png;base64,")
        else:
            assert app["icon_uri"].startswith("data:image/svg+xml;base64,")
    assert ui.bundled_icon_uri("mcp-adapter") and ui.bundled_icon_uri("nope") is None


def test_favicon_is_served(ui_server):
    status, body = _request(ui_server, "GET", "/favicon.svg")
    assert status == 200 and str(body).lstrip().startswith("<svg")
    status, body = _request(ui_server, "GET", "/favicon.ico")
    assert status == 200 and "</svg>" in str(body)


def test_second_instance_on_same_port_is_refused(ui_server, tmp_path):
    """Two UI instances must never share a port (Windows would otherwise allow it with SO_REUSEADDR)."""
    port = ui_server.server_address[1]
    with pytest.raises(OSError):
        ui.serve(port=port, open_browser=False, env_path=tmp_path / "other.env", block=False)
