"""Check Oracle login with SELECT 1; never call the ticket function."""
from alarm_manager_server.config import settings
from alarm_manager_server.plugins.oracle import oracle_connect, oracle_driver


def main() -> None:
    driver = oracle_driver(settings)
    with oracle_connect(driver, settings) as connection:
        connection.call_timeout = settings.oracle_call_timeout_ms
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM dual")
            if cursor.fetchone() != (1,):
                raise RuntimeError("Unexpected Oracle connection check result")
        print(f"Oracle connection OK; mode={settings.oracle_mode}; database={connection.version}")


if __name__ == "__main__":
    main()
