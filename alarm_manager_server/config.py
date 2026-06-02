from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    saymon_base_url: str = "http://localhost:8080"
    saymon_api_prefix: str = "/node/api"
    saymon_login: str = ""
    saymon_password: SecretStr = SecretStr("")
    saymon_auth_redirect_url: str = ""

    group_by_class_names: list[str] = ["Host", "Router", "Local Address"]
    group_by_depth: int = 4

    macros: list[str] = [
        "{{parent[class.id=30,3,24].properties[17. Ответственный]}}",
        "{{parent[class.id=30,3,24].properties[20. Группа для уведомления администраторов систем]}}",
    ]
    macro_depth: int = 8

    fetch_limit: int = 1000
    history_limit: int = 5000
    fetch_page_size: int = 500

    server_url: str = "http://127.0.0.1:4800"
    worker_interval_sec: float = 60.0
    incident_link_template: str = "{saymon_base_url}/saymon.local/apps/alarm-manager?incident={id}"

    cache_enabled: bool = True
    cache_dir: str = "/var/cache/alarm-manager"
    cache_ttl_incidents_sec: int = 120
    cache_ttl_objects_sec: int = 3600
    cache_ttl_object_paths_sec: int = 3600
    cache_ttl_state_labels_sec: int = 86400
    cache_ttl_class_ids_sec: int = 86400

    tickets_file: str = "/var/lib/alarm-manager/tickets.json"
    # Comma-separated import paths: module:Class (see worker/ticket_handlers.py)
    ticket_handlers: str = ""

    @property
    def api_url(self) -> str:
        return f"{self.saymon_base_url.rstrip('/')}{self.saymon_api_prefix}"


settings = Settings()
