#!/usr/bin/env python3
"""
Syntax-check the JavaScript embedded in ``emailrecon.html``.

The browser app keeps all its logic in a single ``<script>`` block. This pulls
that block out and runs ``node --check`` on it, so a typo in the app fails CI
instead of only surfacing in a browser. Skips cleanly if node isn't installed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "emailrecon.html")


def main() -> int:
    with open(HTML, encoding="utf-8") as fh:
        html = fh.read()

    match = re.search(r"<script>(.*)</script>", html, re.S)
    if not match:
        print("FAIL: no <script> block found in emailrecon.html")
        return 1

    node = shutil.which("node")
    if not node:
        print("node not found — skipping JS syntax check")
        return 0

    fd, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(match.group(1))
        proc = subprocess.run([node, "--check", path], capture_output=True, text=True)
        if proc.returncode == 0:
            print(f"JS syntax OK ({len(match.group(1))} chars)")
            return 0
        print("FAIL: JS syntax error")
        print(proc.stderr)
        return proc.returncode
    finally:
        os.unlink(path)


if __name__ == "__main__":
    raise SystemExit(main())
