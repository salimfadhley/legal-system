"""Shape tests for the git trigger hooks (WP04, FR-001/FR-002).

No unit-test framework can drive a live git hook (that needs a real clone + broker +
processor — see doc/runbooks/wiring-the-ingest-trigger.md). What we *can* guarantee
statically is the non-negotiable property of FR-002: the hooks are present, executable,
syntactically valid bash, and **exit 0 on every path** (no ``set -e``, ending in
``exit 0``). A hook that could fail ``git`` would block commits to the legal corpus.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

# tests/unit/test_hooks.py → repo root is two parents up.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "hooks"

# (hook filename, the --source value it must publish with)
_HOOKS = [
    ("post-commit", "post-commit"),
    ("post-merge", "post-merge"),
]


def _hook_path(name: str) -> Path:
    return _HOOKS_DIR / name


@pytest.mark.parametrize("name, _source", _HOOKS)
def test_hook_exists(name: str, _source: str) -> None:
    assert _hook_path(name).is_file(), f"missing hook: hooks/{name}"


@pytest.mark.parametrize("name, _source", _HOOKS)
def test_hook_is_executable(name: str, _source: str) -> None:
    mode = _hook_path(name).stat().st_mode
    assert mode & stat.S_IXUSR, f"hooks/{name} is not executable (owner-exec bit unset)"


@pytest.mark.parametrize("name, _source", _HOOKS)
def test_hook_has_bash_shebang(name: str, _source: str) -> None:
    first_line = _hook_path(name).read_text().splitlines()[0]
    assert first_line == "#!/usr/bin/env bash", (
        f"hooks/{name} must start with '#!/usr/bin/env bash', got {first_line!r}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.parametrize("name, _source", _HOOKS)
def test_hook_syntax_valid(name: str, _source: str) -> None:
    """`bash -n` must accept the script (syntax-only check, no execution)."""
    result = subprocess.run(
        ["bash", "-n", str(_hook_path(name))],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"hooks/{name} failed bash -n: {result.stderr}"


@pytest.mark.parametrize("name, _source", _HOOKS)
def test_hook_ends_with_exit_0(name: str, _source: str) -> None:
    """FR-002: the last meaningful line must be `exit 0` so git is never failed."""
    lines = [ln.strip() for ln in _hook_path(name).read_text().splitlines() if ln.strip()]
    assert lines[-1] == "exit 0", (
        f"hooks/{name} must end with 'exit 0' (FR-002); last line is {lines[-1]!r}"
    )


@pytest.mark.parametrize("name, _source", _HOOKS)
def test_hook_has_no_set_e(name: str, _source: str) -> None:
    """`set -e` would abort the hook on the publish failure it is meant to swallow."""
    for raw in _hook_path(name).read_text().splitlines():
        code = raw.split("#", 1)[0].strip()  # ignore comments
        assert code not in {"set -e", "set -o errexit"}, (
            f"hooks/{name} must not use `set -e` (would let a publish failure fail git)"
        )


@pytest.mark.parametrize("name, source", _HOOKS)
def test_hook_publishes_with_correct_source(name: str, source: str) -> None:
    """Each hook must tag its event with the matching --source producer id."""
    body = _hook_path(name).read_text()
    assert f"--source {source}" in body, (
        f"hooks/{name} must call publish-commit with '--source {source}'"
    )
    assert "goldberg publish-commit" in body, (
        f"hooks/{name} must invoke `goldberg publish-commit`"
    )


@pytest.mark.parametrize("name, _source", _HOOKS)
def test_hook_swallows_failure(name: str, _source: str) -> None:
    """Publish failure must be redirected and OR'd away, never propagated."""
    body = _hook_path(name).read_text()
    assert ">/dev/null 2>&1" in body, (
        f"hooks/{name} must swallow publish-commit stdout/stderr"
    )
    assert "||" in body, (
        f"hooks/{name} must tolerate a non-zero publish-commit exit with `||`"
    )


def test_no_set_e_when_hooks_actually_run_a_failing_command() -> None:
    """Behavioural proof of FR-002: run each hook with `goldberg` forced to fail and
    assert the hook still exits 0. We shadow `git` and `goldberg` with stubs on PATH so
    the test needs neither a real repo nor a broker."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")

    for name, _source in _HOOKS:
        stub_dir = _REPO_ROOT / "tests" / "unit" / f"_stub_{name}"
        stub_dir.mkdir(parents=True, exist_ok=True)
        try:
            # `git rev-parse HEAD` → print a fake sha, succeed.
            git_stub = stub_dir / "git"
            git_stub.write_text('#!/usr/bin/env bash\necho deadbeef\n')
            git_stub.chmod(0o755)
            # `goldberg publish-commit …` → always fail (exit 1).
            gb_stub = stub_dir / "goldberg"
            gb_stub.write_text('#!/usr/bin/env bash\nexit 1\n')
            gb_stub.chmod(0o755)
            # `logger` → succeed silently (may be absent on some systems).
            logger_stub = stub_dir / "logger"
            logger_stub.write_text('#!/usr/bin/env bash\nexit 0\n')
            logger_stub.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{stub_dir}{os.pathsep}{env.get('PATH', '')}"
            result = subprocess.run(
                [bash, str(_hook_path(name))],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode == 0, (
                f"hooks/{name} exited {result.returncode} when publish-commit failed; "
                "it MUST exit 0 (FR-002)"
            )
        finally:
            shutil.rmtree(stub_dir, ignore_errors=True)
