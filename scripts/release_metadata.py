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
for key, value in v.items():
    print(f"{key}={value}")
