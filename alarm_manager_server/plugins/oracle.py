"""Record a ticket through REPAIR.REP_MONIT_SYSTEM_CURS using bound values."""

from __future__ import annotations

from datetime import datetime
from importlib import import_module
from zoneinfo import ZoneInfo

from alarm_manager_server.config import Settings
from alarm_manager_server.worker.ticket_handlers import (
    BaseTicketHandler, HandlerResult, TicketHandlerContext,
)

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
        dsn = cfg.oracle_dsn.strip().removeprefix("jdbc:oracle:thin:@")
        driver = import_module("oracledb")
        with driver.connect(
            user=cfg.oracle_user, password=cfg.oracle_password.get_secret_value(),
            dsn=dsn, tcp_connect_timeout=cfg.oracle_connect_timeout_sec,
        ) as connection:
            connection.call_timeout = cfg.oracle_call_timeout_ms
            try:
                with connection.cursor() as cursor:
                    cursor.execute(SQL, params)
                    row = cursor.fetchone()
                    if row is None or row[0] is None:
                        raise RuntimeError("Oracle ticket function returned no result")
                    result = row[0]
                    if isinstance(result, driver.Cursor):
                        with result:
                            ref = self._cursor_ref(result)
                    elif isinstance(result, (str, int)):
                        ref = str(result).strip()
                        if not ref:
                            raise RuntimeError("Oracle ticket function returned an empty result")
                    else:
                        raise RuntimeError("Unsupported Oracle ticket function result type")
                connection.commit()
            except Exception:
                connection.rollback()
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
