"""Record a ticket through REPAIR.REP_MONIT_SYSTEM_CURS using bound values."""

from __future__ import annotations

import logging
from time import monotonic
from datetime import datetime
from importlib import import_module
from zoneinfo import ZoneInfo
from uuid import uuid4

from alarm_manager_server.config import Settings
from alarm_manager_server.worker.ticket_handlers import (
    BaseTicketHandler, HandlerResult, TicketHandlerContext,
)

logger = logging.getLogger(__name__)

SQL = """SELECT REPAIR.REP_MONIT_SYSTEM_CURS(
    P_B_DATE => :p_b_date,
    P_E_DATE => :p_e_date,
    P_ID_DEPT => :p_id_dept,
    P_NAME_EQUIP => :p_name_equip,
    P_NAME_DEFECT => :p_name_defect,
    P_EXECUTED_WORK => :p_executed_work,
    P_ID_BUILD => :p_id_build,
    P_LOCATION => :p_location,
    P_ID_DEF => :p_id_def,
    P_ID_MONIT => :p_id_monit
) FROM dual"""


def oracle_driver(cfg: Settings):
    """Initialize OCI before connecting; mode remains fixed for this process."""
    driver = import_module("oracledb")
    if cfg.oracle_mode == "thick":
        driver.init_oracle_client()
        logger.info("Oracle driver mode=thick client_version=%s", driver.clientversion())
    return driver


def oracle_connect(driver, cfg: Settings):
    dsn = cfg.oracle_dsn.strip().removeprefix("jdbc:oracle:thin:@")
    if cfg.oracle_mode == "thick":
        # OCI 19 needs timeout in the descriptor, not only a Python keyword.
        params = driver.ConnectParams()
        params.parse_connect_string(dsn)
        params.set(tcp_connect_timeout=cfg.oracle_connect_timeout_sec,
                   retry_count=0)
        dsn = params.get_connect_string()
    return driver.connect(
        user=cfg.oracle_user, password=cfg.oracle_password.get_secret_value(),
        dsn=dsn, tcp_connect_timeout=cfg.oracle_connect_timeout_sec,
    )


