"""Owner-only permissions for .env and the chat store."""
import os
import stat
import subprocess
import sys

from mcp_adapter.fileperm import _icacls, restrict_to_owner
from mcp_adapter.setup_wizard import write_env_updates


def _principals(path) -> list[str]:
    out = subprocess.run([_icacls(), str(path)], capture_output=True, text=True).stdout
    rows = [ln.replace(str(path), "").strip() for ln in out.splitlines() if ":(" in ln]
    return [r.split(":(")[0] for r in rows]


def test_env_file_and_chat_store_are_owner_only_and_still_readable(tmp_path):
    env = tmp_path / ".env"
    write_env_updates(env, {"TAVILY_API": "tvly-x"})
    store = tmp_path / "chats"
    (store / "sub").mkdir(parents=True)
    old = store / "chat_0123456789ab.json"
    old.write_text("{}", encoding="utf-8")
    (store / "sub" / "x.txt").write_text("x", encoding="utf-8")
    assert restrict_to_owner(store, recursive=True)
    new = store / "chat_ba9876543210.json"
    new.write_text("{}", encoding="utf-8")
    for p in (old, store / "sub" / "x.txt", new):
        assert p.read_text(encoding="utf-8"), f"{p} must stay readable for the owner"
    if sys.platform == "win32":
        user = os.environ.get("USERNAME", "")
        for p in (env, store, old, store / "sub" / "x.txt", new):
            who = _principals(p)
            assert who and all(w.endswith("\\" + user) or w == "NT AUTHORITY\\SYSTEM" for w in who), (p, who)
    else:
        assert stat.S_IMODE(env.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.stat().st_mode) == 0o700
    assert restrict_to_owner(tmp_path / "missing") is False
