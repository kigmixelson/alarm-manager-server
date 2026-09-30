"""Record a ticket through REPAIR.REP_MONIT_SYSTEM_CURS using bound values."""

from __future__ import annotations

import logging
from time import monotonic
from datetime import datetime
from importlib import import_module
from zoneinfo import ZoneInfo
from uuid import uuid4

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins.oracle_diagnostics import diagnose_connection
from alarm_manager_server.worker.ticket_handlers import (
    BaseTicketHandler, HandlerResult, TicketHandlerContext,
)
from alarm_manager_server.worker.tickets import close_reason_label

logger = logging.getLogger(__name__)

# PL/SQL function that returns SYS_REFCURSOR. SELECT ... FROM dual / executeQuery
# only yields the cursor handle, not its rows — callfunc opens the cursor.
FUNCTION_NAME = "REPAIR.REP_MONIT_SYSTEM_CURS"
KEYWORD_PARAM_NAMES = (
    ("P_B_DATE", "p_b_date"),
    ("P_E_DATE", "p_e_date"),
    ("P_ID_DEPT", "p_id_dept"),
    ("P_NAME_EQUIP", "p_name_equip"),
    ("P_NAME_DEFECT", "p_name_defect"),
    ("P_EXECUTED_WORK", "p_executed_work"),
    ("P_ID_BUILD", "p_id_build"),
    ("P_LOCATION", "p_location"),
    ("P_ID_DEF", "p_id_def"),
    ("P_ID_MONIT", "p_id_monit"),
)


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
        recorded = bool((ctx.ticket.get("external_meta") or {}).get("oracle_recorded"))
        if ctx.event.action == self.cfg.oracle_event and not recorded:
            return self._send_and_comment(ctx)
        if ctx.event.action in {"updated", "closed"}:
            self._queue_lifecycle_comment(ctx)
        elif ctx.event.action == "created" and self.cfg.oracle_event == "closed":
            self._queue_lifecycle_comment(ctx)
        return None

    def _send_and_comment(self, ctx: TicketHandlerContext) -> HandlerResult | None:
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
        self._announce_success(ctx, result.external_ref if result else None)
        return result

    def _oracle_ref(self, ticket: dict) -> str:
        meta = ticket.get("external_meta") or {}
        refs = meta.get("external_refs") if isinstance(meta, dict) else None
        if isinstance(refs, dict) and refs.get("oracle"):
            return str(refs["oracle"]).strip()
        ref = ticket.get("external_ref")
        return str(ref).strip() if ref else ""

    def _lifecycle_message(self, ctx: TicketHandlerContext) -> str:
        recorded = bool((ctx.ticket.get("external_meta") or {}).get("oracle_recorded"))
        ref = self._oracle_ref(ctx.ticket)
        ref_text = f" Номер заявки: {ref}." if ref else ""
        if ctx.event.action == "created":
            return "Локальный тикет создан; отправка в Oracle ServiceDesk будет выполнена при закрытии."
        if ctx.event.action == "updated":
            changes = "; ".join(ctx.event.changes) if ctx.event.changes else (
                "состав или состояние группы изменились"
            )
            if recorded:
                return (
                    "Локальный тикет обновлён; заявка в Oracle ServiceDesk уже зарегистрирована."
                    f"{ref_text} Изменения: {changes}."
                )
            return (
                "Локальный тикет обновлён; в Oracle ServiceDesk заявка ещё не зарегистрирована."
                f" Изменения: {changes}."
            )
        reason = close_reason_label(ctx.event.close_reason) or ctx.event.close_reason or "не указана"
        if recorded:
            return (
                "Локальный тикет закрыт; заявка в Oracle ServiceDesk уже зарегистрирована."
                f"{ref_text} Причина закрытия: {reason}."
            )
        return (
            "Локальный тикет закрыт; в Oracle ServiceDesk заявка не регистрировалась."
            f" Причина закрытия: {reason}."
        )

    def _queue_lifecycle_comment(self, ctx: TicketHandlerContext) -> None:
        message = self._lifecycle_message(ctx)
        self._queue_comment(ctx, message)
        incident_ids = self._incident_ids(ctx)
        line = f"Oracle ServiceDesk: {ctx.event.action} ticket={ctx.event.ticket_id}"
        if incident_ids:
            line += f"; аварии {','.join(incident_ids)}"
        logger.info("%s", line)
        print(line, flush=True)

    def _incident_ids(self, ctx: TicketHandlerContext) -> list[str]:
        ids: list[str] = []
        snapshot = ctx.ticket.get("snapshot") or {}
        ids.extend(str(value) for value in (snapshot.get("member_ids") or []) if value)
        members = snapshot.get("members")
        if isinstance(members, dict):
            ids.extend(str(value) for value in members if value)
        group = ctx.event.group
        if group is not None:
            ids.extend(str(value) for value in (group.member_ids or ()) if value)
        return list(dict.fromkeys(ids))

    def _announce_success(self, ctx: TicketHandlerContext, external_ref: str | None) -> None:
        parts = [f"Oracle ServiceDesk: отправка подтверждена ticket={ctx.event.ticket_id}"]
        if external_ref:
            parts.append(f"номер заявки {external_ref}")
        incident_ids = self._incident_ids(ctx)
        if incident_ids:
            parts.append(f"аварии {','.join(incident_ids)}")
        else:
            parts.append("аварии группы не найдены — комментарий в SAYMON не поставлен в очередь")
        message = "; ".join(parts)
        logger.info("%s", message)
        print(message, flush=True)

    def _queue_comment(self, ctx: TicketHandlerContext, message: str) -> None:
        if not self.cfg.oracle_saymon_comment_enabled:
            return
        incident_ids = self._incident_ids(ctx)
        if not incident_ids:
            logger.warning(
                "Oracle SAYMON comment queued without incidents ticket=%s",
                ctx.event.ticket_id,
            )
        meta = ctx.ticket.setdefault("external_meta", {})
        meta.setdefault("oracle_comments", []).append({
            "id": uuid4().hex,
            "text": f"[{self.cfg.oracle_comment_module_name.strip() or 'Alarm Manager'}] "
                    f"{message} Локальный тикет: {ctx.event.ticket_id}.",
            "pending_incident_ids": incident_ids,
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
        driver = None
        started = monotonic()
        stage = "initialize"
        logger.info("Oracle connecting ticket=%s event=%s timeout_sec=%s",
                    ctx.event.ticket_id, ctx.event.action, cfg.oracle_connect_timeout_sec)
        try:
            driver = oracle_driver(cfg)
            stage = "connect"
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
                        result = cursor.callfunc(
                            FUNCTION_NAME,
                            driver.DB_TYPE_CURSOR,
                            [],
                            {plsql: params[key] for plsql, key in KEYWORD_PARAM_NAMES},
                        )
                        if result is None:
                            raise RuntimeError("Oracle ticket function returned no result")
                        stage = "fetch"
                        try:
                            remaining_timeout()
                            columns = [
                                str(entry[0])
                                for entry in (getattr(result, "description", None) or [])
                            ]
                            logger.info("Oracle ref cursor opened ticket=%s columns=%s",
                                        ctx.event.ticket_id, ",".join(columns) or "unavailable")
                            stage = "validate_response"
                            ref = self._cursor_ref(result)
                        finally:
                            closer = getattr(result, "close", None)
                            if callable(closer):
                                try:
                                    closer()
                                except Exception:
                                    logger.warning(
                                        "Oracle ref cursor close failed ticket=%s",
                                        ctx.event.ticket_id,
                                    )
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
            if stage in {"initialize", "connect"}:
                diagnose_connection(driver, cfg, exc, ctx.event.ticket_id)
            raise
        return HandlerResult(
            external_ref=ref,
            external_meta={"system": "oracle", "oracle_recorded": True},
        )

    def _cursor_ref(self, cursor) -> str | None:
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Oracle ticket function returned an empty cursor")
        description = getattr(cursor, "description", None)
        if not description:
            raise RuntimeError("Oracle ticket function cursor has no description")
        columns = [entry[0].upper() for entry in description]
        column = self.cfg.oracle_result_id_column.strip().upper()
        if not column:
            if len(columns) != 1:
                # Do not invent a ticket number from an unknown multi-column schema.
                return None
            column = columns[0]
        if column not in columns:
            raise RuntimeError("Configured Oracle ticket ID column is missing")
        value = row[columns.index(column)]
        if value is None or not str(value).strip():
            raise RuntimeError("Oracle ticket ID is empty")
        return str(value).strip()
