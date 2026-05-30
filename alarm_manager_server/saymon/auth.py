"""SAYMON session authentication helpers."""

from __future__ import annotations

import httpx


class SaymonAuthError(Exception):
    """Login or session setup failed."""


def require_session_cookies(cookies: httpx.Cookies) -> tuple[str, str]:
    """Return (sid, csrf) or raise if either cookie is missing."""
    sid = cookies.get("sid")
    csrf = cookies.get("csrf")
    if not sid or not csrf:
        raise SaymonAuthError(
            "POST /users/session returned 200 but sid/csrf cookies are missing in Set-Cookie"
        )
    return sid, csrf


def csrf_headers(cookies: httpx.Cookies) -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    csrf = cookies.get("csrf")
    if csrf:
        headers["x-csrf-token"] = csrf
    return headers
