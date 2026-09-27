"""Hardening for public release: bearer token for network mode, .env value sanitising, token generation."""
import asyncio

import pytest

from mcp_adapter.http_auth import MIN_TOKEN_LENGTH, BearerTokenMiddleware, generate_token
from mcp_adapter.security import SecurityError, SecurityPolicy
from mcp_adapter.setup_wizard import write_env_updates

TOKEN = generate_token()


async def _call(app, headers, scope_type="http"):
    sent = []
    scope = {"type": scope_type, "method": "POST", "path": "/mcp", "headers": headers}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def test_middleware_requires_the_bearer_token():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})

    app = BearerTokenMiddleware(inner, TOKEN)
    run = asyncio.new_event_loop().run_until_complete
    assert run(_call(app, []))[0]["status"] == 401
    assert run(_call(app, [(b"authorization", b"Bearer wrong-token")]))[0]["status"] == 401
    assert run(_call(app, [(b"authorization", TOKEN.encode())]))[0]["status"] == 401, "scheme is required"
    assert run(_call(app, [(b"authorization", b"Bearer " + TOKEN.encode())]))[0]["status"] == 200
    assert run(_call(app, [(b"Authorization", b"bearer " + TOKEN.encode())]))[0]["status"] == 200
    assert run(_call(app, [], scope_type="websocket"))[0] == {"type": "websocket.close", "code": 1008}
    run(app({"type": "lifespan"}, None, None))
    assert seen == ["http", "http", "lifespan"], "lifespan events pass through untouched"
    with pytest.raises(ValueError):
        BearerTokenMiddleware(inner, "short")
    assert len(generate_token()) >= MIN_TOKEN_LENGTH and generate_token() != generate_token()


def test_every_http_transport_refuses_to_start_without_a_token():
    for mode in ("network", "local"):  # local too: other accounts on the same PC share 127.0.0.1
        with pytest.raises(SecurityError, match="require MCP_ADAPTER_AUTH_TOKEN"):
            SecurityPolicy(network_mode=mode).check_http_start()
        with pytest.raises(SecurityError, match="too short"):
            SecurityPolicy(network_mode=mode, auth_token="abc").check_http_start()
        SecurityPolicy(network_mode=mode, auth_token=TOKEN).check_http_start()
    d = SecurityPolicy(network_mode="local").describe()
    assert d["http_authentication"].startswith("MISSING")
    assert "required" in SecurityPolicy(network_mode="local", auth_token=TOKEN).describe()["http_authentication"]
    assert TOKEN not in repr(SecurityPolicy(auth_token=TOKEN)), "the token never shows up in reprs or logs"


def test_server_exits_in_network_mode_without_token(monkeypatch, capsys):
    import importlib

    from mcp_adapter import server as srv

    mod = importlib.reload(srv)
    monkeypatch.setattr(mod, "POLICY", SecurityPolicy(network_mode="network"))
    monkeypatch.setattr(mod, "run_server", lambda *a, **k: pytest.fail("must not start"))
    with pytest.raises(SystemExit) as exc:
        mod.main(["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8123"])
    assert exc.value.code == 2 and "MCP_ADAPTER_AUTH_TOKEN" in capsys.readouterr().err


def test_env_values_cannot_inject_settings(tmp_path):
    env = tmp_path / ".env"
    env.write_text("MCP_ADAPTER_NETWORK_MODE=local\n", encoding="utf-8")
    write_env_updates(env, {"MATLAB_EXE": "C:/x.exe\nMCP_ADAPTER_NETWORK_MODE=network\r", "BAD KEY": "1"})
    text = env.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert "MCP_ADAPTER_NETWORK_MODE=local" in lines
    assert not any(ln.startswith("MCP_ADAPTER_NETWORK_MODE=network") for ln in lines), "no injected setting line"
    assert "MATLAB_EXE=C:/x.exeMCP_ADAPTER_NETWORK_MODE=network" in text and "BAD KEY" not in text


def test_setup_wizard_generates_a_token_for_network_mode(tmp_path, monkeypatch):
    from mcp_adapter import setup_wizard

    monkeypatch.delenv("MCP_ADAPTER_AUTH_TOKEN", raising=False)
    env = tmp_path / ".env"
    setup_wizard.main(["--env-file", str(env), "--non-interactive", "--mode", "network"])
    line = next(ln for ln in env.read_text(encoding="utf-8").splitlines() if ln.startswith("MCP_ADAPTER_AUTH_TOKEN="))
    assert len(line.split("=", 1)[1]) >= MIN_TOKEN_LENGTH


def test_http_app_rejects_requests_without_token():
    testclient = pytest.importorskip("starlette.testclient")
    from mcp_adapter import server as srv
    from mcp_adapter._compat import build_http_app

    app = BearerTokenMiddleware(build_http_app(srv.server, "streamable-http", "127.0.0.1", 8000), TOKEN)
    with testclient.TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.post("/mcp", json={}).status_code == 401
        ok = client.post("/mcp", json={}, headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code != 401


def test_http_refused_without_sdk_host_protection(monkeypatch):
    from mcp_adapter import security

    monkeypatch.setattr(security, "TransportSecuritySettings", None)
    with pytest.raises(SecurityError, match="DNS-rebinding"):
        SecurityPolicy(network_mode="local", auth_token=TOKEN).check_http_start()


def test_output_folder_cannot_be_the_repository_and_workspace_not_a_root(monkeypatch, tmp_path):
    from mcp_adapter import config, workspace
    from mcp_adapter import output_folder as of

    monkeypatch.setattr(of, "protected_locations", lambda: [])
    monkeypatch.delenv("MCP_ADAPTER_WORK_DIR", raising=False)
    with pytest.raises(of.OutputFolderError, match="adapter's own folder"):
        of.check_output_folder(str(config.REPO_ROOT))
    monkeypatch.setenv("MCP_ADAPTER_WORK_DIR_ACCESS", "true")
    monkeypatch.setenv("MCP_ADAPTER_WORK_DIR", str(tmp_path))
    assert workspace.workspace_enabled()
    monkeypatch.setenv("MCP_ADAPTER_WORK_DIR", tmp_path.anchor)
    assert not workspace.workspace_enabled(), "a drive root never gets full access"
