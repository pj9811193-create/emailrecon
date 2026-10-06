# emailrecon

[![CI](https://github.com/pj9811193-create/emailrecon/actions/workflows/ci.yml/badge.svg)](https://github.com/pj9811193-create/emailrecon/actions/workflows/ci.yml)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[![Live demo](https://img.shields.io/badge/demo-GitHub%20Pages-3fb950.svg)](https://pj9811193-create.github.io/emailrecon/)

Parallel **email OSINT** — check whether an email address is registered across
**116 public websites** at once, and print the ones that hit.

It fires every check concurrently (async `httpx`), so a full 116-site sweep
finishes in seconds rather than minutes.

```
$ python -m emailrecon someone@example.com

  FOUND     gravatar     gravatar.com    profile   [Jane Doe / https://gravatar.com/janedoe]
  FOUND     spotify      spotify.com     register
  FOUND     pinterest    pinterest.com   register
  -         github       github.com      register
  RATE      instagram    instagram.com   register  (non-json)
  ...
  3 found  ·  41 clean  ·  12 inconclusive  ·  48 rate-limited  ·  12 errors  (of 116 sites)
```

---

## What it does (and doesn't)

For each site it asks the **same public question the site's own sign-up or
"forgot password" form asks**: *is this address already in use here?* Nothing
more.

- It does **not** log in, guess or brute-force passwords, send email, or read
  anyone's private data.
- A hit means an account **may** exist on that service — it is a **lead to
  verify**, not proof.
- When a site blocks automated requests or changes its flow, the checker
  returns `unknown` (inconclusive) instead of guessing, so you never get a
  confident wrong answer.

Use it on your own addresses, or where you have authorisation. Checking
someone else's footprint without a lawful basis can breach computer-misuse and
data-protection law (in India: the IT Act 2000 and the DPDP Act 2023).

---

## Install

Requires Python 3.10+.

```bash
pip install -r requirements.txt      # httpx, beautifulsoup4
```

Then run it from the project folder (or `pip install .` for a global
`emailrecon` command).

---

## Usage

```bash
# Basic sweep of all 116 sites
python -m emailrecon someone@example.com

# Only social + music sites, 50 at a time, 10s per site
python -m emailrecon someone@example.com --category social --category music \
        -c 50 -t 10

# A specific handful
python -m emailrecon someone@example.com --only gravatar,github,spotify

# Save everything
python -m emailrecon someone@example.com --json results.json --csv results.csv

# Only show the hits
python -m emailrecon someone@example.com --quiet

# See every site and category
python -m emailrecon --list

# Route through a proxy
python -m emailrecon someone@example.com --proxy http://127.0.0.1:8080
```

### Options

| Flag | Meaning | Default |
|------|---------|---------|
| `-c, --concurrency` | max simultaneous requests | 30 |
| `-t, --timeout` | seconds per site | 15 |
| `-r, --retries` | retries per site on failure | 1 |
| `--category` | only these categories (repeatable) | all |
| `--only` | comma-separated site names to run | all |
| `--exclude` | comma-separated site names to skip | none |
| `--proxy` | HTTP(S) proxy URL | env |
| `--json`, `--csv` | write full results to a file | off |
| `-q, --quiet` | show only hits | off |
| `--no-color` | plain output | off |
| `--list` | list sites and exit | — |

### Status codes

| Status | Meaning |
|--------|---------|
| `FOUND` | service says the address is registered |
| `-` | service says the address is free / not found |
| `?` | inconclusive (couldn't tell) |
| `RATE` | rate-limited or blocked by the site |
| `ERR` | network/timeout error |

### Evidence and confidence

A hit is graded, so you can tell a strong signal from a weak one. Every
`FOUND` result carries two labels:

- **confidence** — `high` (the service returned real account data: a Gravatar
  profile, Adobe recovery contacts, an Odnoklassniki masked name, a Proton key),
  `medium` (the service explicitly says the address is registered), or `low`
  (an inferred/heuristic signal that can be noisy — e.g. Taringa, VSCO,
  Wattpad, Ello, Samsung, SEOclerks, Flickr).
- **evidence** — `direct` (actual account data came back) or `inferred`
  (deduced from an availability response).

Each result also carries a `verify_url` — open it to confirm the hit by hand.
The CLI prints the grade for hits (`FOUND  github  github.com  register
<medium/inferred>`), and JSON/CSV include `confidence`, `evidence` and
`verify_url`.

---

## Browser app (emailrecon.html)

The same 116 checks ship as a single-file web app. It has three ways to run:

- **Backend (recommended)** — start the small local server and point the app at
  it. The checks run *server-side*, so there is no CORS and cookies work
  normally — the cookie-based checks (LastPass, Eventbrite, Strava, Zoho, …)
  work here too. This is the mode that removes the browser's limitations.
- **Live** — the page calls the sites directly. Browsers block cross-origin
  requests, so this needs a CORS proxy.
- **Demo** — simulated results for exploring the interface with no network.

You can also run the Python tool and **Import** its `results.json` into the app
to view, filter and export it.

```bash
# from the project folder, with emailrecon.html beside server.py
python server.py                 # serves http://127.0.0.1:8777/
# then open that URL, leave Mode = Backend, and Run
```

The server exposes `GET /api/check?email=…` (plus `/api/sites`) so you can also
script it.

---

## Limitations

Endpoints change, and sites add captchas and bot defences, so some checkers
return `unknown` on any given day — that is expected and by design. A `FOUND`
result is graded evidence, not proof: check the `confidence` label and follow
the `verify_url` before you rely on it. Re-check the sites you care about with
`--only`.

---

## Using it as a library

```python
from emailrecon import run_sync

for r in run_sync("someone@example.com"):
    if r.status == "registered":
        print(r.name, r.domain, r.detail)
```

`run_sync` returns a list of `SiteResult` dataclasses (`name`, `domain`,
`category`, `method`, `status`, `detail`, `error`, `elapsed_ms`). For full
async control use `await run(...)` with a `RunConfig`.

---

## Categories

`social`, `mail`, `dev`, `shopping`, `music`, `media`, `learning`,
`productivity`, `software`, `crm`, `jobs`, `travel`, `medical`, `osint`,
`adult`, `forum`. Run `python -m emailrecon --list` for the full breakdown.

---

## Tests

```bash
PYTHONPATH=. python tests/test_parsing.py
```

Feeds each checker canned HTTP responses (no network) and asserts it maps them
to the right status — 13/13 passing.

---

## How it works

`engine.py` owns the async client, a concurrency semaphore, per-site timeouts
and retries, and classifies each response. `sites.py` is a registry of small
checker functions, each decorated with `@site(name, domain, category, method)`.
Adding a site is one function — see the MyBB forums, where 25 sites share one
factory.

The endpoint patterns are based on the open-source project **holehe** by
megadose (GPL-3.0), whose site knowledge this tool builds on; the engine, CLI,
and checker implementations here are original. This project is therefore also
licensed **GPL-3.0**.
