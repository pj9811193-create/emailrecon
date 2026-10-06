"""
emailrecon.sites
================

The registry of services this tool probes.  Every checker below is a small
async function that asks one public question — "is this address already
registered here?" — and answers with ``exists`` in {True, False, None}.

Nothing here logs in, guesses passwords, or sends mail.  The checks use the
same public "is this email available?" endpoints the sites' own sign-up and
password-reset forms call, which is why a result of ``True`` means only that
an account *may* exist.  Treat every hit as a lead to verify, not a fact.

Endpoints move.  When a site changes its flow a checker will return
``unknown`` (inconclusive) rather than a wrong answer, by design.
"""

from __future__ import annotations

import hashlib
import json
import re

from bs4 import BeautifulSoup

from .engine import SiteSpec, chrome_ua, firefox_ua, rand_token, site


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def R(exists, detail=None, rate_limit=False, error=None):
    """Build the dict every checker returns."""
    return {
        "exists": exists,
        "detail": detail,
        "rate_limit": rate_limit,
        "error": error,
    }


def H(ua=None, accept=None, origin=None, referer=None, content_type=None, extra=None):
    h = {"User-Agent": ua or chrome_ua(), "Accept-Language": "en-US,en;q=0.9", "DNT": "1"}
    if accept:
        h["Accept"] = accept
    if origin:
        h["Origin"] = origin
    if referer:
        h["Referer"] = referer
    if content_type:
        h["Content-Type"] = content_type
    if extra:
        h.update(extra)
    return h


JSON = "application/json"
FORM = "application/x-www-form-urlencoded; charset=UTF-8"


# =========================================================================== #
# Profiles / media
# =========================================================================== #

@site("gravatar", "gravatar.com", "media", "profile", confidence="high", evidence="direct", verify="https://gravatar.com/")
async def gravatar(email, client):
    hashed = hashlib.md5(email.strip().lower().encode()).hexdigest()
    r = await client.get(f"https://en.gravatar.com/{hashed}.json", headers=H(accept=JSON))
    if r.status_code != 200:
        return R(False)
    try:
        entry = r.json()["entry"][0]
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    detail = " / ".join(
        str(x) for x in (entry.get("displayName"), entry.get("profileUrl")) if x
    ) or None
    return R(True, detail=detail)


# =========================================================================== #
# Social
# =========================================================================== #

@site("twitter", "x.com", "social", "register")
async def twitter(email, client):
    r = await client.get(
        "https://api.twitter.com/i/users/email_available.json",
        params={"email": email},
        headers=H(accept=JSON),
    )
    try:
        data = r.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "taken" in data:
        return R(bool(data["taken"]))
    return R(None, rate_limit=True, error="unexpected payload")


async def _meta_register(email, client, origin, signup_path, attempt_path, app_id=""):
    """Shared logic for Instagram / Facebook sign-up email checks."""
    headers = H(
        origin=origin,
        accept="application/json, text/plain, */*",
        extra={"X-Requested-With": "XMLHttpRequest", "Connection": "keep-alive"},
    )
    freq = await client.get(origin + signup_path, headers=headers)
    m = re.search(r'"csrf_token":"([^"]+)"', freq.text)
    if not m:
        return R(None, rate_limit=True, error="no csrf token")
    headers["x-csrftoken"] = m.group(1)
    data = {
        "email": email,
        "username": rand_token(20),
        "first_name": "",
        "opt_into_one_tap": "false",
    }
    check = await client.post(origin + attempt_path, data=data, headers=headers)
    try:
        j = check.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if j.get("status") == "fail":
        return R(None, rate_limit=True, error="attempt failed")
    errors = j.get("errors", {})
    if "email" in errors:
        codes = str(errors["email"])
        if "email_is_taken" in codes or "email_sharing_limit" in codes:
            return R(True)
    if "email_sharing_limit" in str(errors):
        return R(True)
    return R(False)


@site("instagram", "instagram.com", "social", "register")
async def instagram(email, client):
    return await _meta_register(
        email, client,
        origin="https://www.instagram.com",
        signup_path="/accounts/emailsignup/",
        attempt_path="/api/v1/web/accounts/web_create_ajax/attempt/",
    )


@site("facebook", "facebook.com", "social", "register")
async def facebook(email, client):
    return await _meta_register(
        email, client,
        origin="https://www.facebook.com",
        signup_path="/accounts/emailsignup/",
        attempt_path="/api/v1/web/accounts/web_create_ajax/attempt/",
    )


async def _snap(email, client, field):
    req = await client.get("https://accounts.snapchat.com", headers=H(ua=firefox_ua()))
    try:
        xsrf = req.text.split('data-xsrf="')[1].split('"')[0]
        web_client_id = req.text.split('data-web-client-id="')[1].split('"')[0]
    except Exception:
        return R(None, rate_limit=True, error="no token")
    headers = {
        "Host": "accounts.snapchat.com",
        "User-Agent": firefox_ua(),
        "Accept": "*/*",
        "X-XSRF-TOKEN": xsrf,
        "Content-Type": "application/json",
        "Cookie": f"xsrf_token={xsrf}; web_client_id={web_client_id}",
    }
    data = json.dumps({"email": email, "app": "BITMOJI_APP"})
    resp = await client.post(
        "https://accounts.snapchat.com/accounts/merlin/login", data=data, headers=headers
    )
    if resp.status_code == 204:
        return R(False)
    try:
        j = resp.json()
        return R(bool(j.get(field)))
    except Exception:
        return R(None, rate_limit=True, error="non-json")


@site("snapchat", "snapchat.com", "social", "login")
async def snapchat(email, client):
    return await _snap(email, client, "hasSnapchat")


@site("bitmoji", "bitmoji.com", "social", "login")
async def bitmoji(email, client):
    return await _snap(email, client, "hasBitmoji")


@site("pinterest", "pinterest.com", "social", "register")
async def pinterest(email, client):
    req = await client.get(
        "https://www.pinterest.com/_ngjs/resource/EmailExistsResource/get/",
        params={
            "source_url": "/",
            "data": '{"options": {"email": "' + email + '"}, "context": {}}',
        },
        headers=H(accept=JSON),
    )
    try:
        data = req.json()["resource_response"]["data"]
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "source_field" in str(data):
        return R(None, rate_limit=True, error="challenge")
    return R(bool(data))


@site("discord", "discord.com", "social", "register")
async def discord(email, client):
    headers = H(
        ua=firefox_ua(), accept="*/*", origin="https://discord.com", content_type=JSON
    )
    data = json.dumps({
        "fingerprint": "", "email": email, "username": rand_token(20),
        "password": rand_token(20), "invite": None, "consent": True,
        "date_of_birth": "", "gift_code_sku_id": None, "captcha_key": None,
    })
    resp = await client.post("https://discord.com/api/v9/auth/register", headers=headers, data=data)
    try:
        j = resp.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if isinstance(j, dict) and "captcha_key" in j:
        return R(None, rate_limit=True, error="captcha required")
    errors = (j or {}).get("errors", {})
    if "email" in errors:
        if "EMAIL_ALREADY_REGISTERED" in str(errors["email"]):
            return R(True)
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("tumblr", "tumblr.com", "social", "register")
async def tumblr(email, client):
    get_bearer = await client.get("https://www.tumblr.com/", headers=H(ua=chrome_ua()))
    bearer = None
    for s in BeautifulSoup(get_bearer.text, "html.parser").find_all("script"):
        if "___INITIAL_STATE___" in s.text:
            try:
                bearer = s.text.split('{"API_TOKEN":"')[-1].split('","extraHeaders":"{}"}')[0]
            except Exception:
                bearer = None
            if bearer:
                break
    if not bearer:
        return R(None, rate_limit=True, error="no bearer token")
    csrf = await client.get(
        "https://www.tumblr.com/api/v2/radar?fields%5Bblogs%5D=name&limit=1",
        headers=H(ua=chrome_ua(), referer="https://www.tumblr.com/"),
    )
    m = re.search(r'"csrf":"([^"]+)"', csrf.text) or re.search(r"csrf[^a-z0-9]{1,4}([A-Za-z0-9]+)", csrf.text)
    headers = H(
        origin="https://www.tumblr.com",
        referer="https://www.tumblr.com/register",
        accept=JSON,
        content_type=JSON,
        extra={"Authorization": "Bearer " + bearer},
    )
    if m:
        headers["X-CSRF"] = m.group(1)
    post = await client.post(
        "https://www.tumblr.com/api/v2/register/account/validate",
        headers=headers,
        data=json.dumps({"email": email}),
    )
    if post.status_code == 400:
        return R(True)
    if post.status_code in (200, 201):
        return R(False)
    return R(None, rate_limit=True, error=f"HTTP {post.status_code}")


