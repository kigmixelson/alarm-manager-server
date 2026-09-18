"""Bounded, read-only Oracle connectivity diagnostics. Never authenticates or runs SQL."""
from __future__ import annotations

import json
import logging
import socket
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

REASONS = {
    "ORA-01017": "Oracle ответил: авторизация отклонена; проверить логин, пароль и целевой service",
    "ORA-28000": "Oracle ответил: учётная запись заблокирована",
    "ORA-28001": "Oracle ответил: срок действия пароля истёк",
    "ORA-01045": "Oracle ответил: нет права CREATE SESSION",
    "ORA-12154": "Oracle Client не разрешил идентификатор подключения; проверить DSN и naming configuration",
    "ORA-12545": "Неверный или недоступный адрес; проверить DNS, адрес listener и возможный redirect",
    "ORA-12541": "Нет доступного Oracle listener по указанному адресу",
    "ORA-12514": "Listener ответил, но запрошенный SERVICE_NAME ему неизвестен",
    "ORA-12505": "Listener ответил, но запрошенный SID ему неизвестен",
    "ORA-12170": "Истекло время установления Oracle-соединения",
    "DPY-3015": "Thin несовместим с password verifier; требуется Thick и Oracle Instant Client",
    "DPY-3010": "Версия сервера несовместима с Thin; проверить совместимость Thick/Oracle Client",
    "DPI-1047": "Oracle Client не загружен: отсутствует библиотека или её системная зависимость",
    "DPI-1072": "Версия Oracle Client не поддерживается драйвером",
}


def diagnose_connection(driver, cfg, exc: Exception, ticket_id: str) -> None:
    """Never replace the original exception; impose a hard timeout even on DNS."""
    try:
        error = exc.args[0] if exc.args else None
        code = getattr(error, "full_code", None)
        logger.error("Oracle diagnostic ticket=%s step=1 code=%s: %s", ticket_id, code or "unknown",
                     REASONS.get(code, "Ошибка подключения; уточнение по DNS/TCP ниже"))
        logger.info("Oracle diagnostic ticket=%s step=2 requested_mode=%s python_driver=%s",
                    ticket_id, cfg.oracle_mode, getattr(driver, "__version__", "unavailable"))
        if driver is None:
            logger.error("Oracle diagnostic ticket=%s: Python-драйвер не загружен", ticket_id)
            return
        thin = driver.is_thin_mode()
        logger.info("Oracle diagnostic ticket=%s actual_mode=%s client_version=%s",
                    ticket_id, "thin" if thin else "thick",
                    "not used" if thin else driver.clientversion())
        params = driver.ConnectParams()
        params.parse_connect_string(cfg.oracle_dsn.strip().removeprefix("jdbc:oracle:thin:@"))
        hosts = params.host if isinstance(params.host, list) else [params.host]
        ports = params.port if isinstance(params.port, list) else [params.port] * len(hosts)
        endpoints = [(h, p) for h, p in zip(hosts, ports) if isinstance(h, str) and h]
        if not endpoints:
            logger.error("Oracle diagnostic ticket=%s step=3: DSN не содержит разрешённого TCP-адреса", ticket_id)
            return
        # Child process allows terminating a stuck OS DNS resolver, unlike a thread.
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve())],
            input=json.dumps({"ticket": ticket_id, "endpoints": endpoints[:4]}),
            text=True, check=True, timeout=15,
        )
        logger.info("Oracle diagnostic ticket=%s finished; SQL и повторная авторизация не выполнялись; "
                    "версии клиента не переключались (режим фиксирован на процесс)", ticket_id)
    except subprocess.TimeoutExpired:
        logger.error("Oracle diagnostic ticket=%s: диагностика остановлена через 15 секунд (DNS/TCP)", ticket_id)
    except Exception as diagnostic_error:
        logger.error("Oracle diagnostic ticket=%s incomplete error_type=%s; исходная ошибка сохранена",
                     ticket_id, type(diagnostic_error).__name__)


def probe_endpoints(ticket: str, endpoints: list) -> None:
    for host, port in endpoints:
        logger.info("Oracle diagnostic ticket=%s step=3 DNS host=%s", ticket, host)
        try:
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            logger.error("Oracle diagnostic ticket=%s DNS FAILED host=%s error=%s: имя хоста не разрешено",
                         ticket, host, type(exc).__name__)
            continue
        seen = set()
        reachable = False
        for family, kind, proto, _, address in addresses:
            if address in seen:
                continue
            seen.add(address)
            if len(seen) > 4:
                break
            logger.info("Oracle diagnostic ticket=%s step=4 TCP address=%s timeout_sec=2", ticket, address)
            try:
                with socket.socket(family, kind, proto) as sock:
                    sock.settimeout(2)
                    sock.connect(address)
                reachable = True
                logger.info("Oracle diagnostic ticket=%s TCP OK address=%s; доступен TCP-порт, "
                            "авторизация Oracle этим не проверяется", ticket, address)
            except OSError as exc:
                logger.error("Oracle diagnostic ticket=%s TCP FAILED address=%s error=%s errno=%s",
                             ticket, address, type(exc).__name__, exc.errno)
        if not reachable:
            logger.error("Oracle diagnostic ticket=%s: TCP-связь с host=%s port=%s не установлена; "
                         "проверить маршрут, firewall и listener", ticket, host, port)
        else:
            logger.info("Oracle diagnostic ticket=%s: исходный адрес доступен; при ORA-12545 "
                        "возможен недоступный redirect Oracle, его адрес этой проверкой не определяется", ticket)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    payload = json.load(sys.stdin)
    probe_endpoints(payload["ticket"], payload["endpoints"])
