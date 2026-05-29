import uvicorn

from alarm_manager_server.config import settings


def main() -> None:
    uvicorn.run(
        "alarm_manager_server.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
