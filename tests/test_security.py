"""Network security policy: local mode must be enforced in code, not only by the OS firewall."""
import asyncio
import importlib

import pytest

from mcp_adapter import config, docs_search, security
from mcp_adapter.security import SecurityError, SecurityPolicy, is_loopback_host
from mcp_adapter.setup_wizard import main as setup_main
from mcp_adapter.setup_wizard import write_env_updates


def test_loopback_detection():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("localhost")
    assert is_loopback_host("::1")
    assert is_loopback_host("[::1]")
    assert is_loopback_host("127.5.5.5")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("192.168.1.10")
    assert not is_loopback_host("myhost")
    assert not is_loopback_host("")


def test_local_mode_refuses_non_loopback_bind():
    policy = SecurityPolicy(network_mode="local")
    assert policy.effective_host(None) == "127.0.0.1"
    assert policy.effective_host("localhost") == "localhost"
    with pytest.raises(SecurityError):
        policy.effective_host("0.0.0.0")
    with pytest.raises(SecurityError):
        policy.effective_host("192.168.1.10")


def test_network_mode_allows_other_hosts():
    policy = SecurityPolicy(network_mode="network", allowed_hosts=["192.168.1.20:8000"])
    assert policy.effective_host("0.0.0.0") == "0.0.0.0"
    ts = policy.transport_security("0.0.0.0", 8000)
    if ts is not None:
        assert ts.enable_dns_rebinding_protection
        assert "192.168.1.20:8000" in ts.allowed_hosts


def test_local_mode_transport_security_is_loopback_only():
    ts = SecurityPolicy(network_mode="local").transport_security("127.0.0.1", 8000)
    if ts is not None:
        assert ts.enable_dns_rebinding_protection
        assert all(h.startswith(("127.0.0.1:", "localhost:", "[::1]:")) for h in ts.allowed_hosts)


def test_describe_reports_disabled_internet_tools():
    d = SecurityPolicy(network_mode="local", allow_internet=False).describe()
    assert d["network_mode"] == "local" and d["listens_on_network"] is False
    assert all("disabled" in v for v in d["internet_tools"].values())
    assert SecurityPolicy(allow_internet=True).describe()["internet_tools"]["search_docs_online"] == "enabled"


def test_env_defaults_are_local_and_offline(monkeypatch):
    monkeypatch.delenv("MCP_ADAPTER_NETWORK_MODE", raising=False)
    monkeypatch.delenv("MCP_ADAPTER_ALLOW_INTERNET", raising=False)
    assert config.network_mode() == "local"
    assert config.allow_internet() is False
    monkeypatch.setenv("MCP_ADAPTER_NETWORK_MODE", "bogus")
    assert config.network_mode() == "local"
    monkeypatch.setenv("MCP_ADAPTER_ALLOW_INTERNET", "yes")
    assert config.allow_internet() is True


def test_docs_search_blocked_when_internet_disabled(monkeypatch):
    monkeypatch.setenv("MCP_ADAPTER_ALLOW_INTERNET", "false")
    monkeypatch.setenv("TAVILY_API", "dummy")
    assert docs_search.is_enabled() is False
    out = docs_search.search_docs("anything")
    assert out["ok"] is False and "MCP_ADAPTER_ALLOW_INTERNET" in out["error"]


def test_server_omits_internet_tools_by_default(monkeypatch):
    monkeypatch.setenv("MCP_ADAPTER_ALLOW_INTERNET", "false")
    monkeypatch.setenv("MCP_ADAPTER_NETWORK_MODE", "local")
    from mcp_adapter import server as srv

    srv = importlib.reload(srv)
    names = {t.name for t in asyncio.new_event_loop().run_until_complete(srv.server.list_tools())}
    assert "security_policy" in names and "adapter_status" in names
    assert "search_docs_online" not in names
    assert "mathematica_wolfram_alpha" not in names


def test_server_registers_internet_tools_when_allowed(monkeypatch):
    monkeypatch.setenv("MCP_ADAPTER_ALLOW_INTERNET", "true")
    monkeypatch.setenv("MCP_ADAPTER_EXPOSE_UNAVAILABLE", "true")
    from mcp_adapter import server as srv

    srv = importlib.reload(srv)
    names = {t.name for t in asyncio.new_event_loop().run_until_complete(srv.server.list_tools())}
    assert {"search_docs_online", "mathematica_wolfram_alpha"} <= names
    monkeypatch.setenv("MCP_ADAPTER_ALLOW_INTERNET", "false")
    monkeypatch.setenv("MCP_ADAPTER_EXPOSE_UNAVAILABLE", "false")
    importlib.reload(srv)


def test_main_refuses_network_bind_in_local_mode(monkeypatch, capsys):
    monkeypatch.setenv("MCP_ADAPTER_NETWORK_MODE", "local")
    from mcp_adapter import server as srv

    srv = importlib.reload(srv)
    with pytest.raises(SystemExit) as exc:
        srv.main(["--transport", "streamable-http", "--host", "0.0.0.0"])
    assert exc.value.code == 2
    assert "refused to start" in capsys.readouterr().err


def test_print_policy(capsys):
    from mcp_adapter import server as srv

    srv.main(["--print-policy"])
    assert '"network_mode"' in capsys.readouterr().out


def test_wizard_writes_and_updates_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TAVILY_API=abc\nOTHER=1\n", encoding="utf-8")
    setup_main(["--non-interactive", "--mode", "network", "--allow-internet", "yes",
                "--allowed-hosts", "10.0.0.5:8000", "--env-file", str(env_file)])
    text = env_file.read_text(encoding="utf-8")
    assert "MCP_ADAPTER_NETWORK_MODE=network" in text
    assert "MCP_ADAPTER_ALLOW_INTERNET=true" in text
    assert "MCP_ADAPTER_ALLOWED_HOSTS=10.0.0.5:8000" in text
    assert "TAVILY_API=abc" in text and "OTHER=1" in text
    write_env_updates(env_file, {"MCP_ADAPTER_NETWORK_MODE": "local"})
    text = env_file.read_text(encoding="utf-8")
    assert text.count("MCP_ADAPTER_NETWORK_MODE=") == 1 and "MCP_ADAPTER_NETWORK_MODE=local" in text


def test_security_module_defaults():
    assert security.INTERNET_TOOLS == ("search_docs_online", "mathematica_wolfram_alpha")
