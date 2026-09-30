"""Locate the Codex binary bundled with the desktop app."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def _desktop_candidates():
    if sys.platform == "darwin":
        yield Path("/Applications/Codex.app/Contents/Resources/codex")
        yield Path.home() / "Applications/Codex.app/Contents/Resources/codex"
        yield Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    elif sys.platform == "win32":
        for root in (os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMFILES")):
            if root:
                base = Path(root)
                for folder in ("Codex", "OpenAI/Codex"):
                    yield base / "Programs" / folder / "resources/codex.exe"
                    yield base / "Programs" / folder / "app/resources/codex.exe"

        # Microsoft Store installs have a versioned WindowsApps directory.
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1 -ExpandProperty InstallLocation)"],
                capture_output=True, text=True, timeout=5, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            location = result.stdout.strip().splitlines()
            if result.returncode == 0 and location:
                yield Path(location[0]) / "app/resources/codex.exe"
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        yield Path("/usr/lib/chatgpt/resources/codex")


def resolve_codex_executable(configured: str | None = None) -> str | None:
    """Respect an explicit choice, then try Desktop, then the standalone CLI."""
    if configured and configured.strip():
        return configured.strip()
    for candidate in _desktop_candidates():
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return shutil.which("codex")
