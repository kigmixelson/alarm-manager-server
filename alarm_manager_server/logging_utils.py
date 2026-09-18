"""Compact operational errors; tracebacks are available at DEBUG only."""
from __future__ import annotations

import logging
import re
import sys


def error_summary(exc: BaseException | None) -> str:
    if exc is None:
        return "неизвестная ошибка"
    text = str(exc)
    detail = exc.args[0] if exc.args else None
    code = getattr(detail, "full_code", None)
    if not isinstance(code, str):
        match = re.search(r"\b(?:ORA|DPY|DPI)-\d{4,5}\b", text)
        code = match.group() if match else None
    if code:
        reasons = {
            "ORA-01017": "Oracle: авторизация отклонена",
            "ORA-28000": "Oracle: учётная запись заблокирована",
            "ORA-28001": "Oracle: срок действия пароля истёк",
            "ORA-12545": "Oracle: целевой хост или адрес перенаправления недоступен",
            "ORA-12154": "Oracle: не удалось разрешить строку подключения",
            "ORA-12541": "Oracle: listener недоступен",
            "ORA-12514": "Oracle: service name неизвестен listener",
            "ORA-12170": "Oracle: таймаут подключения",
            "DPY-4024": "Oracle: таймаут ожидания ответа",
            "DPY-3015": "Oracle: формат пароля несовместим с Thin",
            "DPI-1047": "Oracle: не удалось загрузить клиентскую библиотеку",
        }
        return f"{reasons.get(code, 'ошибка Oracle')} ({code})"
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        match = re.search(r"\bstatus(?:_code)?[=: ]+(\d{3})\b", text)
        status = int(match.group(1)) if match else None
    if status is not None:
        if status in (401, 403):
            reason = "авторизация или доступ отклонены"
        elif status >= 500:
            reason = "удалённый сервис недоступен или вернул внутреннюю ошибку"
        else:
            reason = "удалённый сервис отклонил запрос"
        return f"HTTP {status}: {reason}"
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return "TLS: сертификат удалённого сервиса не прошёл проверку"
    name = type(exc).__name__
    if isinstance(exc, TimeoutError) or "Timeout" in name:
        return "истекло время ожидания ответа"
    if isinstance(exc, ConnectionError) or name in {"ConnectError", "NetworkError", "gaierror"}:
        return "не удалось установить соединение с удалённым сервисом"
    if name == "SaymonAuthError":
        return "SAYMON: не удалось установить авторизованную сессию"
    # Never dump response bodies, URLs with credentials, SQL or arbitrary exception text.
    return f"ошибка {name}; подробности доступны с --verbose"


def log_error(logger: logging.Logger, message: str, *args) -> None:
    info = sys.exc_info()
    logger.error(message + "; %s", *args, error_summary(info[1]))
    logger.debug("Подробности: " + message, *args, exc_info=info if info[1] else None)
