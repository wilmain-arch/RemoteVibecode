#!/usr/bin/env python3
"""Check local Markdown destinations; no network or application tests."""
import re
from pathlib import Path
root = Path(__file__).resolve().parent.parent
errors = []
for path in [root / "README.md", root / "CONTRIBUTING.md", *root.glob("docs/*.md"), *[root / d / "README.md" for d in ("agent", "android", "arch", "relay")]]:
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', path.read_text()):
        if target.startswith(("http:", "https:", "#", "mailto:")):
            continue
        target = target.split("#", 1)[0]
        if not (path.parent / target).exists():
            errors.append(f"{path.relative_to(root)}: {target}")
if errors:
    raise SystemExit("\n".join(errors))
print("Documentation links OK")
