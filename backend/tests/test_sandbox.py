from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.sandbox import PolicyViolation, SandboxPaths, build_sandbox, validate_command
from app.services.workspace import Workspace, WorkspaceError, is_test_path


@pytest.mark.parametrize("argv", [
    ["bash", "-c", "ls"], ["sh", "x.sh"], ["curl", "https://example.com"], ["python", "-c", "print(1)"],
    ["python", "-m", "http.server"], ["git", "push"], ["git", "commit", "-m", "x"], ["/usr/bin/python3", "x.py"],
    ["../bin/python", "x"], ["find", ".", "-delete"], ["rm", "-rf", "/"], ["sudo", "ls"], [],
])
def test_policy_rejects_dangerous_commands(argv):
    with pytest.raises(PolicyViolation):
        validate_command(argv)


@pytest.mark.parametrize("argv", [
    ["python", "-m", "pytest", "-q"], ["pytest"], ["npm", "test"], ["git", "diff"], ["bandit", "-r", "app"],
    [".venv/bin/python", "-m", "pytest"], ["ls", "-la"],
])
def test_policy_allows_build_and_test_commands(argv):
    assert validate_command(argv).argv == tuple(argv)


def test_policy_network_only_for_install():
    assert validate_command(["pip", "install", "x"], purpose="install", allow_network=True).network
    assert not validate_command(["pytest"], purpose="test", allow_network=True).network
    with pytest.raises(PolicyViolation):
        validate_command(["pytest"], workdir="../outside")


def make_ws(tmp_path: Path) -> Workspace:
    ws = Workspace(root=tmp_path / "task")
    (ws.repo / "app").mkdir(parents=True)
    (ws.repo / ".git").mkdir()
    (ws.repo / "app" / "main.py").write_text("print('hello')\nprint('hello')\n")
    return ws


def test_workspace_blocks_traversal_and_git(tmp_path):
    ws = make_ws(tmp_path)
    for bad in ["../secret", "/etc/passwd", "app/../../x", ".git/config", ""]:
        with pytest.raises(WorkspaceError):
            ws.write_file(bad, "x")
    (tmp_path / "outside.txt").write_text("secret")
    (ws.repo / "link").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(WorkspaceError):
        ws.read_file("link")


def test_workspace_edit_requires_unique_match(tmp_path):
    ws = make_ws(tmp_path)
    with pytest.raises(WorkspaceError, match="matches 2 places"):
        ws.edit_file("app/main.py", "print('hello')", "x")
    with pytest.raises(WorkspaceError, match="not found"):
        ws.edit_file("app/main.py", "absent", "x")
    ws.write_file("app/new.py", "a = 1\n")
    ws.edit_file("app/new.py", "a = 1", "a = 2")
    assert (ws.repo / "app" / "new.py").read_text() == "a = 2\n"


def test_test_only_writes(tmp_path):
    ws = make_ws(tmp_path)
    assert is_test_path("tests/test_auth.py") and is_test_path("src/x.test.ts") and not is_test_path("app/main.py")
    ws.write_file("tests/test_x.py", "def test_x():\n    assert True\n", test_only=True)
    with pytest.raises(WorkspaceError):
        ws.write_file("app/main.py", "hacked", test_only=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS-only")
async def test_seatbelt_isolation(settings, tmp_path):
    sandbox = build_sandbox(settings)
    base = settings.workspaces_root / "sb"
    paths = SandboxPaths(repo=base / "repo", deps=base / "deps", tmp=base / "tmp")
    paths.repo.mkdir(parents=True)
    (paths.repo / "probe.py").write_text(
        "import socket, os, pathlib\n"
        "def t(name, fn):\n"
        "    try:\n        fn(); print(name, 'ALLOWED')\n"
        "    except Exception as e:\n        print(name, 'DENIED')\n"
        "t('write_repo', lambda: open('ok.txt', 'w').write('x'))\n"
        f"t('write_outside', lambda: open({str(tmp_path / 'escape.txt')!r}, 'w').write('x'))\n"
        "t('read_home', lambda: os.listdir(os.path.expanduser('~' + os.environ.get('LOGNAME', ''))))\n"
        "t('network', lambda: socket.create_connection(('1.1.1.1', 443), timeout=3))\n"
    )
    result = await sandbox.run(validate_command(["python3", "probe.py"]), paths, timeout_s=30)
    assert result.exit_code == 0, result.stderr
    assert "write_repo ALLOWED" in result.stdout
    assert "write_outside DENIED" in result.stdout
    assert "network DENIED" in result.stdout
    assert not (tmp_path / "escape.txt").exists()

    (paths.repo / "spin.py").write_text("while True:\n    pass\n")
    result = await sandbox.run(validate_command(["python3", "spin.py"]), paths, timeout_s=1)
    assert result.timed_out and result.exit_code is None
