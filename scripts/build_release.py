#!/usr/bin/env python3
"""
Assemble the distributable ``dist/emailrecon.zip``.

Used by the release workflow so every tagged release ships the same bundle:
the Python package, the local server, the browser app (and its ``index.html``
copy), tests, and project metadata — all at the archive root.
"""

from __future__ import annotations

import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INCLUDE_FILES = [
    "emailrecon.html",
    "index.html",
    "server.py",
    "README.md",
    "LICENSE",
    "requirements.txt",
    "pyproject.toml",
    ".gitignore",
]
INCLUDE_DIRS = ["emailrecon", "tests"]
SKIP_PARTS = {"__pycache__", "build", "dist", ".git", ".venv"}


def iter_entries():
    for name in INCLUDE_FILES:
        path = os.path.join(ROOT, name)
        if os.path.isfile(path):
            yield path, name
    for directory in INCLUDE_DIRS:
        base = os.path.join(ROOT, directory)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
            for filename in sorted(filenames):
                if filename.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(dirpath, filename)
                yield full, os.path.relpath(full, ROOT)


def main() -> int:
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "emailrecon.zip")

    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, arcname in iter_entries():
            zf.write(full, arcname)
            count += 1

    print(f"wrote {out_path} ({os.path.getsize(out_path)} bytes, {count} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