class OracleTicketHandler(BaseTicketHandler):
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.timezone = ZoneInfo(cfg.oracle_timezone)

    @classmethod
    def from_settings(cls, cfg: Settings) -> OracleTicketHandler | None:
        return cls(cfg) if cfg.oracle_enabled else None

    def _date(self, override: str, value: str) -> str:
        if override:
            datetime.strptime(override, "%d.%m.%Y %H:%M")
            return override
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            raise ValueError("Oracle ticket timestamps must include a timezone")
        return dt.astimezone(self.timezone).strftime("%d.%m.%Y %H:%M")

    def on_ticket_event(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        if ctx.event.action != self.cfg.oracle_event:
            return None
        if (ctx.ticket.get("external_meta") or {}).get("oracle_recorded"):
            return None
        try:
            result = self._send(ctx)
        except Exception as exc:
            error = exc.args[0] if exc.args else None
            timeout = isinstance(exc, TimeoutError) or getattr(error, "full_code", None) in {
                "DPY-4024", "DPI-1067", "ORA-12170",
            }
            reason = "истекло время ожидания" if timeout else "ошибка отправки или некорректный ответ"
            if getattr(error, "full_code", None) == "DPY-3015":
                reason = ("подключение отклонено (DPY-3015): формат пароля учётной записи "
                          "несовместим с Oracle Thin; требуется обращение к DBA")
                logger.error("Oracle DPY-3015 ticket=%s: DBA must regenerate an 11G/12C password "
                             "verifier or deploy Oracle Client with Thick mode; function was not called",
                             ctx.event.ticket_id)
            self._queue_comment(ctx, (
                f"Отправка информации в Oracle ServiceDesk не подтверждена: {reason}. "
                "Перед повторной отправкой требуется сверка с Oracle."
            ))
            raise
        self._queue_comment(ctx, (
            "Информация успешно отправлена в Oracle ServiceDesk; получено подтверждение commit."
            + (f" Номер заявки: {result.external_ref}." if result and result.external_ref else "")
        ))
        return result

    def _queue_comment(self, ctx: TicketHandlerContext, message: str) -> None:
        if not self.cfg.oracle_saymon_comment_enabled:
            return
        meta = ctx.ticket.setdefault("external_meta", {})
        meta.setdefault("oracle_comments", []).append({
            "id": uuid4().hex,
            "text": f"[{self.cfg.oracle_comment_module_name.strip() or 'Alarm Manager'}] "
                    f"{message} Локальный тикет: {ctx.event.ticket_id}.",
            "pending_incident_ids": list(dict.fromkeys(
                str(value) for value in (ctx.ticket.get("snapshot") or {}).get("member_ids", []) if value
            )),
        })

    def _send(self, ctx: TicketHandlerContext) -> HandlerResult | None:
        cfg = self.cfg
        if ctx.event.action != cfg.oracle_event:
            return None
        if (ctx.ticket.get("external_meta") or {}).get("oracle_recorded"):
            return None
        snapshot = ctx.ticket.get("snapshot") or {}
        params = {
            "p_b_date": self._date(cfg.oracle_b_date, ctx.ticket.get("created_at", "")),
            "p_e_date": self._date(cfg.oracle_e_date, (
                ctx.ticket.get("closed_at") if cfg.oracle_event == "closed"
                else ctx.ticket.get("updated_at")
            ) or ctx.ticket.get("created_at", "")),
            "p_id_dept": cfg.oracle_id_dept,
            "p_name_equip": cfg.oracle_name_equip or ctx.event.title or snapshot.get("title", ""),
            "p_name_defect": cfg.oracle_name_defect or ctx.body_text or "\n".join(
                m.get("text", "") for m in (snapshot.get("members") or {}).values()
            ),
            "p_executed_work": cfg.oracle_executed_work,
            "p_id_build": cfg.oracle_id_build,
            "p_location": cfg.oracle_location,
            "p_id_def": cfg.oracle_id_def,
            "p_id_monit": cfg.oracle_id_monit,
        }
        driver = oracle_driver(cfg)
        started = monotonic()
        stage = "connect"
        logger.info("Oracle connecting ticket=%s event=%s timeout_sec=%s",
                    ctx.event.ticket_id, ctx.event.action, cfg.oracle_connect_timeout_sec)
        try:
            with oracle_connect(driver, cfg) as connection:
                deadline = monotonic() + cfg.oracle_call_timeout_ms / 1000

                def remaining_timeout():
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Oracle confirmation deadline exceeded")
                    connection.call_timeout = max(1, int(remaining * 1000))

                try:
                    stage = "execute"
                    logger.info("Oracle sending ticket=%s event=%s confirmation_timeout_ms=%s",
                                ctx.event.ticket_id, ctx.event.action, cfg.oracle_call_timeout_ms)
                    with connection.cursor() as cursor:
                        remaining_timeout()
                        cursor.execute(SQL, params)
                        stage = "fetch"
                        remaining_timeout()
                        row = cursor.fetchone()
                        if row is None or row[0] is None:
                            raise RuntimeError("Oracle ticket function returned no result")
                        result = row[0]
                        stage = "validate_response"
                        if isinstance(result, driver.Cursor):
                            with result:
                                remaining_timeout()
                                ref = self._cursor_ref(result)
                        elif isinstance(result, (str, int)):
                            ref = str(result).strip()
                            if not ref:
                                raise RuntimeError("Oracle ticket function returned an empty result")
                        else:
                            raise RuntimeError("Unsupported Oracle ticket function result type")
                    logger.info("Oracle response received ticket=%s external_ref=%s; awaiting commit",
                                ctx.event.ticket_id, ref or "unavailable")
                    stage = "commit"
                    remaining_timeout()
                    connection.commit()
                    logger.info("Oracle confirmed ticket=%s external_ref=%s elapsed_sec=%.3f",
                                ctx.event.ticket_id, ref or "unavailable", monotonic() - started)
                except Exception:
                    # Cleanup has its own timeout; preserve the original failure.
                    try:
                        connection.call_timeout = cfg.oracle_call_timeout_ms
                        connection.rollback()
                    except Exception:
                        logger.warning("Oracle rollback failed ticket=%s; reconcile with DBA",
                                       ctx.event.ticket_id)
                    raise
        except Exception as exc:
            error = exc.args[0] if exc.args else None
            code = getattr(error, "full_code", None)
            timed_out = isinstance(exc, TimeoutError) or code in {
                "DPY-4024", "DPI-1067", "DPY-4005", "ORA-12170",
            }
            logger.error(
                "Oracle confirmation missing ticket=%s stage=%s reason=%s "
                "error_type=%s error_code=%s elapsed_sec=%.3f; "
                "no automatic retry; reconcile database before retry",
                ctx.event.ticket_id, stage, "timeout" if timed_out else "error_or_invalid_response",
                type(exc).__name__, code or "unavailable", monotonic() - started,
            )
            raise
        return HandlerResult(
            external_ref=ref,
            external_meta={"system": "oracle", "oracle_recorded": True},
        )

    def _cursor_ref(self, cursor) -> str | None:
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Oracle ticket function returned an empty cursor")
        column = self.cfg.oracle_result_id_column.strip().upper()
        if not column:
            # Do not invent a ticket number from an unknown result schema.
            return None
        columns = [entry[0].upper() for entry in cursor.description]
        if column not in columns:
            raise RuntimeError("Configured Oracle ticket ID column is missing")
        value = row[columns.index(column)]
        if value is None or not str(value).strip():
            raise RuntimeError("Oracle ticket ID is empty")
        return str(value).strip()
