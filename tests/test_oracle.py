from unittest.mock import MagicMock, patch

import pytest

from alarm_manager_server.config import Settings
from alarm_manager_server.plugins.oracle import OracleTicketHandler, SQL
from alarm_manager_server.plugins.registry import discover_ticket_handlers
from alarm_manager_server.worker.ticket_handlers import TicketHandlerContext
from alarm_manager_server.worker.tickets import TicketEvent


def config(**kwargs):
    return Settings(_env_file=None, **dict(
        oracle_dsn="jdbc:oracle:thin:@//db.example:1523/service",
        oracle_user="test", oracle_password="secret",
        oracle_id_dept=215094, oracle_id_build=30,
        oracle_id_def=11449, oracle_id_monit=1, **kwargs,
    ))


def context(action="created"):
    return TicketHandlerContext(
        TicketEvent(action, "T-1", None, [], title="Объект '1'"),
        {"created_at": "2026-07-23T12:37:00+00:00",
         "updated_at": "2026-07-23T12:38:00+00:00",
         "closed_at": "2026-07-23T13:00:00+00:00"},
        "Дефект 'оборудования'",
    )


def driver_mock():
    driver = MagicMock()
    driver.Cursor = type("ResultCursor", (), {})
    connection = driver.connect.return_value.__enter__.return_value
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (123,)
    return driver, connection, cursor


def test_discovery_and_secrets():
    assert OracleTicketHandler.from_settings(Settings(_env_file=None)) is None
    cfg = config()
    assert any(isinstance(h, OracleTicketHandler) for h in discover_ticket_handlers(cfg))
    assert "secret" not in repr(cfg)
    assert OracleTicketHandler.from_settings(cfg.model_copy(update={"oracle_id_def": None})) is None


def test_bound_query_commit_and_jdbc():
    driver, connection, cursor = driver_mock()
    with patch("alarm_manager_server.plugins.oracle.import_module", return_value=driver):
        result = OracleTicketHandler(config()).on_ticket_event(context())
    assert result.external_ref == "123"
    assert result.external_meta["oracle_recorded"] is True
    driver.connect.assert_called_once_with(
        user="test", password="secret", dsn="//db.example:1523/service", tcp_connect_timeout=10,
    )
    sql, params = cursor.execute.call_args.args
    assert sql == SQL
    assert params["p_b_date"] == "23.07.2026 15:37"
    assert params["p_e_date"] == "23.07.2026 15:38"
    assert params["p_name_equip"] == "Объект '1'"
    assert params["p_name_defect"] == "Дефект 'оборудования'"
    assert params["p_id_dept"] == 215094
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    connection.__exit__.assert_called_once()


@pytest.mark.parametrize("failure", ["execute", "empty", "commit"])
def test_failure_rolls_back(failure):
    driver, connection, cursor = driver_mock()
    if failure == "execute":
        cursor.execute.side_effect = RuntimeError("database failure")
    elif failure == "empty":
        cursor.fetchone.return_value = (None,)
    else:
        connection.commit.side_effect = RuntimeError("commit failure")
    with patch("alarm_manager_server.plugins.oracle.import_module", return_value=driver):
        with pytest.raises(RuntimeError):
            OracleTicketHandler(config()).on_ticket_event(context())
    connection.rollback.assert_called_once()


def test_close_event_and_skip_duplicates():
    driver, connection, cursor = driver_mock()
    handler = OracleTicketHandler(config(oracle_event="closed"))
    with patch("alarm_manager_server.plugins.oracle.import_module", return_value=driver):
        assert handler.on_ticket_event(context()) is None
        assert handler.on_ticket_event(context("updated")) is None
        handler.on_ticket_event(context("closed"))
        assert cursor.execute.call_args.args[1]["p_e_date"] == "23.07.2026 16:00"
        ctx = context("closed")
        ctx.ticket["external_meta"] = {"oracle_recorded": True}
        assert handler.on_ticket_event(ctx) is None
    connection.commit.assert_called_once()


def test_cursor_id_column():
    handler = OracleTicketHandler(config(oracle_result_id_column="ticket_id"))
    cursor = MagicMock()
    cursor.description = [("STATUS",), ("TICKET_ID",)]
    cursor.fetchone.return_value = ("ok", 456)
    assert handler._cursor_ref(cursor) == "456"
    cursor.description = [("ERROR",)]
    with pytest.raises(RuntimeError, match="column is missing"):
        handler._cursor_ref(cursor)
