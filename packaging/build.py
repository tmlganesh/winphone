"""Build dist/winphone/winphone.exe, zip it, and pin the zip's SHA-256 into the npm package.

Run from the repo root: python -m uv run python packaging/build.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
ZIP = DIST / "winphone-win64.zip"
NPM_PACKAGE = ROOT / "npm" / "winphone" / "package.json"


def main() -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "winphone.spec"), "--noconfirm",
                    "--distpath", str(DIST), "--workpath", str(ROOT / "build")], check=True, cwd=ROOT / "packaging")
    folder = DIST / "winphone"
    ZIP.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, Path("winphone") / f.relative_to(folder))
    digest = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    pkg = json.loads(NPM_PACKAGE.read_text(encoding="utf-8"))
    pkg["winphone"]["sha256"] = digest
    NPM_PACKAGE.write_text(json.dumps(pkg, indent=2) + "\n", encoding="utf-8")
    size = ZIP.stat().st_size / 1_000_000
    print(f"{ZIP} {size:.1f} MB sha256={digest}")
    shutil.rmtree(ROOT / "build", ignore_errors=True)


if __name__ == "__main__":
    main()
