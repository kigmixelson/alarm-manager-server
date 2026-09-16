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
        user="test", password="secret", dsn="//db.example:1523/service", tcp_connect_timeout=30,
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


def test_confirmation_logs_and_budget(caplog):
    driver, connection, cursor = driver_mock()
    with caplog.at_level("INFO"), patch(
        "alarm_manager_server.plugins.oracle.import_module", return_value=driver
    ), patch("alarm_manager_server.plugins.oracle.monotonic", side_effect=[0, 0, 0, 12, 15, 16]):
        OracleTicketHandler(config()).on_ticket_event(context())
    assert connection.call_timeout == 15000
    assert "Oracle sending ticket=T-1" in caplog.text
    assert "Oracle response received ticket=T-1" in caplog.text
    assert "Oracle confirmed ticket=T-1" in caplog.text
    assert "secret" not in caplog.text


def test_deadline_expired_no_commit(caplog):
    driver, connection, cursor = driver_mock()
    with patch("alarm_manager_server.plugins.oracle.import_module", return_value=driver), patch(
        "alarm_manager_server.plugins.oracle.monotonic", side_effect=[0, 0, 0, 31, 31]
    ):
        with pytest.raises(TimeoutError):
            OracleTicketHandler(config()).on_ticket_event(context())
    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()
    assert "confirmation missing ticket=T-1 stage=fetch reason=timeout" in caplog.text
    assert "Oracle confirmed" not in caplog.text


def test_commit_failure_and_rollback_failure_preserve_error(caplog):
    driver, connection, cursor = driver_mock()
    connection.commit.side_effect = RuntimeError("commit lost")
    connection.rollback.side_effect = RuntimeError("rollback lost")
    with patch("alarm_manager_server.plugins.oracle.import_module", return_value=driver):
        with pytest.raises(RuntimeError, match="commit lost"):
            OracleTicketHandler(config()).on_ticket_event(context())
    assert "stage=commit" in caplog.text
    assert "Oracle confirmed" not in caplog.text


@pytest.mark.parametrize("empty", [None, (None,), ("",)])
def test_missing_confirmation_logged(empty, caplog):
    driver, connection, cursor = driver_mock()
    cursor.fetchone.return_value = empty
    with patch("alarm_manager_server.plugins.oracle.import_module", return_value=driver):
        with pytest.raises(RuntimeError):
            OracleTicketHandler(config()).on_ticket_event(context())
    assert "Oracle confirmation missing ticket=T-1" in caplog.text
    connection.commit.assert_not_called()


def test_outcome_comments_success_and_failure():
    driver, connection, cursor = driver_mock()
    handler = OracleTicketHandler(config(oracle_comment_module_name="Модуль SD"))
    ctx = context()
    ctx.ticket["snapshot"] = {"member_ids": ["i1"]}
    with patch("alarm_manager_server.plugins.oracle.import_module", return_value=driver):
        handler.on_ticket_event(ctx)
        cursor.execute.side_effect = TimeoutError("private diagnostic")
        with pytest.raises(TimeoutError):
            handler.on_ticket_event(ctx)
    comments = ctx.ticket["external_meta"]["oracle_comments"]
    assert len(comments) == 2
    assert "[Модуль SD]" in comments[0]["text"]
    assert "успешно" in comments[0]["text"]
    assert "истекло время ожидания" in comments[1]["text"]
    assert "private diagnostic" not in comments[1]["text"]
    assert comments[1]["pending_incident_ids"] == ["i1"]


def test_thick_initialization_and_descriptor():
    from alarm_manager_server.plugins.oracle import oracle_driver, oracle_connect

    cfg = config(oracle_mode="thick")
    driver, _, _ = driver_mock()
    params = driver.ConnectParams.return_value
    params.get_connect_string.return_value = "(DESCRIPTION=test)"
    with patch("alarm_manager_server.plugins.oracle.import_module", return_value=driver):
        assert oracle_driver(cfg) is driver
    driver.init_oracle_client.assert_called_once_with()
    oracle_connect(driver, cfg)
    params.parse_connect_string.assert_called_once_with("//db.example:1523/service")
    params.set.assert_called_once_with(tcp_connect_timeout=30, retry_count=0)
    assert driver.connect.call_args.kwargs["dsn"] == "(DESCRIPTION=test)"


def test_thin_does_not_load_oci():
    from alarm_manager_server.plugins.oracle import oracle_driver

    driver, _, _ = driver_mock()
    with patch("alarm_manager_server.plugins.oracle.import_module", return_value=driver):
        oracle_driver(config())
    driver.init_oracle_client.assert_not_called()
