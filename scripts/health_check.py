#!/usr/bin/env python3
"""
Weekly endpoint-health probe.

The checkers rely on public endpoints that sites change without notice. This
runs a handful of *stable* services against a random address that certainly has
no account anywhere, and fails if any of them comes back ``registered`` — i.e.
a false positive, which is the clearest sign that an endpoint has drifted.
"""

from __future__ import annotations

import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emailrecon import RunConfig, registry, run_sync  # noqa: E402

WATCH = [
    "gravatar", "spotify", "zoho", "hubspot", "archive",
    "protonmail", "firefox", "office365", "docker", "duolingo",
]


def main() -> int:
    email = f"healthcheck.{secrets.token_hex(6)}@example.com"
    specs = [s for s in registry() if s.name in WATCH]
    results = run_sync(email, specs=specs, cfg=RunConfig(concurrency=10, timeout=15))

    for r in sorted(results, key=lambda r: r.name):
        note = r.error or ""
        print(f"  {r.name:<12} {r.status:<16} {note}")

    false_positives = [r for r in results if r.status == "registered"]
    print(f"\nprobed {len(results)} sites for {email}")

    if false_positives:
        print("FALSE POSITIVES:", ", ".join(r.name for r in false_positives))
        return 1

    print("OK: no false positives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
