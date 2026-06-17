"""Build an emailable deploy package: dist/arcgis-uploader-YYYYMMDD.zip

Files are picked by ALLOWLIST, not by excluding patterns, so local state —
.env (credentials!), .venv, __pycache__, .pytest_cache, dist — can never
leak into a package that is about to leave the building.

Usage:  python scripts/make_package.py        (stdlib only, no deps)
"""
from __future__ import annotations

import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INCLUDE = [
    "app",
    "static",
    "tests",
    "scripts",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    ".env.example",
    ".gitignore",
]
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
FORBIDDEN = {".env"}  # belt and braces: never package these names


def gather() -> list[Path]:
    files: list[Path] = []
    for name in INCLUDE:
        path = ROOT / name
        if not path.exists():
            print(f"  note: {name} not found, skipped")
            continue
        if path.is_file():
            files.append(path)
            continue
        for child in sorted(path.rglob("*")):
            if not child.is_file():
                continue
            parts = child.relative_to(ROOT).parts
            if SKIP_DIRS.intersection(parts) or child.suffix == ".pyc":
                continue
            files.append(child)
    for file in files:
        if file.name in FORBIDDEN:
            sys.exit(f"refusing to package {file}")
    return files


def main() -> None:
    out_dir = ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"arcgis-uploader-{date.today():%Y%m%d}.zip"
    files = gather()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in files:
            arcname = file.relative_to(ROOT).as_posix()
            zf.write(file, arcname)
            print(f"  + {arcname}")
    print(f"\n{out}  ({out.stat().st_size / 1024:.0f} KB, {len(files)} files)")


if __name__ == "__main__":
    main()
