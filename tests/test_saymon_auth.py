import httpx
import pytest

from alarm_manager_server.saymon.auth import SaymonAuthError, csrf_headers, require_session_cookies
from alarm_manager_server.saymon.client import SaymonClient


def test_require_session_cookies_ok():
    jar = httpx.Cookies()
    jar.set("sid", "sid-val")
    jar.set("csrf", "csrf-val")
    assert require_session_cookies(jar) == ("sid-val", "csrf-val")


def test_require_session_cookies_missing():
    with pytest.raises(SaymonAuthError):
        require_session_cookies(httpx.Cookies())


def test_csrf_headers():
    jar = httpx.Cookies()
    jar.set("csrf", "abc")
    assert csrf_headers(jar) == {"Accept": "application/json", "x-csrf-token": "abc"}


@pytest.mark.asyncio
async def test_client_authenticates_before_api_call():
    session_posted = False
    incidents_requested = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal session_posted, incidents_requested
        if request.url.path == "/users/session" and request.method == "POST":
            import json

            session_posted = True
            assert request.headers.get("content-type") == "application/json"
            assert json.loads(request.content) == {"login": "user", "password": "pass"}
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "sid=test-sid; Path=/"),
                    ("set-cookie", "csrf=test-csrf; Path=/"),
                ],
                json={"ok": True},
            )
        if request.url.path.startswith("/incidents") and request.method == "GET":
            incidents_requested = True
            assert request.headers.get("x-csrf-token") == "test-csrf"
            cookie = request.headers.get("cookie", "")
            assert "sid=test-sid" in cookie
            assert "csrf=test-csrf" in cookie
            return httpx.Response(200, json=[{"id": "1"}])
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = SaymonClient(
        "http://saymon.test/node/api",
        login="user",
        password="pass",
        saymon_base_url="http://saymon.test",
    )
    client._client = httpx.AsyncClient(
        base_url=client._base,
        transport=transport,
        follow_redirects=True,
    )
    client._authenticated = False

    data = await client.get_incidents(limit=1)
    assert data == [{"id": "1"}]
    assert session_posted
    assert incidents_requested

    await client.aclose()


@pytest.mark.asyncio
async def test_client_follows_redirect_url_after_login():
    redirect_hit = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal redirect_hit
        if request.url.path == "/users/session":
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "sid=s; Path=/"),
                    ("set-cookie", "csrf=c; Path=/"),
                ],
                json={},
            )
        if request.url.path == "/saymon.local/apps/alarm-manager":
            redirect_hit = True
            assert request.headers.get("x-csrf-token") == "c"
            return httpx.Response(200)
        if request.url.path == "/classes":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = SaymonClient(
        "http://saymon.test/node/api",
        login="u",
        password="p",
        saymon_base_url="http://saymon.test",
        auth_redirect_url="/saymon.local/apps/alarm-manager",
    )
    client._client = httpx.AsyncClient(
        base_url=client._base,
        transport=transport,
        follow_redirects=True,
    )

    await client.get_classes()
    assert redirect_hit
    await client.aclose()
