"""FastAPI exception handlers — surface SAYMON failures instead of bare 500."""

from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from alarm_manager_server.saymon.auth import SaymonAuthError
from alarm_manager_server.saymon.response import SaymonResponseError

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SaymonAuthError)
    async def saymon_auth_error(_request: Request, exc: SaymonAuthError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    @app.exception_handler(httpx.HTTPStatusError)
    async def saymon_http_error(_request: Request, exc: httpx.HTTPStatusError) -> JSONResponse:
        url = str(exc.request.url)
        body = exc.response.text[:500] if exc.response is not None else ""
        detail = f"SAYMON HTTP {exc.response.status_code} for {url}"
        if body:
            detail = f"{detail}: {body}"
        logger.error(detail)
        return JSONResponse(status_code=502, content={"detail": detail})

    @app.exception_handler(httpx.RequestError)
    async def saymon_request_error(_request: Request, exc: httpx.RequestError) -> JSONResponse:
        detail = f"SAYMON request failed: {exc}"
        logger.error(detail)
        return JSONResponse(status_code=502, content={"detail": detail})

    @app.exception_handler(SaymonResponseError)
    async def saymon_response_error(_request: Request, exc: SaymonResponseError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def validation_error(_request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    @app.exception_handler(Exception)
    async def unhandled_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error in request")
        return JSONResponse(status_code=500, content={"detail": str(exc)})
