#!/usr/bin/env python3
"""Validate source versions and print GitHub Actions outputs."""
import json, re
from pathlib import Path
root = Path(__file__).resolve().parent.parent
v = json.loads((root / "release.json").read_text())
android = (root / "android/app/build.gradle.kts").read_text()
arch = (root / "arch/PKGBUILD").read_text()
assert re.search(r'versionName = "' + re.escape(v["android"]) + '"', android), "Android version mismatch"
assert re.search(r'versionCode = ' + str(v["androidCode"]) + r'\b', android), "Android code mismatch"
assert re.search(r'^pkgver=' + re.escape(v["agent"]) + '$', arch, re.M), "Agent version mismatch"
updater = (root / "agent/updates.py").read_text()
assert re.search(r"VERSION = '" + re.escape(v["agent"]) + "'", updater), "Updater version mismatch"
public = (root / "agent/update-public.pem").read_text().strip()
mobile = (root / "android/app/src/main/java/ru/wilmain/codexphone/AppUpdates.kt").read_text()
embedded = re.search(r'UPDATE_KEY = """\n(.*?)"""', mobile, re.S).group(1).strip()
assert embedded == public, "Android/agent update trust keys mismatch"
for key, value in v.items():
    print(f"{key}={value}")
