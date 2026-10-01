#!/usr/bin/env python3
"""Produce and sign exact release metadata after the binaries are built."""
import argparse
import base64
import hashlib
import json
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('directory', type=Path)
parser.add_argument('--key', type=Path, required=True)
a = parser.parse_args()
v = json.loads(Path('release.json').read_text())
base = f'https://github.com/wilmain-arch/RemoteVibecode/releases/download/bundle-v{v["bundle"]}/'
names = {'android': f'RemoteVibecode-{v["android"]}.apk',
         'windows': 'RemoteVibecodeAgent.exe',
         'arch': f'remotevibecode-agent-{v["agent"]}-1-any.pkg.tar.zst',
         'relay': f'remotevibecode-relay_{v["relay"]}_all.deb'}
components = {}
for name, filename in names.items():
 p = a.directory / filename
 components[name] = {'version': v['agent' if name in ('arch', 'windows') else name],
  'url': base + filename, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
  'size': p.stat().st_size, 'protocol': 1}
components['android'].update(versionCode=v['androidCode'], packageId='ru.wilmain.codexphone.relay', certificateSha256=v['androidCert'])
data = {'schema': 1, 'bundle': v['bundle'], 'notes': Path('RELEASE_NOTES.md').read_text(),
 'releaseUrl': f'https://github.com/wilmain-arch/RemoteVibecode/releases/tag/bundle-v{v["bundle"]}',
 'components': components}
manifest = a.directory / 'update.json'
manifest.write_bytes(json.dumps(data,ensure_ascii=False,indent=2).encode())
r = subprocess.run(['openssl','dgst','-sha256','-sign',str(a.key),str(manifest)],check=True,capture_output=True)
signature = a.directory / 'update.signature'
signature.write_bytes(r.stdout)
try:
 subprocess.run(['openssl','dgst','-sha256','-verify','agent/update-public.pem','-signature',str(signature),str(manifest)],check=True)
finally:
 signature.unlink(missing_ok=True)
(a.directory / 'update.json.sig').write_bytes(base64.b64encode(r.stdout))