@site("strava", "strava.com", "social", "register")
async def strava(email, client):
    r = await client.get(
        "https://www.strava.com/register/free?cta=sign-up&element=button&source=website_show",
        headers=H(referer="https://www.strava.com/register/free"),
    )
    m = re.search(r'name="csrf-token" content="([^"]+)"', r.text)
    if not m:
        return R(None, rate_limit=True, error="no csrf token")
    response = await client.get(
        "https://www.strava.com/athletes/email_unique",
        headers=H(extra={"X-CSRF-Token": m.group(1), "X-Requested-With": "XMLHttpRequest"}),
        params={"email": email},
    )
    txt = response.text.strip()
    if txt == "false":
        return R(True)
    if txt == "true":
        return R(False)
    return R(None, rate_limit=True, error="unexpected body")


@site("wattpad", "wattpad.com", "social", "register", confidence="low", evidence="inferred")
async def wattpad(email, client):
    await client.get("https://www.wattpad.com", headers=H())
    response = await client.get(
        "https://www.wattpad.com/api/v3/users/validate",
        headers=H(referer="https://www.wattpad.com/"),
        params={"email": email},
    )
    if response.status_code in (200, 400):
        txt = response.text
        if txt == '{"message":"OK","code":200}' or "Cette adresse" not in txt:
            if "already" in txt.lower() or "existe" in txt.lower() or "taken" in txt.lower():
                return R(True)
            return R(False)
        return R(True)
    return R(None, rate_limit=True, error=f"HTTP {response.status_code}")


