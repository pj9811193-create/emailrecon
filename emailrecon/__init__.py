"""
emailrecon
==========

Parallel email OSINT: check whether an address is registered across 100+
public websites.

    from emailrecon import run_sync, registry

    results = run_sync("someone@example.com")
    for r in results:
        if r.status == "registered":
            print(r.name, r.domain)

See ``python -m emailrecon --help`` for the CLI.
"""

from .engine import (  # noqa: F401
    ERROR,
    NOT_REGISTERED,
    RATE_LIMITED,
    REGISTERED,
    UNKNOWN,
    RunConfig,
    SiteResult,
    SiteSpec,
    registry,
    run,
    run_sync,
)

__version__ = "1.0.0"
__all__ = [
    "run",
    "run_sync",
    "registry",
    "RunConfig",
    "SiteResult",
    "SiteSpec",
    "REGISTERED",
    "NOT_REGISTERED",
    "UNKNOWN",
    "RATE_LIMITED",
    "ERROR",
]
