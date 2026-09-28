"""Chat workspace API of the UI: chats, settings, approvals, history, providers, models, MCP endpoint, folder picker."""
import asyncio
import http.client
import json
import socket
import threading
import time

import pytest

from agent_runner import service as svc
from agent_runner.chat import ChatManager, ChatSettings
from mcp_adapter.ui import mcp_service
from mcp_adapter.ui import server as ui

SEEN: list = []


async def fake_run_task(cfg):
    from agent_runner.runner import RunResult, Transcript

    SEEN.append(cfg)
    tr = Transcript(cfg)
    tr.add("status", text=f"fake {cfg.provider}/{cfg.model} access={cfg.work_dir_access} history={len(cfg.history)}")
    tr.add("assistant", text="Working on: " + cfg.task)
    tr.add("tool_call", name="time_now", input={"timezone": "UTC"})
    approved = True
    if cfg.approval_handler and cfg.approve:  # like the real runner: ask only when approvals are on
        approved = await cfg.approval_handler("time_now", {"timezone": "UTC"})
    tr.add("tool_result", name="time_now", content="approved" if approved else "declined", is_error=not approved)
    for _ in range(40):
        if cfg.stop_requested and cfg.stop_requested():
            tr.add("note", text="Stopped by the user.")
            break
        await asyncio.sleep(0.05)
    res = RunResult(final_text=f"done ({len(cfg.history)} earlier turns)", turns=2, tool_calls=1, stop_reason="end_turn")
    res.report_path = tr.save(res)
    return res


@pytest.fixture
def ui_server(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "run_task", fake_run_task)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    SEEN.clear()
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    store = ui.ConfigStore(env)
    chats = ChatManager(tmp_path / "chats", store.chat_defaults())
    mcp = mcp_service.McpHttpService()
    httpd = ui.serve(port=0, open_browser=False, env_path=env, block=False, chats=chats, mcp=mcp)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd, chats, tmp_path
    httpd.shutdown()
    httpd.server_close()
    mcp.stop()


def _request(httpd, method, path, token=None, body=None, _attempt=0):
    try:
        return _request_once(httpd, method, path, token, body)
    except (ConnectionResetError, ConnectionAbortedError):  # security software on some Windows PCs
        # drops loopback connections now and then; a short pause lets such a burst pass
        if _attempt >= 2:
            raise
        time.sleep(0.5 * (_attempt + 1))
        return _request(httpd, method, path, token, body, _attempt + 1)


def _request_once(httpd, method, path, token=None, body=None):
    port = httpd.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": f"127.0.0.1:{port}"}
    if token:
        headers[ui.TOKEN_HEADER] = token
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read().decode("utf-8") or "{}")
    conn.close()
    return resp.status, data


def _token(httpd):
    return httpd.session_token


