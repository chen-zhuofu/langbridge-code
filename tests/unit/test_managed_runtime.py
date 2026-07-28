import os
from pathlib import Path
from types import SimpleNamespace

from langbridge_code.tools.common import runtime


def test_inject_runtime_env_prepends_bin(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_RUNTIME_DIR", str(tmp_path / "runtime"))
    env = {"PATH": os.pathsep.join(["/usr/bin", "/bin"])}

    result = runtime.inject_runtime_env(env)

    assert result["PATH"].split(os.pathsep)[0] == str(
        tmp_path / "runtime" / "prefix" / "bin"
    )
    assert "PLAYWRIGHT_BROWSERS_PATH" not in result


def test_ensure_native_tools_installs_missing_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_RUNTIME_DIR", str(tmp_path / "runtime"))
    installed = False
    commands = []

    def available(_name):
        return installed

    def run_checked(command, *, env=None):
        nonlocal installed
        commands.append(command)
        installed = True

    micromamba = tmp_path / "micromamba"
    micromamba.touch()
    monkeypatch.setattr(runtime, "_binary_available", available)
    monkeypatch.setattr(runtime, "_install_micromamba", lambda: micromamba)
    monkeypatch.setattr(runtime, "_run_checked", run_checked)
    monkeypatch.setattr(runtime, "activate_runtime", lambda: None)

    runtime.ensure_native_tools()

    assert commands
    assert commands[0][0] == str(micromamba)
    assert {"ripgrep", "git", "bash"} <= set(commands[0])


def test_ensure_tui_tools_installs_nodejs_once(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_RUNTIME_DIR", str(tmp_path / "runtime"))
    installed = False
    commands = []

    def available(_name):
        return installed

    def run_checked(command, *, env=None):
        nonlocal installed
        commands.append(command)
        installed = True

    micromamba = tmp_path / "micromamba"
    micromamba.touch()
    monkeypatch.setattr(runtime, "_binary_available", available)
    monkeypatch.setattr(runtime, "_install_micromamba", lambda: micromamba)
    monkeypatch.setattr(runtime, "_run_checked", run_checked)
    monkeypatch.setattr(runtime, "activate_runtime", lambda: None)

    runtime.ensure_tui_tools()

    assert commands
    assert commands[0].count("nodejs") == 1


def test_ensure_native_tools_skips_install_when_all_are_available(monkeypatch):
    monkeypatch.setattr(runtime, "_binary_available", lambda _name: True)
    monkeypatch.setattr(runtime, "activate_runtime", lambda: None)
    monkeypatch.setattr(
        runtime,
        "_install_micromamba",
        lambda: (_ for _ in ()).throw(AssertionError("must not install")),
    )

    runtime.ensure_native_tools()


def test_node_older_than_minimum_is_not_available(monkeypatch):
    monkeypatch.setattr(runtime, "_configured_binary", lambda _name: None)
    monkeypatch.setattr(runtime, "inject_runtime_env", lambda env: env)
    monkeypatch.setattr(
        "langbridge_code.tools.common.runtime.shutil.which",
        lambda *_args, **_kwargs: "/usr/bin/node",
    )
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v16.20.2\n"),
    )

    assert runtime._binary_available("node") is False


def test_managed_binary_prefers_frontend_bundled_ripgrep(tmp_path, monkeypatch):
    bundled_rg = tmp_path / "rg"
    bundled_rg.write_text("#!/bin/sh\n", encoding="utf-8")
    bundled_rg.chmod(0o755)
    monkeypatch.setenv("LANGBRIDGE_RG_PATH", str(bundled_rg))

    assert runtime.managed_binary("rg") == str(bundled_rg.resolve())


def test_managed_binary_does_not_install(monkeypatch):
    monkeypatch.setattr(runtime, "_configured_binary", lambda _name: None)
    monkeypatch.setattr(runtime, "inject_runtime_env", lambda env: env)
    monkeypatch.setattr(
        "langbridge_code.tools.common.runtime.shutil.which",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        runtime,
        "ensure_native_tools",
        lambda: (_ for _ in ()).throw(AssertionError("must not install from tools")),
    )

    try:
        runtime.managed_binary("rg")
    except runtime.RuntimeBootstrapError as error:
        assert "unavailable" in str(error)
    else:
        raise AssertionError("expected RuntimeBootstrapError")


def test_bootstrap_prepares_every_advertised_runtime_dependency(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "_ensure_runtime_ignored", lambda: calls.append("ignore"))
    monkeypatch.setattr(runtime, "ensure_native_tools", lambda: calls.append("native"))
    monkeypatch.setattr(
        runtime, "ensure_managed_test_python", lambda: calls.append("pytest")
    )

    runtime.bootstrap_runtime()

    assert calls == ["ignore", "native", "pytest"]


def test_runtime_root_defaults_inside_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGBRIDGE_RUNTIME_DIR", raising=False)
    monkeypatch.setattr("langbridge_code.settings.WORKSPACE_ROOT", tmp_path)

    assert runtime.runtime_root() == tmp_path / ".langbridge" / "runtime"


def test_runtime_ignore_uses_local_git_exclude(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGBRIDGE_RUNTIME_DIR", raising=False)
    monkeypatch.setattr("langbridge_code.settings.WORKSPACE_ROOT", tmp_path)
    exclude = tmp_path / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True)

    runtime._ensure_runtime_ignored()
    runtime._ensure_runtime_ignored()

    assert exclude.read_text().splitlines().count(".langbridge/runtime/") == 1
