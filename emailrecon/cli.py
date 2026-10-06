"""
emailrecon.cli
==============

Command-line interface.  Run it as::

    python -m emailrecon someone@example.com
    emailrecon someone@example.com --json hits.json --csv hits.csv

See ``python -m emailrecon --help`` for every option.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys

from . import engine

# ANSI colours
_C = {
    "registered": "\033[92m",   # green
    "not_registered": "\033[90m",  # grey
    "unknown": "\033[93m",      # yellow
    "rate_limited": "\033[95m",  # magenta
    "error": "\033[91m",        # red
    "reset": "\033[0m",
    "bold": "\033[1m",
}
_LABEL = {
    "registered": "FOUND",
    "not_registered": "-",
    "unknown": "?",
    "rate_limited": "RATE",
    "error": "ERR",
}


def _colour(status: str, text: str, enabled: bool) -> str:
    if not enabled:
        return text
    return _C.get(status, "") + text + _C["reset"]


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="emailrecon",
        description="Check whether an email address is registered across "
                    "100+ public websites, in parallel.",
    )
    p.add_argument("email", nargs="?", help="email address to investigate")
    p.add_argument("-c", "--concurrency", type=int, default=30,
                   help="max simultaneous requests (default 30)")
    p.add_argument("-t", "--timeout", type=float, default=15.0,
                   help="per-site timeout in seconds (default 15)")
    p.add_argument("-r", "--retries", type=int, default=1,
                   help="retries per site on failure (default 1)")
    p.add_argument("--category", action="append", default=None,
                   help="only run sites in this category (repeatable)")
    p.add_argument("--only", default=None,
                   help="comma-separated site names to run exclusively")
    p.add_argument("--exclude", default=None,
                   help="comma-separated site names to skip")
    p.add_argument("--proxy", default=None, help="HTTP(S) proxy URL")
    p.add_argument("--json", dest="json_path", default=None,
                   help="write full results to this JSON file")
    p.add_argument("--csv", dest="csv_path", default=None,
                   help="write results to this CSV file")
    p.add_argument("-q", "--quiet", action="store_true",
                   help="show only sites where an account was found")
    p.add_argument("--no-color", action="store_true", help="disable coloured output")
    p.add_argument("--list", action="store_true",
                   help="list all available sites and exit")
    return p.parse_args(argv)


def _select_specs(args):
    specs = engine.registry()
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",")}
        specs = [s for s in specs if s.name.lower() in wanted]
    if args.category:
        cats = {c.strip().lower() for c in args.category}
        specs = [s for s in specs if s.category.lower() in cats]
    if args.exclude:
        drop = {s.strip().lower() for s in args.exclude.split(",")}
        specs = [s for s in specs if s.name.lower() not in drop]
    return specs


def _print_table(results, quiet, colour):
    ordered = sorted(
        results,
        key=lambda r: (r.status != engine.REGISTERED, r.name),
    )
    shown = [r for r in ordered if not quiet or r.status == engine.REGISTERED]
    if not shown:
        print("No results to display.")
        return
    name_w = max(len(r.name) for r in shown)
    dom_w = max(len(r.domain) for r in shown)
    for r in shown:
        label = _colour(r.status, _LABEL.get(r.status, "?"), colour)
        line = f"  {label:<9} {r.name:<{name_w}}  {r.domain:<{dom_w}}  {r.method}"
        if r.status == engine.REGISTERED and r.confidence not in ("none", "", None):
            line += f"  <{r.confidence}/{r.evidence}>"
        if r.detail:
            line += f"  [{r.detail}]"
        if r.error:
            line += f"  ({r.error})"
        print(line)


def _summary(results, colour):
    found = sum(1 for r in results if r.status == engine.REGISTERED)
    unknown = sum(1 for r in results if r.status == engine.UNKNOWN)
    limited = sum(1 for r in results if r.status == engine.RATE_LIMITED)
    errors = sum(1 for r in results if r.status == engine.ERROR)
    total = len(results)
    print()
    head = _colour("registered", f"{found} found", colour)
    print(f"{head}  ·  {total - found - unknown - limited - errors} clean  ·  "
          f"{unknown} inconclusive  ·  {limited} rate-limited  ·  {errors} errors  "
          f"(of {total} sites)")


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.list:
        specs = engine.registry()
        cats = sorted({s.category for s in specs})
        print(f"{len(specs)} sites across {len(cats)} categories:\n")
        for cat in cats:
            names = [s.name for s in specs if s.category == cat]
            print(f"  {cat:<12} ({len(names)}): {', '.join(names)}")
        return 0

    if not args.email:
        print("error: an email address is required (or use --list)", file=sys.stderr)
        return 2

    specs = _select_specs(args)
    if not specs:
        print("error: no sites selected", file=sys.stderr)
        return 2

    colour = not args.no_color and sys.stdout.isatty()

    print(f"Probing {args.email} against {len(specs)} sites "
          f"(concurrency={args.concurrency}, timeout={args.timeout}s)\n")

    cfg = engine.RunConfig(
        concurrency=args.concurrency,
        timeout=args.timeout,
        retries=args.retries,
        proxies=args.proxy,
    )
    results = engine.run_sync(args.email, specs=specs, cfg=cfg)

    _print_table(results, args.quiet, colour)
    _summary(results, colour)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump(
                {"email": args.email, "results": [r.as_dict() for r in results]},
                fh, indent=2,
            )
        print(f"\nJSON written to {args.json_path}")

    if args.csv_path:
        with open(args.csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["name", "domain", "category", "method",
                            "status", "confidence", "evidence", "verify_url",
                            "detail", "error", "elapsed_ms"],
            )
            writer.writeheader()
            for r in sorted(results, key=lambda r: (r.status != engine.REGISTERED, r.name)):
                writer.writerow(r.as_dict())
        print(f"CSV written to {args.csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
