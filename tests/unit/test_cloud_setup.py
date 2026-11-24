"""Tests for the Claude Code cloud setup hook."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _warm_spacy_pipeline() -> None:
    """Let the hook test run before a spaCy model has been installed."""


@pytest.mark.skipif(sys.platform == "win32", reason="The hook runs in a Unix shell")
def test_cloud_setup_is_noop_without_remote_flag(
    repo_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = tmp_path / "uv-called"
    fake_uv = tmp_path / "uv"
    fake_uv.write_text('#!/bin/sh\n: > "$UV_CALL_LOG"\nexit 99\n', encoding="utf-8")
    fake_uv.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=os.pathsep)
    monkeypatch.setenv("UV_CALL_LOG", str(calls))
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)

    hook = repo_root / ".claude" / "hooks" / "cloud-setup.sh"
    result = subprocess.run([str(hook)], cwd=tmp_path, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert not calls.exists()
