from types import SimpleNamespace

import pytest

from langbridge_code import main
from langbridge_code.tools.common.runtime import RuntimeBootstrapError


def test_ensure_tui_built_skips_existing_build(tmp_path, monkeypatch):
    dist = tmp_path / "dist" / "cli.js"
    dist.parent.mkdir()
    dist.write_text("built", encoding="utf-8")
    monkeypatch.setattr(main, "TUI_DIR", tmp_path)
    monkeypatch.setattr(main, "TUI_DIST", dist)
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("npm should not run"),
    )

    main.ensure_tui_built("npm")


def test_ensure_tui_built_installs_locked_dependencies_and_builds(
    tmp_path, monkeypatch
):
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    dist = tmp_path / "dist" / "cli.js"
    commands = []

    def run(command, *, cwd, check):
        commands.append((command, cwd, check))
        if command[1:] == ["run", "build"]:
            dist.parent.mkdir()
            dist.write_text("built", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(main, "TUI_DIR", tmp_path)
    monkeypatch.setattr(main, "TUI_DIST", dist)
    monkeypatch.setattr(main.subprocess, "run", run)

    main.ensure_tui_built("/runtime/npm")

    assert [command for command, _cwd, _check in commands] == [
        ["/runtime/npm", "ci", "--no-audit", "--no-fund"],
        ["/runtime/npm", "run", "build"],
    ]
    assert all(cwd == tmp_path and check is False for _command, cwd, check in commands)


def test_ensure_tui_built_reports_failed_command(tmp_path, monkeypatch):
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main, "TUI_DIR", tmp_path)
    monkeypatch.setattr(main, "TUI_DIST", tmp_path / "dist" / "cli.js")
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=7),
    )

    with pytest.raises(RuntimeBootstrapError, match=r"npm ci"):
        main.ensure_tui_built("npm")


def test_main_prepares_runtime_and_tui_before_credentials(monkeypatch):
    calls = []
    monkeypatch.setattr(
        main, "ensure_tui_tools", lambda: calls.append("tui-tools")
    )
    monkeypatch.setattr(
        main,
        "managed_binary",
        lambda name: calls.append(f"binary:{name}") or f"/runtime/{name}",
    )
    monkeypatch.setattr(
        main,
        "ensure_tui_built",
        lambda npm: calls.append(f"tui:{npm}"),
    )
    monkeypatch.setattr(
        main, "ensure_api_credentials", lambda: calls.append("credentials")
    )
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda command, *, cwd: calls.append(("launch", command, cwd))
        or SimpleNamespace(returncode=0),
    )

    with pytest.raises(SystemExit) as exit_info:
        main.main()

    assert exit_info.value.code == 0
    assert calls[:5] == [
        "tui-tools",
        "binary:node",
        "binary:npm",
        "tui:/runtime/npm",
        "credentials",
    ]
