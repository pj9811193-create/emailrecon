"""Offline correctness checks: feed each checker canned responses and assert
it maps them to the right status. No network involved."""

import asyncio
import httpx

from emailrecon import sites as S


def client_for(routes):
    """routes: dict path-substring -> httpx.Response (or callable)."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for key, resp in routes.items():
            if key in url:
                return resp if not callable(resp) else resp(request)
        return httpx.Response(404, text="")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


async def main():
    checks = []

    # twitter
    c = client_for({"email_available": httpx.Response(200, json={"taken": True})})
    checks.append(("twitter/True", (await S.twitter("a@b.com", c))["exists"] is True))
    c = client_for({"email_available": httpx.Response(200, json={"taken": False})})
    checks.append(("twitter/False", (await S.twitter("a@b.com", c))["exists"] is False))

    # gravatar
    c = client_for({"gravatar.com": httpx.Response(404)})
    checks.append(("gravatar/False", (await S.gravatar("a@b.com", c))["exists"] is False))
    c = client_for({"gravatar.com": httpx.Response(200, json={"entry": [{"displayName": "Jo", "profileUrl": "u"}]})})
    r = await S.gravatar("a@b.com", c)
    checks.append(("gravatar/True", r["exists"] is True and "Jo" in (r["detail"] or "")))

    # spotify
    c = client_for({"signup/public/v1/account": httpx.Response(200, json={"status": 20})})
    checks.append(("spotify/True", (await S.spotify("a@b.com", c))["exists"] is True))
    c = client_for({"signup/public/v1/account": httpx.Response(200, json={"status": 1})})
    checks.append(("spotify/False", (await S.spotify("a@b.com", c))["exists"] is False))

    # github
    join_html = '<auto-check src="/signup_check/username" value="tok1"><auto-check src="/signup_check/email" value="tok2">'
    c = client_for({"github.com/join": httpx.Response(200, text=join_html),
                    "signup_check/email": httpx.Response(422, text="taken")})
    checks.append(("github/True", (await S.github("a@b.com", c))["exists"] is True))
    c = client_for({"github.com/join": httpx.Response(200, text=join_html),
                    "signup_check/email": httpx.Response(200, text="ok")})
    checks.append(("github/False", (await S.github("a@b.com", c))["exists"] is False))

    # lastfm
    body = {"email": {"error_messages": ["Sorry, that email address is already registered to another account."]}}
    c = client_for({"partial/validate": httpx.Response(200, json=body),
                    "last.fm/join": httpx.Response(200, text="x", headers={"set-cookie": "csrftoken=t"})})
    checks.append(("lastfm/True", (await S.lastfm("a@b.com", c))["exists"] is True))

    # ebay
    c = client_for({"srv/identifer": httpx.Response(200, text='{"err": []}'),
                    "www.ebay.com/signin/": httpx.Response(200, text='"csrfAjaxToken":"abc"')})
    checks.append(("ebay/False", (await S.ebay("a@b.com", c))["exists"] is False))

    # pinterest
    c = client_for({"EmailExistsResource": httpx.Response(200, json={"resource_response": {"data": {"x": 1}}})})
    checks.append(("pinterest/True", (await S.pinterest("a@b.com", c))["exists"] is True))

    # google
    html = 'quot;,null,null,null,&quot;FLOWTOKEN&quot;'
    c = client_for({"webcreateaccount": httpx.Response(200, text=html),
                    "webusernameavailability": httpx.Response(200, text='"gf.wuar",2')})
    checks.append(("google/True", (await S.google("a@b.com", c))["exists"] is True))

    # mybb factory
    c = client_for({"member.php": httpx.Response(200, text='var my_post_key = "KEY"'),
                    "xmlhttp.php": httpx.Response(200, text="email address that is already in use by another member.")})
    checks.append(("mybb/True", (await S._mybb_mybb("a@b.com", c))["exists"] is True))

    ok = 0
    for name, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
        ok += passed
    print(f"\n{ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


raise SystemExit(asyncio.run(main()))