def _wait(pred, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_chat_lifecycle_with_folder_access_and_history(ui_server):
    httpd, chats, tmp_path = ui_server
    token = _token(httpd)
    work = tmp_path / "work"
    status, data = _request(httpd, "POST", "/api/chats", token,
                            {"settings": {"work_dir": str(work), "work_dir_access": True, "approve": True,
                                          "model": "claude-sonnet-5"}})
    assert status == 200
    cid = data["chat"]["id"]
    assert data["chat"]["settings"]["work_dir_access"] is True and data["chat"]["title"] == "New chat"
    status, data = _request(httpd, "POST", f"/api/chats/{cid}/send", token, {"text": "first task"})
    assert status == 200 and data["chat"]["status"] == "running"
    chat = chats.get(cid)
    assert _wait(lambda: chat.service.pending is not None)
    status, ev = _request(httpd, "GET", f"/api/chats/{cid}/events?since=0", token)
    assert ev["pending"]["name"] == "time_now" and any(e["kind"] == "approval" for e in ev["events"])
    status, _ = _request(httpd, "POST", f"/api/chats/{cid}/decide", token, {"approve": True})
    assert status != 200, "an approval without the call id is refused"
    status, _ = _request(httpd, "POST", f"/api/chats/{cid}/decide", token, {"approve": True, "id": "0" * 16})
    assert status != 200, "a stale or wrong call id is refused"
    status, _ = _request(httpd, "POST", f"/api/chats/{cid}/decide", token, {"approve": True, "id": ev["pending"]["id"]})
    assert status == 200
    assert _wait(lambda: not chat.service.running)
    status, data = _request(httpd, "GET", f"/api/chats/{cid}", token)
    msgs = data["chat"]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["text"].startswith("done (0")
    assert data["chat"]["title"] == "first task" and data["chat"]["status"] == "finished"
    assert SEEN[-1].work_dir_access is True and str(SEEN[-1].work_dir) == str(work)
    assert (work / "agent-reports").exists()
    name = msgs[1]["report"].replace("\\", "/").split("/")[-1]
    status, rep = _request(httpd, "GET", f"/api/chats/{cid}/report?name={name}", token)
    assert status == 200 and "first task" in rep["text"]
    # second turn carries the history
    status, data = _request(httpd, "POST", f"/api/chats/{cid}/send", token,
                            {"text": "second task", "settings": {"approve": False}})
    assert status == 200
    assert _wait(lambda: not chat.service.running)
    assert SEEN[-1].history == [{"role": "user", "content": "first task"},
                                {"role": "assistant", "content": msgs[1]["text"]}]
    assert chat.messages[-1]["text"].startswith("done (2")
    # defaults remembered for new chats, persisted on disk, reloadable
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "AGENT_MODEL=claude-sonnet-5" in env_text and "AGENT_WORK_DIR_ACCESS=true" in env_text
    reloaded = ChatManager(tmp_path / "chats", ChatSettings())
    assert reloaded.get(cid).messages[-1]["text"] == chat.messages[-1]["text"]
    status, data = _request(httpd, "GET", "/api/chats", token)
    assert data["chats"][0]["id"] == cid and data["defaults"]["model"] == "claude-sonnet-5"
    status, data = _request(httpd, "POST", f"/api/chats/{cid}/delete", token, {})
    assert status == 200 and data["chats"] == []
    assert _request(httpd, "GET", f"/api/chats/{cid}", token)[0] == 404


def test_parallel_chats_and_stop(ui_server):
    httpd, chats, _ = ui_server
    token = _token(httpd)
    ids = [_request(httpd, "POST", "/api/chats", token, {})[1]["chat"]["id"] for _ in range(2)]
    for cid in ids:
        assert _request(httpd, "POST", f"/api/chats/{cid}/send", token, {"text": "go"})[0] == 200
    assert all(chats.get(c).service.running for c in ids)
    assert _request(httpd, "POST", f"/api/chats/{ids[0]}/send", token, {"text": "again"})[0] == 409
    assert _request(httpd, "POST", f"/api/chats/{ids[0]}/stop", token, {})[0] == 200
    assert _wait(lambda: not chats.get(ids[0]).service.running)
    assert chats.get(ids[0]).status == "stopped"
    assert _wait(lambda: not chats.get(ids[1]).service.running)
    assert chats.get(ids[1]).status == "finished"
    assert _request(httpd, "POST", f"/api/chats/{ids[1]}/rename", token, {"title": "renamed"})[1]["chat"]["title"] == "renamed"


def test_provider_key_required_per_chat(ui_server):
    httpd, _, _ = ui_server
    token = _token(httpd)
    cid = _request(httpd, "POST", "/api/chats", token, {"settings": {"provider": "openai", "model": "gpt-5"}})[1]["chat"]["id"]
    status, data = _request(httpd, "POST", f"/api/chats/{cid}/send", token, {"text": "x"})
    assert status == 400 and "OpenAI API key" in data["error"]
    assert _request(httpd, "POST", f"/api/chats/{cid}/send", token, {"text": ""})[0] == 400


def test_providers_status_and_models(ui_server):
    httpd, _, _ = ui_server
    token = _token(httpd)
    status, data = _request(httpd, "GET", "/api/providers/status", token)
    assert status == 200
    p = data["providers"]
    assert p["anthropic"]["key_set"] is True and p["anthropic"]["light"] in ("green", "orange")
    assert p["openai"]["key_set"] is False and p["openai"]["light"] == "red"
    status, data = _request(httpd, "GET", "/api/models/all", token)
    rows = data["models"]
    assert rows and all(r["provider"] == "anthropic" for r in rows) and any(r["model"] == "claude-opus-5" for r in rows)


def test_pick_folder_and_check_path(ui_server, monkeypatch, tmp_path):
    httpd, _, _ = ui_server
    token = _token(httpd)
    monkeypatch.setattr(ui, "pick_folder", lambda initial="": {"ok": True, "path": str(tmp_path), "cancelled": False})
    status, data = _request(httpd, "POST", "/api/pick-folder", token, {"initial": ""})
    assert status == 200 and data["path"] == str(tmp_path)
    status, data = _request(httpd, "POST", "/api/check-path", token, {"path": str(tmp_path)})
    assert data["is_dir"] is True


def test_mcp_endpoint_start_stop(ui_server):
    httpd, _, tmp_path = ui_server
    token = _token(httpd)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    status, data = _request(httpd, "GET", "/api/mcp/config", token)
    assert status == 200 and "claude_desktop_stdio_json" in data and data["http_url"].startswith("http://127.0.0.1:")
    status, data = _request(httpd, "POST", "/api/mcp/start", token, {"port": port})
    assert status == 200, data
    assert data["running"] is True and data["url"] == f"http://127.0.0.1:{port}/mcp", data
    assert "MCP_HTTP_PORT=" + str(port) in (tmp_path / ".env").read_text(encoding="utf-8")
    status, data = _request(httpd, "GET", "/api/mcp/status", token)
    assert data["running"] is True and data["pid"]
    status, data = _request(httpd, "POST", "/api/mcp/stop", token, {})
    assert data["running"] is False


def test_pick_folder_helper_handles_missing_tk(monkeypatch):
    import subprocess

    class R:
        stdout = "__NO_TK__:boom"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    res = mcp_service.pick_folder("")
    assert res["ok"] is False and "tkinter" in res["error"]
