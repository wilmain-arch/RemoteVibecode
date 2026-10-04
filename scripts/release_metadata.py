#!/usr/bin/env python3
"""Validate source versions and print GitHub Actions outputs."""
import json, re, sys
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

readme = root / "README.md"
block = f'<!-- BEGIN GENERATED VERSIONS -->\nКомплект **{v["bundle"]}**: Android **{v["android"]}**, агент ПК **{v["agent"]}**, ретранслятор **{v["relay"]}**.\n<!-- END GENERATED VERSIONS -->'
pattern = r'<!-- BEGIN GENERATED VERSIONS -->.*?<!-- END GENERATED VERSIONS -->'
if "--sync-readme" in sys.argv:
    readme.write_text(re.sub(pattern, lambda _: block, readme.read_text(), flags=re.S))
else:
    assert re.search(pattern, readme.read_text(), re.S).group(0) == block, "README versions mismatch; run release_metadata.py --sync-readme"
