"""
emailrecon.engine
=================

The async core: an HTTP client, a site registry, and a parallel runner.

A *site* is a small async function with the signature::

    async def checker(email: str, client: httpx.AsyncClient) -> dict

which returns a dict with (at least) the key ``exists``:

    exists = True   -> an account tied to this email was found
    exists = False  -> the service says the email is free / unknown
    exists = None   -> inconclusive (blocked, captcha, changed API, ...)

The optional keys ``detail``, ``rate_limit`` and ``error`` carry extra
context.  Everything else is handled here so individual checkers stay tiny.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import httpx

# --------------------------------------------------------------------------- #
# User agents
# --------------------------------------------------------------------------- #

CHROME_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

FIREFOX_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


def chrome_ua() -> str:
    return random.choice(CHROME_UAS)


def firefox_ua() -> str:
    return random.choice(FIREFOX_UAS)


def rand_token(n: int = 12) -> str:
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(n))


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #

REGISTERED = "registered"
NOT_REGISTERED = "not_registered"
UNKNOWN = "unknown"
RATE_LIMITED = "rate_limited"
ERROR = "error"


@dataclass
class SiteResult:
    name: str
    domain: str
    category: str
    method: str
    status: str
    detail: Optional[str] = None
    error: Optional[str] = None
    elapsed_ms: int = 0
    confidence: str = "medium"   # high | medium | low  (strength of a hit)
    evidence: str = "inferred"   # direct | inferred     (kind of proof)
    verify_url: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "domain": self.domain,
            "category": self.category,
            "method": self.method,
            "status": self.status,
            "detail": self.detail,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "verify_url": self.verify_url,
        }


@dataclass
class SiteSpec:
    """A registered checker plus its metadata."""

    name: str
    domain: str
    func: Callable[[str, httpx.AsyncClient], Awaitable[dict]]
    category: str = "misc"
    method: str = "register"
    confidence: str = "medium"   # how strong a positive from this check is
    evidence: str = "inferred"   # direct account data, or an inferred signal
    verify: Optional[str] = None  # URL a human can open to confirm a hit


def site(
    name: str,
    domain: str,
    category: str = "misc",
    method: str = "register",
    confidence: str = "medium",
    evidence: str = "inferred",
    verify: Optional[str] = None,
):
    """Decorator that turns a checker function into a registered SiteSpec."""

    def wrap(func):
        func._site_spec = SiteSpec(
            name=name, domain=domain, func=func, category=category, method=method,
            confidence=confidence, evidence=evidence, verify=verify,
        )
        return func

    return wrap


def registry() -> list[SiteSpec]:
    """Import the sites module (triggering registration) and return specs."""
    from . import sites  # noqa: F401  (side-effect: registers every checker)

    specs: list[SiteSpec] = []
    for obj in vars(sites).values():
        spec = getattr(obj, "_site_spec", None)
        if isinstance(spec, SiteSpec):
            specs.append(spec)
    specs.sort(key=lambda s: s.name)
    return specs


# --------------------------------------------------------------------------- #
# Response helpers
# --------------------------------------------------------------------------- #

def classify_exception(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, asyncio.TimeoutError):
        return ERROR, "timeout"
    if isinstance(exc, httpx.TimeoutException):
        return ERROR, "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return RATE_LIMITED, "HTTP 429"
        return ERROR, f"HTTP {code}"
    if isinstance(exc, httpx.ConnectError):
        return ERROR, "connection failed"
    return ERROR, type(exc).__name__


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

@dataclass
class RunConfig:
    concurrency: int = 30
    timeout: float = 15.0
    retries: int = 1
    proxies: Optional[str] = None
    verbose: bool = False
    on_result: Optional[Callable[[SiteResult], None]] = None


def build_client(cfg: RunConfig) -> httpx.AsyncClient:
    kwargs = dict(
        follow_redirects=True,
        timeout=httpx.Timeout(cfg.timeout, connect=min(cfg.timeout, 10.0)),
        headers={
            "User-Agent": chrome_ua(),
            "Accept-Language": "en-US,en;q=0.9",
            "DNT": "1",
        },
        limits=httpx.Limits(max_connections=cfg.concurrency * 2),
    )
    if cfg.proxies:
        kwargs["proxy"] = cfg.proxies
    return httpx.AsyncClient(**kwargs)


async def _run_one(
    spec: SiteSpec,
    email: str,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    cfg: RunConfig,
) -> SiteResult:
    started = time.perf_counter()
    result: Optional[dict] = None
    last_error = None

    for attempt in range(cfg.retries + 1):
        try:
            async with sem:
                result = await asyncio.wait_for(
                    spec.func(email, client), timeout=cfg.timeout
                )
            break
        except Exception as exc:  # noqa: BLE001 - checker failures are expected
            last_error = exc
            if attempt < cfg.retries and not isinstance(exc, httpx.TimeoutException):
                await asyncio.sleep(0.5 + random.random())
                continue
            break

    elapsed = int((time.perf_counter() - started) * 1000)

    verify_url = spec.verify or f"https://{spec.domain}"
    if result is None:
        status, err = classify_exception(last_error) if last_error else (ERROR, "no result")
        res = SiteResult(
            name=spec.name, domain=spec.domain, category=spec.category,
            method=spec.method, status=status, error=err, elapsed_ms=elapsed,
            confidence="low", evidence=spec.evidence, verify_url=verify_url,
        )
    else:
        exists = result.get("exists")
        if result.get("rate_limit"):
            status = RATE_LIMITED
        elif exists is True:
            status = REGISTERED
        elif exists is False:
            status = NOT_REGISTERED
        else:
            status = UNKNOWN
        # Confidence is only meaningful for a hit; a clean/unknown result carries
        # no positive evidence at all.
        conf = spec.confidence if status == REGISTERED else "none"
        res = SiteResult(
            name=spec.name, domain=spec.domain, category=spec.category,
            method=spec.method, status=status,
            detail=result.get("detail"), error=result.get("error"),
            elapsed_ms=elapsed, confidence=conf, evidence=spec.evidence,
            verify_url=verify_url,
        )

    if cfg.on_result:
        try:
            cfg.on_result(res)
        except Exception:  # noqa: BLE001
            pass
    return res


async def run(
    email: str,
    specs: Optional[list[SiteSpec]] = None,
    cfg: Optional[RunConfig] = None,
) -> list[SiteResult]:
    """Check ``email`` against every site in parallel. Returns all results."""
    cfg = cfg or RunConfig()
    specs = specs if specs is not None else registry()
    sem = asyncio.Semaphore(cfg.concurrency)

    async with build_client(cfg) as client:
        tasks = [_run_one(spec, email, client, sem, cfg) for spec in specs]
        return await asyncio.gather(*tasks)


def run_sync(
    email: str,
    specs: Optional[list[SiteSpec]] = None,
    cfg: Optional[RunConfig] = None,
) -> list[SiteResult]:
    return asyncio.run(run(email, specs=specs, cfg=cfg))