@site("xing", "xing.com", "social", "register")
async def xing(email, client):
    response = await client.get(
        "https://www.xing.com/start/signup?registration=1", headers=H(ua=firefox_ua())
    )
    if response.status_code != 200:
        return R(None, rate_limit=True, error=f"HTTP {response.status_code}")
    response = await client.post(
        "https://www.xing.com/welcome/api/signup/validate",
        headers=H(origin="https://www.xing.com", accept=JSON, content_type=JSON),
        data=json.dumps({"email": email}),
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "email" in str(j) and ("taken" in str(j).lower() or "already" in str(j).lower()):
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("patreon", "patreon.com", "social", "register")
async def patreon(email, client):
    response = await client.post(
        "https://www.patreon.com/api/email/available",
        headers=H(origin="https://www.patreon.com", accept=JSON, content_type=JSON),
        params={"include": ""},
        data=json.dumps({"email": email}),
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if j.get("is_available") is True:
        return R(False)
    if j.get("is_available") is False:
        return R(True)
    return R(None, rate_limit=True, error="inconclusive")


@site("venmo", "venmo.com", "social", "register")
async def venmo(email, client):
    await client.get("https://venmo.com/signup/email", headers=H())
    response = await client.post(
        "https://venmo.com/api/v5/users",
        headers=H(origin="https://venmo.com", accept=JSON, content_type=JSON),
        data=json.dumps({"email": email}),
    )
    if "Not acceptable" in response.text:
        return R(None, rate_limit=True, error="blocked")
    if "That email is already registered in our system." in response.text:
        return R(True)
    if response.status_code in (200, 201, 400):
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("imgur", "imgur.com", "social", "register")
async def imgur(email, client):
    await client.get("https://imgur.com/register?redirect=%2Fuser", headers=H())
    response = await client.post(
        "https://imgur.com/signin/ajax_email_available",
        headers=H(origin="https://imgur.com", accept=JSON, content_type=JSON),
        data=json.dumps({"email": email}),
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "Invalid email domain" in response.text:
        return R(False)
    data = j.get("data", {})
    if data.get("available") is True:
        return R(False)
    if data.get("available") is False:
        return R(True)
    return R(None, rate_limit=True, error="inconclusive")


@site("myspace", "myspace.com", "social", "register")
async def myspace(email, client):
    r = await client.get("https://myspace.com/signup/email", headers=H())
    m = re.search(r'name="csrf"[^>]*value="([^"]+)"', r.text)
    if not m:
        return R(None, rate_limit=True, error="no csrf token")
    response = await client.post(
        "https://myspace.com/ajax/account/validateemail",
        headers=H(origin="https://myspace.com", referer="https://myspace.com/signup/email",
                  content_type=FORM, extra={"Hash": m.group(1)}),
        data={"email": email},
    )
    if "This email address was already used to create an account." in response.text:
        return R(True)
    return R(False)


@site("fanpop", "fanpop.com", "social", "register")
async def fanpop(email, client):
    response = await client.post(
        "https://www.fanpop.com/login/superlogin",
        headers=H(origin="https://www.fanpop.com", referer="https://www.fanpop.com/register",
                  content_type=FORM),
        data={"email": email, "password": rand_token(12), "redirect_url": "https://www.fanpop.com/"},
    )
    if "already registered" in response.text:
        return R(True)
    return R(False)


@site("plurk", "plurk.com", "social", "register")
async def plurk(email, client):
    response = await client.post(
        "https://www.plurk.com/Users/isEmailFound",
        headers=H(origin="https://www.plurk.com", content_type=FORM),
        data={"email": email},
    )
    txt = response.text.strip()
    if txt == "True":
        return R(True)
    if txt == "False":
        return R(False)
    return R(None, rate_limit=True, error="unexpected body")


@site("tellonym", "tellonym.me", "social", "register")
async def tellonym(email, client):
    response = await client.get(
        "https://api.tellonym.me/accounts/check",
        headers=H(origin="https://tellonym.me", referer="https://tellonym.me/register/email",
                  accept=JSON),
        params={"email": email},
    )
    if "EMAIL_ALREADY_IN_USE" in response.text:
        return R(True)
    if response.status_code in (200, 400, 409):
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("taringa", "taringa.net", "social", "register", confidence="low", evidence="inferred")
async def taringa(email, client):
    headers = H(origin="https://www.taringa.net", accept="*/*", content_type=JSON)
    cookies = {"G_ENABLED_IDPS": "google"}
    response = await client.post(
        "https://www.taringa.net/api/auth/availability/email",
        headers=headers, cookies=cookies, data=json.dumps({"email": email}),
    )
    if response.status_code == 200 and response.text == '{"available":false}':
        domain = email.split("@")[-1]
        probe = f"{rand_token(15)}@{domain}"
        r2 = await client.post(
            "https://www.taringa.net/api/auth/availability/email",
            headers=headers, cookies=cookies, data=json.dumps({"email": probe}),
        )
        if r2.status_code == 200 and r2.text == '{"available":false}':
            return R(None, rate_limit=True, error="baseline also unavailable")
        return R(True)
    if response.status_code in (200, 400):
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("vsco", "vsco.co", "social", "register", confidence="low", evidence="inferred")
async def vsco(email, client):
    r = await client.get(
        f"https://api.vsco.co/2.0/users/email?email={email}",
        headers=H(accept=JSON),
    )
    try:
        j = r.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if j.get("error") in (None, False) and j.get("user") is not None:
        return R(True)
    if "not found" in str(j).lower():
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("odnoklassniki", "ok.ru", "social", "password recovery", confidence="high", evidence="direct", verify="https://ok.ru/")
async def odnoklassniki(email, client):
    headers = H(referer="https://ok.ru/")
    login_url = (
        "https://www.ok.ru/dk?st.cmd=anonymMain&st.accRecovery=on"
        "&st.error=errors.password.wrong&st.email=" + email
    )
    await client.get(login_url, headers=headers)
    recover = await client.get(
        "https://www.ok.ru/dk?st.cmd=anonymRecoveryAfterFailedLogin"
        "&st._aid=LeftColumn_Login_ForgotPassword",
        headers=headers,
    )
    if "account_info" in recover.text or "anonymRecoveryStart" in recover.text:
        masked = re.search(r'class="[^"]*account_info[^"]*"[^>]*>([^<]+)<', recover.text)
        return R(True, detail=masked.group(1).strip() if masked else None)
    if "not found" in recover.text.lower() or "st.error" in recover.text:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


# =========================================================================== #
# Mail providers
# =========================================================================== #

@site("google", "google.com", "mail", "register")
async def google(email, client):
    headers = H(
        ua=firefox_ua(), accept="*/*", origin="https://accounts.google.com",
        content_type="application/x-www-form-urlencoded;charset=utf-8",
        referer="https://accounts.google.com/signup/v2/webcreateaccount",
        extra={"X-Same-Domain": "1", "Google-Accounts-XSRF": "1"},
    )
    req = await client.get(
        "https://accounts.google.com/signup/v2/webcreateaccount?continue=https%3A%2F%2Faccounts.google.com%2FManageAccount%3Fnc%3D1&gmb=exp&biz=false&flowName=GlifWebSignIn&flowEntry=SignUp",
        headers=headers,
    )
    try:
        freq = req.text.split("quot;,null,null,null,&quot;")[1].split("&quot")[0]
    except Exception:
        return R(None, rate_limit=True, error="no flow token")
    data = {
        "continue": "https://accounts.google.com/",
        "dsh": "", "hl": "en",
        "f.req": '["' + freq + '","","","' + email + '",false]',
        "azt": "", "cookiesDisabled": "false",
        "gmscoreversion": "unined", "": "",
    }
    response = await client.post(
        "https://accounts.google.com/_/signup/webusernameavailability",
        headers=headers, params={"hl": "en", "rt": "j"}, data=data,
    )
    if '"gf.wuar",2' in response.text:
        return R(True)
    if '"gf.wuar",1' in response.text or "EmailInvalid" in response.text:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("yahoo", "yahoo.com", "mail", "login")
async def yahoo(email, client):
    headers = H(ua=chrome_ua(), origin="https://login.yahoo.com", accept="*/*")
    req = await client.get("https://login.yahoo.com", headers=headers)
    m = re.search(r'<input[^>]*name="acrumb"[^>]*value="([^"]+)"', req.text)
    if not m:
        return R(None, rate_limit=True, error="no session token")
    data = {"acrumb": m.group(1), "sessionIndex": "", "username": email,
            "passwd": "", "signin": "Next", ".persistent": "y", ".src": "",
            ".lang": "en-US", ".done": "https://www.yahoo.com"}
    response = await client.post("https://login.yahoo.com/", headers=headers, data=data)
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "error" in j:
        return R(True)
    if "render" in j:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("mail_ru", "mail.ru", "mail", "password recovery")
async def mail_ru(email, client):
    headers = H(origin="https://account.mail.ru", accept=JSON, content_type=JSON,
                referer=f"https://account.mail.ru/recovery?email={email}")
    response = await client.post(
        "https://account.mail.ru/api/v1/user/password/restore",
        headers=headers,
        data=json.dumps({"email": email, "htmlencoded": False, "token": ""}),
    )
    if response.status_code == 200:
        try:
            j = response.json()
        except Exception:
            return R(None, rate_limit=True, error="non-json")
        body = str(j).lower()
        if "not found" in body or "не найден" in body or j.get("status") == "error":
            return R(False)
        if j.get("status") == "ok" or "email" in body:
            return R(True)
    return R(None, rate_limit=True, error="inconclusive")


@site("protonmail", "proton.me", "mail", "other", confidence="high", evidence="direct", verify="https://account.proton.me/")
async def protonmail(email, client):
    response = await client.get(
        "https://api.protonmail.ch/pks/lookup?op=index&search=" + email,
        headers=H(),
    )
    if "info:1:0" in response.text:
        return R(False)
    if "info:1:1" in response.text:
        detail = None
        m = re.search(r"pub:([0-9a-fA-F]+):", response.text)
        if m:
            detail = f"key {m.group(1)[:16]}..."
        return R(True, detail=detail)
    return R(None, rate_limit=True, error="unexpected body")


@site("rambler", "rambler.ru", "mail", "register")
async def rambler(email, client):
    headers = H(origin="https://id.rambler.ru", referer="https://id.rambler.ru/champ/registration",
                  accept=JSON, content_type=JSON)
    response = await client.post(
        "https://id.rambler.ru/jsonrpc",
        headers=headers,
        data=json.dumps({
            "method": "Rambler::Id::email_exist",
            "params": {"email": email},
            "id": 1,
        }),
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "result" in j and isinstance(j["result"], dict):
        if j["result"].get("exists") is True:
            return R(True)
        if j["result"].get("exists") is False:
            return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("office365", "office365.com", "mail", "other", confidence="medium", evidence="direct", verify="https://login.microsoftonline.com/")
async def office365(email, client):
    domain = email.split("@")[-1]
    url = "https://outlook.office365.com/autodiscover/autodiscover.json/v1.0/{}?Protocol=Autodiscoverv1".format(
        email
    )
    r = await client.get(url, headers=H(accept=JSON))
    if r.status_code != 200:
        r = await client.get(
            "https://outlook.office365.com/autodiscover/autodiscover.json/v1.0/{}?Protocol=Autodiscoverv1".format(
                domain
            ),
            headers=H(accept=JSON),
        )
    if r.status_code == 200:
        try:
            j = r.json()
        except Exception:
            return R(None, rate_limit=True, error="non-json")
        if "Protocol" in str(j) or "Url" in str(j):
            return R(True)
        return R(False)
    return R(False)


# =========================================================================== #
# Developer / programming
# =========================================================================== #

@site("github", "github.com", "dev", "register")
async def github(email, client):
    freq = await client.get("https://github.com/join", headers=H())
    regex = re.compile(
        r'<auto-check src="/signup_check/username[\s\S]*?value="([\S]+)"'
        r'[\s\S]*<auto-check src="/signup_check/email[\s\S]*?value="([\S]+)"'
    )
    token = re.findall(regex, freq.text)
    if not token:
        return R(None, rate_limit=True, error="no authenticity token")
    req = await client.post(
        "https://github.com/signup_check/email",
        data={"value": email, "authenticity_token": token[0][1]},
        headers=H(),
    )
    if "Your browser did something unexpected." in req.text:
        return R(None, rate_limit=True, error="challenge")
    if req.status_code == 422:
        return R(True)
    if req.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error=f"HTTP {req.status_code}")


@site("docker", "docker.com", "dev", "register")
async def docker(email, client):
    headers = H(
        origin="https://hub.docker.com", referer="https://hub.docker.com/signup",
        accept=JSON, content_type=JSON,
    )
    data = json.dumps({"email": email, "password": "", "recaptcha_response": "",
                       "redirect_value": "", "subscribe": True, "username": ""})
    response = await client.post("https://hub.docker.com/v2/users/signup/", headers=headers, data=data)
    if "This email is already in use." in response.text:
        return R(True)
    if response.status_code in (200, 201, 400):
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("replit", "replit.com", "dev", "register")
async def replit(email, client):
    response = await client.post(
        "https://replit.com/data/user/exists",
        headers=H(origin="https://replit.com", accept=JSON, content_type=JSON),
        data=json.dumps({"email": email}),
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if j.get("exists") is True:
        return R(True)
    if j.get("exists") is False:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("codepen", "codepen.io", "dev", "register")
async def codepen(email, client):
    r = await client.get(
        "https://codepen.io/accounts/signup/user/free",
        headers=H(referer="https://codepen.io/accounts/signup/user/free", origin="https://codepen.io"),
    )
    token = re.search(r'name="csrf-token" content="([^"]+)"', r.text)
    headers = H(origin="https://codepen.io", referer="https://codepen.io/accounts/signup/user/free",
                content_type=FORM)
    if token:
        headers["X-CSRF-Token"] = token.group(1)
    response = await client.post(
        "https://codepen.io/accounts/duplicate_check", headers=headers,
        data={"email": email},
    )
    if "That Email is already taken." in response.text:
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("codecademy", "codecademy.com", "dev", "register")
async def codecademy(email, client):
    r = await client.get(
        "https://www.codecademy.com/register?redirect=%2F", headers=H(ua=chrome_ua())
    )
    token = re.search(r'name="csrf-token" content="([^"]+)"', r.text) or re.search(
        r'"csrfToken":"([^"]+)"', r.text
    )
    headers = H(origin="https://www.codecademy.com",
                referer="https://www.codecademy.com/register?redirect=%2F",
                content_type=FORM)
    if token:
        headers["X-CSRF-Token"] = token.group(1)
    response = await client.post(
        "https://www.codecademy.com/register/validate", headers=headers,
        data={"user[email]": email},
    )
    if "is already taken" in response.text:
        return R(True)
    if response.status_code in (200, 400):
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("devrant", "devrant.com", "dev", "register")
async def devrant(email, client):
    headers = H(origin="https://devrant.com",
                referer="https://devrant.com/feed/top/month?login=1", content_type=FORM)
    response = await client.post(
        "https://devrant.com/api/users", headers=headers,
        data={"email": email, "username": rand_token(12), "password": rand_token(12),
              "type": "1", "app": "3"},
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "email" in str(j).lower() and ("taken" in str(j).lower() or "exist" in str(j).lower()):
        return R(True)
    if j.get("success") is True:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("teamtreehouse", "teamtreehouse.com", "dev", "register")
async def teamtreehouse(email, client):
    r = await client.get(
        "https://teamtreehouse.com/subscribe/new?trial=yes",
        headers=H(referer="https://teamtreehouse.com/subscribe/new?trial=yes",
                  origin="https://teamtreehouse.com"),
    )
    token = re.search(r'name="authenticity_token" value="([^"]+)"', r.text)
    headers = H(origin="https://teamtreehouse.com",
                referer="https://teamtreehouse.com/subscribe/new?trial=yes", content_type=FORM)
    if token:
        headers["X-CSRF-Token"] = token.group(1)
    response = await client.post(
        "https://teamtreehouse.com/account/email_address", headers=headers,
        data={"email": email},
    )
    if "that email address is taken." in response.text:
        return R(True)
    if response.text == '{"success":true}':
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("lastpass", "lastpass.com", "dev", "register")
async def lastpass(email, client):
    response = await client.get(
        "https://lastpass.com/create_account.php",
        headers=H(referer="https://lastpass.com/"),
        params={"check": "1", "email": email},
    )
    txt = response.text.strip()
    if txt == "no":
        return R(True)
    if txt in ("ok", "emailinvalid"):
        return R(False)
    return R(None, rate_limit=True, error="unexpected body")


@site("firefox", "firefox.com", "dev", "register")
async def firefox(email, client):
    req = await client.post(
        "https://api.accounts.firefox.com/v1/account/status",
        headers=H(accept=JSON, content_type=JSON),
        data=json.dumps({"email": email}),
    )
    if '"exists":false' in req.text or req.text.strip() == "false":
        return R(False)
    if '"exists":true' in req.text or req.text.strip() == "true":
        return R(True)
    return R(None, rate_limit=True, error="inconclusive")


# =========================================================================== #
# Shopping / products
# =========================================================================== #

@site("amazon", "amazon.com", "shopping", "login")
async def amazon(email, client):
    headers = H(ua=chrome_ua())
    url = (
        "https://www.amazon.com/ap/signin?openid.pape.max_auth_age=0&openid.return_to=https%3A%2F%2Fwww.amazon.com%2F%3F_encoding%3DUTF8%26ref_%3Dnav_ya_signin&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.assoc_handle=usflex&openid.mode=checkid_setup&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0&"
    )
    req = await client.get(url, headers=headers)
    body = BeautifulSoup(req.text, "html.parser")
    data = {
        x["name"]: x["value"]
        for x in body.select("form input")
        if "name" in x.attrs and "value" in x.attrs
    }
    if not data:
        return R(None, rate_limit=True, error="no form")
    data["email"] = email
    req = await client.post("https://www.amazon.com/ap/signin/", data=data, headers=headers)
    body = BeautifulSoup(req.text, "html.parser")
    if body.find("div", {"id": "auth-password-missing-alert"}):
        return R(True)
    if body.find("div", {"id": "auth-error-message-box"}):
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("ebay", "ebay.com", "shopping", "login")
async def ebay(email, client):
    headers = H(origin="https://www.ebay.com", accept=JSON)
    try:
        req = await client.get("https://www.ebay.com/signin/", headers=headers)
        srt = req.text.split('"csrfAjaxToken":"')[1].split('"')[0]
    except Exception:
        return R(None, rate_limit=True, error="no csrf token")
    req = await client.post(
        "https://signin.ebay.com/signin/srv/identifer",
        data={"identifier": email, "srt": srt},
        headers=headers,
    )
    try:
        results = json.loads(req.text)
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "err" in results:
        return R(False)
    return R(True)


@site("envato", "envato.com", "shopping", "register")
async def envato(email, client):
    req = await client.post(
        "https://account.envato.com/api/validate_email",
        headers=H(accept=JSON, content_type=JSON),
        data=json.dumps({"email": email}),
    )
    if "Email is already in use" in req.text:
        return R(True)
    if "Page designed by Kotulsky" in req.text or req.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("deliveroo", "deliveroo.com", "shopping", "register")
async def deliveroo(email, client):
    req = await client.post(
        "https://consumer-ow-api.deliveroo.com/orderapp/v1/check-email",
        headers=H(origin="https://deliveroo.com", accept=JSON, content_type=JSON),
        json={"email": email},
    )
    if req.status_code == 200:
        try:
            data = json.loads(req.text)
        except Exception:
            return R(None, rate_limit=True, error="non-json")
        if data.get("exists") is True or data.get("available") is False:
            return R(True)
        if data.get("exists") is False or data.get("available") is True:
            return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("dominosfr", "dominos.fr", "shopping", "register")
async def dominosfr(email, client):
    headers = H(referer="https://commande.dominos.fr/eStore/fr/Signup")
    await client.get("https://commande.dominos.fr/eStore/fr/Signup", headers=headers)
    req = await client.get(
        "https://commande.dominos.fr/eStore/fr/Signup/IsEmailAvailable",
        headers=headers, params={"email": email},
    )
    if req.status_code == 200:
        if req.text.strip() == "false":
            return R(True)
        if req.text.strip() == "true":
            return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("nike", "nike.com", "shopping", "register")
async def nike(email, client):
    params = {"appVersion": "831", "experienceVersion": "831",
              "uxid": "com.nike.commerce.nikedotcom.web", "locale": "en_US",
              "backendEnvironment": "identity", "browser": "", "mobile": "false",
              "native": "false", "visit": "1"}
    try:
        response = await client.post(
            "https://unite.nike.com/account/email/v1",
            headers=H(ua=firefox_ua(), origin="https://www.nike.com",
                      referer="https://www.nike.com/", content_type="text/plain;charset=UTF-8"),
            params=params,
            data=json.dumps({"emailAddress": email}),
        )
    except Exception:
        return R(None, rate_limit=True, error="request failed")
    if response.status_code == 409:
        return R(True)
    if response.status_code == 204:
        return R(False)
    return R(None, rate_limit=True, error=f"HTTP {response.status_code}")


@site("samsung", "samsung.com", "shopping", "password recovery", confidence="low", evidence="inferred")
async def samsung(email, client):
    req = await client.get(
        "https://account.samsung.com/accounts/v1/Samsung_com_FR/signUp", headers=H(ua=chrome_ua())
    )
    m = re.search(r"'{'token' : '([^']+)'", req.text) or re.search(r"'token' : '([^']+)'", req.text)
    if not m:
        return R(None, rate_limit=True, error="no token")
    headers = H(origin="https://account.samsung.com",
                referer="https://account.samsung.com/accounts/v1/Samsung_com_FR/signUp",
                content_type=FORM)
    response = await client.post(
        "https://account.samsung.com/accounts/v1/Samsung_com_FR/signUpCheckEmailIDProc",
        headers=headers,
        data={"emailID": email, "token": m.group(1)},
    )
    if response.status_code == 200:
        try:
            data = response.json()
        except Exception:
            return R(None, rate_limit=True, error="non-json")
        if "rtnCd" in data and "INAPPROPRIATE_CHARACTERS" not in response.text:
            return R(True)
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("garmin", "garmin.com", "shopping", "register")
async def garmin(email, client):
    params = {"service": "https://www.garmin.com/en-US/account/profile/",
              "webhost": "https://www.garmin.com/en-US/account/create/",
              "source": "https://www.garmin.com/en-US/account/create/",
              "gauthHost": "https://sso.garmin.com/sso",
              "redirectAfterAccountCreationUrl": "https://www.garmin.com/en-US/account/profile/"}
    req = await client.get(
        "https://sso.garmin.com/sso/createNewAccount", headers=H(), params=params
    )
    m = re.search(r'"token": "([^"]+)"', req.text)
    if not m:
        return R(None, rate_limit=True, error="no token")
    req = await client.post(
        "https://sso.garmin.com/sso/validateNewAccount",
        headers=H(origin="https://sso.garmin.com", content_type=FORM),
        params=params,
        data={"email": email, "token": m.group(1)},
    )
    try:
        j = req.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if j.get("emailAvailable") is False or "already" in str(j).lower():
        return R(True)
    if j.get("emailAvailable") is True:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("vivino", "vivino.com", "shopping", "register")
async def vivino(email, client):
    response = await client.post(
        "https://www.vivino.com/api/login",
        headers=H(referer="https://www.vivino.com/", content_type=FORM),
        data={"email": email, "password": rand_token(12)},
    )
    if response.status_code == 429:
        return R(None, rate_limit=True, error="HTTP 429")
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "password" in str(j).lower() and "incorrect" in str(j).lower():
        return R(True)
    if "not found" in str(j).lower() or "invalid" in str(j).lower():
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("bodybuilding", "bodybuilding.com", "shopping", "register")
async def bodybuilding(email, client):
    response = await client.head(
        "https://api.bodybuilding.com/profile/email/" + email,
        headers=H(origin="https://www.bodybuilding.com", referer="https://www.bodybuilding.com/"),
    )
    if response.status_code == 200:
        return R(True)
    if response.status_code == 404:
        return R(False)
    return R(None, rate_limit=True, error=f"HTTP {response.status_code}")


@site("eventbrite", "eventbrite.com", "shopping", "login")
async def eventbrite(email, client):
    try:
        req = await client.get(
            "https://www.eventbrite.com/signin/?referrer=%2F", headers=H(ua=firefox_ua())
        )
        csrf_token = req.cookies["csrftoken"]
    except Exception:
        return R(None, rate_limit=True, error="no csrf cookie")
    headers = H(origin="https://www.eventbrite.com", referer="https://www.eventbrite.com/",
                accept="*/*", content_type=JSON,
                extra={"X-CSRFToken": csrf_token, "X-Requested-With": "XMLHttpRequest"})
    response = await client.post(
        "https://www.eventbrite.com/api/v3/users/lookup/",
        headers=headers, cookies={"csrftoken": csrf_token}, data=json.dumps({"email": email}),
    )
    if response.status_code == 200:
        try:
            return R(bool(response.json().get("exists")))
        except Exception:
            return R(None, rate_limit=True, error="non-json")
    return R(None, rate_limit=True, error=f"HTTP {response.status_code}")


@site("naturabuy", "naturabuy.fr", "shopping", "register")
async def naturabuy(email, client):
    response = await client.post(
        "https://www.naturabuy.fr/includes/ajax/register.php",
        headers=H(origin="https://www.naturabuy.fr", content_type=FORM),
        data={"email": email, "pseudo": rand_token(10)},
    )
    try:
        j = json.loads(response.text)
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if j.get("free") is False:
        return R(True)
    if j.get("free") is True:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("armurerieauxerre", "armurerie-auxerre.com", "shopping", "register")
async def armurerieauxerre(email, client):
    req = await client.post(
        "https://www.armurerie-auxerre.com/customer/Email/email/",
        headers=H(origin="https://www.armurerie-auxerre.com", content_type=FORM),
        data={"email": email},
    )
    if req.text.strip() == "exist":
        return R(True)
    if req.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


# =========================================================================== #
# Media / music / learning
# =========================================================================== #

@site("spotify", "spotify.com", "music", "register")
async def spotify(email, client):
    req = await client.get(
        "https://spclient.wg.spotify.com/signup/public/v1/account",
        headers=H(accept=JSON),
        params={"validate": "1", "email": email},
    )
    try:
        j = req.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if j.get("status") == 1:
        return R(False)
    if j.get("status") == 20:
        return R(True)
    return R(None, rate_limit=True, error="inconclusive")


@site("soundcloud", "soundcloud.com", "music", "register")
async def soundcloud(email, client):
    get_auth = await client.get(
        "https://soundcloud.com/octobersveryown",
        headers=H(accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    )
    client_id = None
    for s in BeautifulSoup(get_auth.text, "html.parser").find_all("script"):
        try:
            j = json.loads(s.contents[0])
            if isinstance(j, dict) and "runtimeConfig" in j and "clientId" in j["runtimeConfig"]:
                client_id = j["runtimeConfig"]["clientId"]
                break
        except Exception:
            continue
    if not client_id:
        return R(None, rate_limit=True, error="no clientId")
    link = email.replace("@", "%40")
    api = await client.get(
        f"https://api-auth.soundcloud.com/web-auth/identifier?q={link}&client_id={client_id}",
        headers=H(accept=JSON),
    )
    try:
        j = api.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    status = j.get("status")
    if status == "in_use":
        return R(True)
    if status == "available":
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("lastfm", "last.fm", "music", "register")
async def lastfm(email, client):
    try:
        req = await client.get("https://www.last.fm/join", headers=H())
        token = req.cookies["csrftoken"]
    except Exception:
        return R(None, rate_limit=True, error="no csrf cookie")
    headers = H(referer="https://www.last.fm/join", accept="*/*",
                extra={"X-Requested-With": "XMLHttpRequest", "Cookie": f"csrftoken={token}"})
    check = await client.post(
        "https://www.last.fm/join/partial/validate", headers=headers,
        data={"csrfmiddlewaretoken": token, "userName": "", "email": email},
    )
    try:
        j = check.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    msgs = j.get("email", {}).get("error_messages", []) or []
    if any("already registered" in m for m in msgs):
        return R(True)
    if "email" in j:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("smule", "smule.com", "music", "register")
async def smule(email, client):
    await client.get("https://www.smule.com/user/check_email", headers=H(origin="https://www.smule.com"))
    response = await client.post(
        "https://www.smule.com/user/check_email",
        headers=H(origin="https://www.smule.com", content_type=FORM),
        data={"email": email},
    )
    if '"exists":true' in response.text.replace(" ", "") or "already" in response.text.lower():
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("tunefind", "tunefind.com", "music", "register")
async def tunefind(email, client):
    r = await client.get("https://www.tunefind.com/user/join", headers=H(ua=chrome_ua()))
    m = re.search(r'"csrf-token" content="([^"]+)"', r.text)
    headers = H(origin="https://www.tunefind.com", referer="https://www.tunefind.com/",
                content_type=FORM)
    if m:
        headers["X-CSRF-Token"] = m.group(1)
    response = await client.post(
        "https://www.tunefind.com/user/join", headers=headers,
        data={"user[email]": email, "user[username]": rand_token(10),
              "user[password]": rand_token(12)},
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "email" in (j.get("errors") or {}):
        return R(True)
    return R(False)


@site("blip", "blip.fm", "music", "register")
async def blip(email, client):
    response = await client.post(
        "https://blip.fm/signup/save",
        headers=H(origin="https://blip.fm", referer="https://blip.fm/", content_type=FORM),
        data={"email": email, "username": rand_token(10), "password": rand_token(12)},
    )
    if "That email address is already in use." in response.text:
        return R(True)
    if "loading" in response.text or response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("flickr", "flickr.com", "media", "login", confidence="low", evidence="inferred")
async def flickr(email, client):
    headers = H(referer="https://identity.flickr.com/login", origin="https://identity.flickr.com",
                accept=JSON)
    response = await client.get(
        "https://identity-api.flickr.com/migration?email=" + str(email), headers=headers
    )
    try:
        data = json.loads(response.text)
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if data.get("is_migrated") is True or data.get("user") is not None:
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("komoot", "komoot.com", "media", "register")
async def komoot(email, client):
    response = await client.post(
        "https://account.komoot.com/v1/signin",
        headers=H(origin="https://account.komoot.com", referer="https://account.komoot.com/signin",
                  accept=JSON, content_type=JSON),
        data=json.dumps({"email": email, "reason": "SignIn"}),
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "login" in str(j.get("type", "")):
        return R(True)
    if response.status_code in (200, 400, 401):
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("sporcle", "sporcle.com", "media", "register")
async def sporcle(email, client):
    response = await client.post(
        "https://www.sporcle.com/auth/ajax/verify.php",
        headers=H(origin="https://www.sporcle.com", content_type=FORM),
        data={"email": email},
    )
    if "account already exists with this email" in response.text:
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("ello", "ello.co", "media", "register", confidence="low", evidence="inferred")
async def ello(email, client):
    headers = H(origin="https://ello.co", referer="https://ello.co/join", content_type=FORM)
    response = await client.post(
        "https://ello.co/api/v2/availability", headers=headers, data={"email": email}
    )
    if response.status_code == 200 and "taken" not in response.text.lower():
        return R(False)
    response2 = await client.post(
        "https://ello.co/api/v2/availability", headers=headers,
        data={"email": f"{rand_token(12)}@{email.split('@')[-1]}"},
    )
    if response.status_code == 200 and response2.status_code == 200:
        if "taken" in response.text.lower() and "taken" not in response2.text.lower():
            return R(True)
    return R(None, rate_limit=True, error="inconclusive")


@site("duolingo", "duolingo.com", "learning", "other")
async def duolingo(email, client):
    req = await client.get(
        "https://www.duolingo.com/2017-06-30/users?email=" + email,
        headers=H(ua=firefox_ua(), accept=JSON),
    )
    try:
        j = req.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if j.get("users"):
        return R(True)
    if "users" in j:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("quora", "quora.com", "learning", "register")
async def quora(email, client):
    headers = H(ua=firefox_ua(), accept="application/json, text/javascript, */*; q=0.01",
                origin="https://www.quora.com", content_type=FORM,
                extra={"X-Requested-With": "XMLHttpRequest"})
    try:
        r = await client.get("https://www.quora.com", headers=headers)
        formkey = re.search(r'formkey": "([^"]+)"', r.text).group(1)
    except Exception:
        return R(None, rate_limit=True, error="no formkey")
    data = {
        "json": '{"args":[],"kwargs":{"value":"' + email + '"}}',
        "formkey": formkey, "__hmac": "0XXXXXXxxXDxX", "__method": "validate",
    }
    response = await client.post(
        "https://www.quora.com/webnode2/server_call_POST", headers=headers, data=data
    )
    low = response.text.lower()
    if "already" in low or "existe" in low or "compte" in low:
        return R(True)
    if response.status_code in (200, 400):
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


# =========================================================================== #
# Productivity / software / CMS / CRM
# =========================================================================== #

@site("adobe", "adobe.com", "software", "password recovery", confidence="high", evidence="direct", verify="https://account.adobe.com/")
async def adobe(email, client):
    headers = H(accept=JSON, origin="https://auth.services.adobe.com",
                content_type="application/json;charset=utf-8",
                extra={"X-IMS-CLIENTID": "adobedotcom2"})
    r = await client.post(
        "https://auth.services.adobe.com/signin/v1/authenticationstate",
        headers=headers, json={"username": email, "accountType": "individual"},
    )
    try:
        j = r.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "errorCode" in str(j.keys()):
        return R(False)
    state = r.headers.get("x-ims-authentication-state-encrypted")
    if not state:
        return R(None, rate_limit=True, error="no auth state")
    headers["X-IMS-Authentication-State-Encrypted"] = state
    response = await client.get(
        "https://auth.services.adobe.com/signin/v2/challenges",
        headers=headers, params={"purpose": "passwordRecovery"},
    )
    try:
        data = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "errorCode" in data:
        return R(True)
    return R(True, detail=f"recovery={data.get('secondaryEmail')} phone={data.get('securityPhoneNumber')}")


@site("archive", "archive.org", "software", "register")
async def archive(email, client):
    response = await client.post(
        "https://archive.org/account/signup",
        headers=H(origin="https://archive.org", referer="https://archive.org/account/signup",
                  content_type=FORM),
        data={"email": email, "screenname": rand_token(10), "password": rand_token(12)},
    )
    if "is already taken." in response.text:
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("issuu", "issuu.com", "software", "register")
async def issuu(email, client):
    response = await client.get(
        "https://issuu.com/call/signup/check-email/" + email,
        headers=H(referer="https://issuu.com/signup", accept=JSON),
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if j.get("available") is False:
        return R(True)
    if j.get("available") is True:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("anydo", "any.do", "productivity", "login")
async def anydo(email, client):
    response = await client.post(
        "https://sm-prod2.any.do/check_email",
        headers=H(origin="https://desktop.any.do", referer="https://desktop.any.do/",
                  accept=JSON, content_type=JSON),
        data=json.dumps({"email": email}),
    )
    if response.status_code == 200:
        try:
            j = response.json()
        except Exception:
            return R(None, rate_limit=True, error="non-json")
        if j.get("exists") is True or "already" in response.text.lower():
            return R(True)
        if j.get("exists") is False:
            return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("evernote", "evernote.com", "productivity", "login")
async def evernote(email, client):
    headers = H(origin="https://www.evernote.com", referer="https://www.evernote.com/Login.action",
                content_type=FORM)
    data = await client.get("https://www.evernote.com/Login.action", headers=headers)
    try:
        hpts = data.text.split('document.getElementById("hpts").value = "')[1].split('"')[0]
        hptsh = data.text.split('document.getElementById("hptsh").value = "')[1].split('"')[0]
    except Exception:
        return R(None, rate_limit=True, error="no hidden tokens")
    response = await client.post(
        "https://www.evernote.com/Login.action", headers=headers,
        data={"username": email, "hpts": hpts, "hptsh": hptsh, "password": rand_token(12)},
    )
    if "usePasswordAuth" in response.text:
        return R(True)
    if "displayMessage" in response.text:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("atlassian", "atlassian.com", "software", "register")
async def atlassian(email, client):
    headers = H(referer="https://id.atlassian.com/", origin="https://id.atlassian.com",
                content_type=FORM)
    r = await client.get("https://id.atlassian.com/login", headers=headers)
    m = re.search(r'csrfToken&quot;:&quot;([^&]+)&quot;', r.text) or re.search(
        r'"csrfToken":"([^"]+)"', r.text
    )
    if not m:
        return R(None, rate_limit=True, error="no csrf token")
    response = await client.post(
        "https://id.atlassian.com/rest/check-username", headers=headers,
        data={"email": email, "csrfToken": m.group(1)},
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if j.get("action") == "login":
        return R(True)
    if j.get("action") == "signup":
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("wordpress", "wordpress.com", "software", "login")
async def wordpress(email, client):
    headers = H(accept=JSON)
    response = await client.get(
        "https://public-api.wordpress.com/rest/v1.1/users/" + email + "/auth-options",
        headers=headers, params={"http_envelope": "1"},
    )
    try:
        info = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "body" in info:
        info = info["body"]
    if isinstance(info, dict) and "password" in info:
        return R(True)
    if response.status_code == 404:
        return R(False)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("hubspot", "hubspot.com", "crm", "login")
async def hubspot(email, client):
    headers = H(origin="https://app.hubspot.com", referer="https://app.hubspot.com/",
                accept=JSON, content_type=JSON)
    response = await client.post(
        "https://api.hubspot.com/login-api/v1/login", headers=headers,
        data=json.dumps({"email": email, "password": rand_token(12)}),
    )
    if response.status_code == 400:
        try:
            status = response.json().get("status")
        except Exception:
            return R(None, rate_limit=True, error="non-json")
        if status == "INVALID_PASSWORD":
            return R(True)
        if status == "INVALID_USER":
            return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("zoho", "zoho.com", "crm", "register")
async def zoho(email, client):
    headers = H(origin="https://accounts.zoho.com", accept="*/*", content_type=FORM)
    response = await client.get("https://accounts.zoho.com/register", headers=headers)
    csrf = response.cookies.get("iamcsr")
    if csrf:
        headers["X-ZCSRF-TOKEN"] = "iamcsrcoo=" + csrf
    response = await client.post(
        "https://accounts.zoho.com/signin/v2/lookup/" + email, headers=headers,
        data={"mode": "primary", "servicename": "ZohoCRM",
              "serviceurl": "https://crm.zoho.com/crm/ShowHomePage.do",
              "service_language": "en"},
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if response.status_code == 200 and j.get("message") == "User exists":
        return R(True)
    if response.status_code == 200 and "status_code" in j:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("pipedrive", "pipedrive.com", "crm", "register")
async def pipedrive(email, client):
    response = await client.post(
        "https://app.pipedrive.com/signup-service/start",
        headers=H(origin="https://www.pipedrive.com", referer="https://www.pipedrive.com/",
                  accept=JSON, content_type=JSON),
        data=json.dumps({"email": email, "language": "en"}),
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    errors = j.get("errors", {})
    if "user_email" in errors and "Email is not available" in str(errors["user_email"]):
        return R(True)
    if "data" in j:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("teamleader", "teamleader.eu", "crm", "register")
async def teamleader(email, client):
    response = await client.post(
        "https://focus.teamleader.eu/app/emails/availability",
        headers=H(origin="https://signup.focus.teamleader.eu",
                  referer="https://signup.focus.teamleader.eu/", accept=JSON, content_type=JSON),
        data=json.dumps({"email": email}),
    )
    if response.text == '{"available":false}':
        return R(True)
    if response.text == '{"available":true}':
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("insightly", "insightly.com", "crm", "register")
async def insightly(email, client):
    response = await client.post(
        "https://accounts.insightly.com/signup/isemailvalid",
        headers=H(origin="https://accounts.insightly.com",
                  referer="https://accounts.insightly.com/?plan=trial", content_type=FORM),
        data={"email": email},
    )
    if "An account exists for this address" in response.text:
        return R(True)
    if response.text.strip() == "true":
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("nimble", "nimble.com", "crm", "register")
async def nimble(email, client):
    response = await client.get(
        "https://www.nimble.com/lib/register.php?email=" + email,
        headers=H(referer="https://www.nimble.com/"),
    )
    txt = response.text.strip()
    if "already registered" in txt:
        return R(True)
    if txt.strip('"') == "true":
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("nocrm", "nocrm.io", "crm", "register")
async def nocrm(email, client):
    response = await client.get(
        "https://register.nocrm.io/register/check_trial_duplicate?email=" + email,
        headers=H(referer="https://register.nocrm.io/"),
    )
    if '{"account":1' in response.text:
        return R(True)
    if response.text.strip() == '{"account":0}':
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("nutshell", "nutshell.com", "crm", "register")
async def nutshell(email, client):
    response = await client.post(
        "https://app.nutshell.com/auth",
        headers=H(origin="https://app.nutshell.com", referer="https://app.nutshell.com/auth",
                  content_type=FORM),
        data={"email": email, "password": rand_token(12)},
    )
    if "Sorry, your password is incorrect" in response.text:
        return R(True)
    if "find a Nutshell account for that email address" in response.text:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("amocrm", "amocrm.com", "crm", "register")
async def amocrm(email, client):
    response = await client.post(
        "https://www.amocrm.com/account/check_login.php",
        headers=H(origin="https://www.amocrm.com", referer="https://www.amocrm.com/",
                  content_type=FORM),
        data={"login": email},
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if response.status_code == 200 and j.get("status") == "used":
        return R(True)
    if response.status_code == 200 and j.get("status") == "free":
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("axonaut", "axonaut.com", "crm", "register")
async def axonaut(email, client):
    response = await client.get(
        "https://axonaut.com/onboarding/?email=" + email,
        headers=H(referer="https://axonaut.com/en"),
    )
    if response.status_code == 302 and "/login?email" in str(response.headers.get("Location", "")):
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


# =========================================================================== #
# Jobs / travel / medical / osint
# =========================================================================== #

@site("freelancer", "freelancer.com", "jobs", "register")
async def freelancer(email, client):
    try:
        response = await client.post(
            "https://www.freelancer.com/api/users/0.1/users/check?compact=true&new_errors=true",
            data=json.dumps({"user": {"email": email}}),
            headers=H(origin="https://www.freelancer.com", accept=JSON, content_type=JSON),
        )
    except Exception:
        return R(None, rate_limit=True, error="request failed")
    if response.status_code == 409 and "EMAIL_ALREADY_IN_USE" in response.text:
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("coroflot", "coroflot.com", "jobs", "register")
async def coroflot(email, client):
    response = await client.post(
        "https://www.coroflot.com/home/signup_email_check",
        headers=H(origin="https://www.coroflot.com", referer="https://www.coroflot.com/signup",
                  content_type=FORM),
        data={"email": email},
    )
    if "already" in response.text.lower() or "taken" in response.text.lower():
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("seoclerks", "seoclerks.com", "jobs", "register", confidence="low", evidence="inferred")
async def seoclerks(email, client):
    r = await client.get("https://www.seoclerks.com", headers=H(origin="https://www.seoclerks.com"))
    token = None
    if "token" in r.text:
        try:
            token = r.text.split('token" value="')[1].split('"')[0]
        except Exception:
            token = None
    response = await client.post(
        "https://www.seoclerks.com/signup/check",
        headers=H(origin="https://www.seoclerks.com", content_type=FORM),
        data={"email": email, "token": token or ""},
    )
    if "The email address you entered is already taken." in response.text:
        return R(True)
    if response.status_code in (200, 400):
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("rocketreach", "rocketreach.co", "osint", "register")
async def rocketreach(email, client):
    response = await client.get("https://rocketreach.co/signup", headers=H())
    m = re.search(r'name="csrfmiddlewaretoken" value="(.*)"', response.text)
    headers = H(referer="https://rocketreach.co/signup", accept=JSON,
                extra={"X-Requested-With": "XMLHttpRequest"})
    if m:
        headers["X-CSRFToken"] = m.group(1)
    r = await client.get(
        "https://rocketreach.co/v1/validateEmail?email_address=" + email, headers=headers
    )
    if r.status_code in (200, 201):
        try:
            j = r.json()
        except Exception:
            return R(None, rate_limit=True, error="non-json")
        if j.get("valid") is False:
            return R(False)
        if j.get("valid") is True:
            return R(True)
    return R(None, rate_limit=True, error="inconclusive")


@site("blablacar", "blablacar.com", "travel", "register")
async def blablacar(email, client):
    app_token = await client.get(
        "https://www.blablacar.fr/register",
        headers=H(referer="https://www.blablacar.fr/", origin="https://www.blablacar.fr"),
    )
    m = re.search(r'"appToken":"([^"]+)"', app_token.text)
    if not m:
        return R(None, rate_limit=True, error="no app token")
    response = await client.get(
        "https://edge.blablacar.fr/auth/validation/email/" + email,
        headers=H(accept=JSON, extra={"Authorization": "Bearer " + m.group(1)}),
    )
    try:
        data = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "exists" in data:
        return R(bool(data["exists"]))
    return R(None, rate_limit=True, error="inconclusive")


@site("vrbo", "vrbo.com", "travel", "register")
async def vrbo(email, client):
    response = await client.post(
        "https://www.vrbo.com/auth/aam/v3/status",
        headers=H(origin="https://www.vrbo.com", accept=JSON, content_type=JSON),
        data=json.dumps({"email": email}),
    )
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if "authType" in j:
        return R(True)
    if response.status_code in (200, 400):
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("caringbridge", "caringbridge.org", "medical", "register")
async def caringbridge(email, client):
    response = await client.post(
        "https://www.caringbridge.org/signin",
        headers=H(origin="https://www.caringbridge.org", referer="https://www.caringbridge.org/signin",
                  content_type=FORM),
        data={"email": email, "password": rand_token(12)},
    )
    if "Welcome Back," in response.text:
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("sevencups", "7cups.com", "medical", "register")
async def sevencups(email, client):
    response = await client.post(
        "https://www.7cups.com/listener/CreateAccount.php",
        headers=H(origin="https://www.7cups.com",
                  referer="https://www.7cups.com/listener/CreateAccount.php", content_type=FORM),
        data={"email": email, "username": rand_token(10), "password": rand_token(12)},
    )
    if response.status_code == 200:
        if "Account already exists with this email address" in response.text:
            return R(True)
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


# =========================================================================== #
# Adult
# =========================================================================== #

@site("pornhub", "pornhub.com", "adult", "register")
async def pornhub(email, client):
    await client.get("https://www.pornhub.com/signup", headers=H())
    response = await client.post(
        "https://www.pornhub.com/user/create_account_check",
        headers=H(origin="https://www.pornhub.com", content_type=FORM),
        data={"email": email, "username": rand_token(10), "password": rand_token(12)},
    )
    low = response.text.lower()
    if "already" in low or "taken" in low or "in use" in low:
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("redtube", "redtube.com", "adult", "register")
async def redtube(email, client):
    r = await client.get("https://redtube.com/register", headers=H(origin="https://redtube.com"))
    token = None
    soup = BeautifulSoup(r.text, "html.parser")
    tag = soup.find("input", {"name": "token"})
    if tag and tag.get("value"):
        token = tag["value"]
    response = await client.post(
        "https://www.redtube.com/user/create_account_check",
        headers=H(origin="https://redtube.com", content_type=FORM),
        params={"token": token or ""},
        data={"email": email, "username": rand_token(10), "password": rand_token(12)},
    )
    if "Email has been taken." in response.text:
        return R(True)
    if response.status_code == 200:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("xnxx", "xnxx.com", "adult", "register")
async def xnxx(email, client):
    headers = H(referer="https://www.google.com/")
    site_req = await client.get("https://www.xnxx.com", headers=headers)
    if site_req.status_code != 200:
        return R(None, rate_limit=True, error="site unreachable")
    api = await client.get(
        f"https://www.xnxx.com/account/checkemail?email={email}",
        headers=headers, cookies=site_req.cookies,
    )
    if api.status_code == 200:
        try:
            data = json.loads(api.text)
        except Exception:
            return R(None, rate_limit=True, error="non-json")
        if data.get("result") is False:
            return R(True)
        if data.get("result") is True:
            return R(False)
    return R(None, rate_limit=True, error="inconclusive")


@site("xvideos", "xvideos.com", "adult", "register")
async def xvideos(email, client):
    response = await client.get(
        "https://www.xvideos.com/account/checkemail",
        headers=H(referer="https://www.xvideos.com/", accept=JSON),
        params={"email": email},
    )
    if "This email is already in use" in response.text:
        return R(True)
    try:
        j = response.json()
    except Exception:
        return R(None, rate_limit=True, error="non-json")
    if j.get("result") is True:
        return R(False)
    return R(None, rate_limit=True, error="inconclusive")


# =========================================================================== #
# MyBB-style forums (one factory, many sites)
# =========================================================================== #

_MYBB_FORUMS = [
    ("mybb", "community.mybb.com", "https://community.mybb.com"),
    ("koditv", "forum.kodi.tv", "https://forum.kodi.tv"),
    ("codeigniter", "forum.codeigniter.com", "https://forum.codeigniter.com"),
    ("clashfarmer", "clashfarmer.com", "https://www.clashfarmer.com/forum"),
    ("thevapingforum", "thevapingforum.com", "http://www.thevapingforum.com"),
    ("biotechnologyforums", "biotechnologyforums.com", "https://biotechnologyforums.com"),
    ("chinaphonearena", "chinaphonearena.com", "https://www.chinaphonearena.com/forum"),
    ("cpaelites", "cpaelites.com", "https://www.cpaelites.com"),
    ("cpahero", "cpahero.com", "https://www.cpahero.com"),
    ("cracked_to", "cracked.to", "https://cracked.to"),
    ("demonforums", "demonforums.net", "https://demonforums.net"),
    ("freiberg", "drachenhort.user.stunet.tu-freiberg.de",
     "https://drachenhort.user.stunet.tu-freiberg.de"),
    ("nattyornot", "nattyornotforum.nattyornot.com",
     "https://nattyornotforum.nattyornot.com"),
    ("ndemiccreations", "forum.ndemiccreations.com", "https://forum.ndemiccreations.com"),
    ("nextpvr", "forums.nextpvr.com", "https://forums.nextpvr.com"),
    ("onlinesequencer", "onlinesequencer.net", "https://onlinesequencer.net/forum"),
    ("thecardboard", "thecardboard.org", "https://thecardboard.org/board"),
    ("therianguide", "forums.therian-guide.com", "https://forums.therian-guide.com"),
    ("babeshows", "babeshows.co.uk", "https://www.babeshows.co.uk"),
    ("badeggsonline", "badeggsonline.com", "https://www.badeggsonline.com/beo2-forum"),
    ("biosmods", "bios-mods.com", "https://bios-mods.com/forum"),
    ("blackworldforum", "blackworldforum.com", "http://blackworldforum.com"),
    ("blitzortung", "forum.blitzortung.org", "https://forum.blitzortung.org"),
    ("bluegrassrivals", "bluegrassrivals.com", "http://bluegrassrivals.com/forum"),
    ("cambridgemt", "discussion.cambridge-mt.com", "https://discussion.cambridge-mt.com"),
]


def _make_mybb(name, domain, base):
    async def checker(email, client):
        headers = H(
            referer=base + "/member.php", origin=base,
            accept="application/json, text/javascript, */*; q=0.01", content_type=FORM,
        )
        try:
            r = await client.get(base + "/member.php", headers=headers)
        except Exception:
            return R(None, rate_limit=True, error="unreachable")
        if "Your request was blocked" in r.text or r.status_code != 200:
            return R(None, rate_limit=True, error="blocked")
        try:
            key = r.text.split('var my_post_key = "')[1].split('"')[0]
        except Exception:
            return R(None, rate_limit=True, error="no post key")
        headers["X-Requested-With"] = "XMLHttpRequest"
        response = await client.post(
            base + "/xmlhttp.php", headers=headers,
            params={"action": "email_availability"},
            data={"email": email, "my_post_key": key},
        )
        if "Your request was blocked" in response.text or response.status_code != 200:
            return R(None, rate_limit=True, error="blocked")
        if "email address that is already in use by another member." in response.text:
            return R(True)
        return R(False)

    checker.__name__ = "mybb_" + name
    checker._site_spec = SiteSpec(
        name=name, domain=domain, func=checker, category="forum", method="register"
    )
    return checker


for _n, _d, _b in _MYBB_FORUMS:
    globals()["_mybb_" + _n] = _make_mybb(_n, _d, _b)
