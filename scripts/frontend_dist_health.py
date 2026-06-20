"""Check that the built frontend dist contains usable entry assets."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from source_health import FORBIDDEN_STRINGS, ROOT


DIST = ROOT / "frontend" / "dist"
INDEX = DIST / "index.html"


def main() -> int:
    failures: list[str] = []

    if not INDEX.exists():
        failures.append("frontend/dist/index.html is missing")
    else:
        html = INDEX.read_text(encoding="utf-8")
        if '<div id="app"></div>' not in html:
            failures.append("dist index is missing #app mount node")
        if "WeChatAI" not in html:
            failures.append("dist index title does not mention WeChatAI")

        hits = sorted({f"U+{ord(token[0]):04X}" for token in FORBIDDEN_STRINGS if token in html})
        if hits:
            failures.append(f"dist index has suspicious text markers: {', '.join(hits)}")

        asset_paths = re.findall(r"""(?:src|href)=["'](/assets/[^"']+)["']""", html)
        js_assets = [path for path in asset_paths if path.endswith(".js")]
        css_assets = [path for path in asset_paths if path.endswith(".css")]
        if not js_assets:
            failures.append("dist index does not reference a JS asset")
        if not css_assets:
            failures.append("dist index does not reference a CSS asset")

        for asset_path in asset_paths:
            asset = DIST / asset_path.lstrip("/")
            if not asset.exists():
                failures.append(f"referenced asset is missing: {asset_path}")
                continue
            if asset.stat().st_size <= 0:
                failures.append(f"referenced asset is empty: {asset_path}")

    if failures:
        print("Frontend dist health check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Frontend dist health check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
