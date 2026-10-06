#!/usr/bin/env python3
"""
emailrecon server
=================

A tiny local backend so the HTML app can run *real* checks with no CORS and
with proper cookie handling.

Why this exists
---------------
A browser cannot make these cross-origin requests itself (CORS) and cannot read
cross-site cookies, so some checks fail from a pure web page. Run this server
and open the app at http://127.0.0.1:8777/ — the page calls this server, which
runs the very same Python checkers over httpx (where cookies and redirects work
normally) and streams the results back as JSON.

Usage
-----
    python server.py                 # serves on http://127.0.0.1:8777/
    python server.py --port 9000 --host 0.0.0.0

It serves emailrecon.html (next to this file) at / and exposes:
    GET /api/sites
    GET /api/check?email=...&concurrency=30&timeout=15&category=social&only=a,b
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from emailrecon import RunConfig, registry, run_sync

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, "emailrecon.html")


def _load_html() -> bytes:
    with open(HTML_PATH, encoding="utf-8") as fh:
        return fh.read().encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "emailrecon/1.0"

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, q = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            try:
                self._send(200, _load_html(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, b"emailrecon.html not found next to server.py", "text/plain")
            return

        if path == "/api/sites":
            specs = registry()
            self._json(200, {
                "count": len(specs),
                "sites": [
                    {"name": s.name, "domain": s.domain, "category": s.category,
                     "method": s.method, "confidence": s.confidence, "evidence": s.evidence}
                    for s in specs
                ],
            })
            return

        if path == "/api/check":
            email = (q.get("email", [""])[0]).strip()
            if not email or "@" not in email:
                self._json(400, {"error": "a valid email is required"})
                return

            specs = registry()
            only = q.get("only", [""])[0]
            if only:
                wanted = {n.strip().lower() for n in only.split(",") if n.strip()}
                specs = [s for s in specs if s.name.lower() in wanted]
            cats = q.get("category", [""])[0]
            if cats:
                wanted = {c.strip().lower() for c in cats.split(",") if c.strip()}
                specs = [s for s in specs if s.category.lower() in wanted]
            excl = q.get("exclude", [""])[0]
            if excl:
                drop = {n.strip().lower() for n in excl.split(",") if n.strip()}
                specs = [s for s in specs if s.name.lower() not in drop]

            try:
                concurrency = int(q.get("concurrency", ["30"])[0])
                timeout = float(q.get("timeout", ["15"])[0])
            except ValueError:
                self._json(400, {"error": "bad concurrency/timeout"})
                return

            cfg = RunConfig(concurrency=max(1, concurrency), timeout=max(3.0, timeout))
            try:
                results = run_sync(email, specs=specs, cfg=cfg)
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
                return

            self._json(200, {
                "email": email,
                "count": len(results),
                "results": [r.as_dict() for r in results],
            })
            return

        self._send(404, b"not found", "text/plain")

    def log_message(self, fmt, *args):
        # quieter default logging
        print(f"  {self.address_string()} {fmt % args}")


def main():
    ap = argparse.ArgumentParser(description="Serve the emailrecon app and its check API.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8777)
    args = ap.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"emailrecon server running — open {url}")
    print(f"  API: {url}api/check?email=someone@example.com")
    print("  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
