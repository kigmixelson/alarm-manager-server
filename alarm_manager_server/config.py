from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    """User-writable state dir (local venv). Docker .env overrides with /var/... paths."""
    return Path.home() / ".local" / "share" / "alarm-manager"


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
    cache_dir: str = Field(default_factory=lambda: str(default_data_dir() / "cache"))
    cache_ttl_incidents_sec: int = 120
    cache_ttl_objects_sec: int = 3600
    cache_ttl_object_paths_sec: int = 3600
    cache_ttl_state_labels_sec: int = 86400
    cache_ttl_class_ids_sec: int = 86400

    tickets_file: str = Field(default_factory=lambda: str(default_data_dir() / "tickets.json"))
    # Comma-separated import paths: module:Class (see worker/ticket_handlers.py)
    ticket_handlers: str = ""

    # --- Jira plugin (enabled when URL + token + project are set) ---
    jira_base_url: str = ""
    jira_user: str = ""
    jira_api_token: SecretStr = SecretStr("")
    jira_project_key: str = ""
    jira_issue_type: str = "Task"
    # Transition name on CLOSE (e.g. Done); empty = comment only
    jira_close_transition: str = ""

    # --- Redmine plugin (enabled when URL + API key + project are set) ---
    redmine_base_url: str = ""
    redmine_api_key: SecretStr = SecretStr("")
    redmine_project_id: str = ""
    redmine_tracker_id: int = 1
    # Status id on CLOSE; 0 = comment only (no status change)
    redmine_status_closed_id: int = 0

    # --- Freshdesk plugin (enabled when URL + API key + requester email are set) ---
    freshdesk_base_url: str = ""
    freshdesk_api_key: SecretStr = SecretStr("")
    freshdesk_requester_email: str = ""
    freshdesk_priority: int = 2
    freshdesk_status_open: int = 2
    freshdesk_status_closed: int = 5

    # --- ServiceNow plugin ---
    servicenow_instance_url: str = ""
    servicenow_user: str = ""
    servicenow_password: SecretStr = SecretStr("")
    servicenow_oauth_token: SecretStr = SecretStr("")
    servicenow_table: str = "incident"
    servicenow_caller_id: str = ""
    servicenow_assignment_group: str = ""
    servicenow_impact: str = "2"
    servicenow_urgency: str = "2"
    servicenow_close_state: str = "7"

    # --- SimpleOne plugin ---
    simpleone_base_url: str = ""
    simpleone_api_token: SecretStr = SecretStr("")
    simpleone_table: str = "itsm_incident"
    simpleone_caller: str = ""
    simpleone_service: str = ""
    simpleone_contact_type: str = "email"
    simpleone_close_state: str = ""

    # --- Naumen / ITSM 365 plugin ---
    naumen_base_url: str = ""
    naumen_access_key: SecretStr = SecretStr("")
    naumen_meta_class: str = "serviceCall$serviceCall"
    naumen_client: str = ""
    naumen_client_employee: str = ""
    naumen_agreement: str = ""
    naumen_service: str = ""
    naumen_office: str = ""
    naumen_comment_author: str = ""
    naumen_close_state: str = "resolved"
    naumen_close_code: str = "resolved"

    # --- ELMA365 plugin ---
    elma_base_url: str = ""
    elma_api_token: SecretStr = SecretStr("")
    elma_namespace: str = ""
    elma_app_code: str = ""
    elma_title_field: str = "__name"
    elma_description_field: str = ""
    elma_close_status: str = ""
    elma_context_extra: str = "{}"
    # bearer | x-token
    elma_auth_mode: str = "bearer"

    # --- Bitrix24 plugin (incoming webhook) ---
    bitrix24_webhook_url: str = ""
    bitrix24_responsible_id: int = 0
    bitrix24_created_by: int = 0
    bitrix24_group_id: int = 0
    bitrix24_comment_author_id: int = 0
    bitrix24_complete_on_close: bool = True

    # --- HP Service Manager / Service Desk plugin ---
    hpsm_base_url: str = ""
    hpsm_user: str = ""
    hpsm_password: SecretStr = SecretStr("")
    hpsm_collection: str = "incidents"
    hpsm_resource_name: str = "Incident"
    hpsm_impact: str = "3"
    hpsm_urgency: str = "3"
    hpsm_category: str = "incident"
    hpsm_assignment_group: str = ""
    hpsm_affected_ci: str = ""
    hpsm_close_status: str = "Closed"
    hpsm_closure_code: str = ""

    # Oracle ticket function (credentials are never included in repr).
    oracle_comment_module_name: str = "Alarm Manager"
    oracle_saymon_comment_enabled: bool = True
    oracle_dsn: str = ""
    oracle_user: str = ""
    oracle_password: SecretStr = SecretStr("")
    oracle_id_dept: int | None = None
    oracle_id_build: int | None = None
    oracle_id_def: int | None = None
    oracle_id_monit: int | None = None
    oracle_event: Literal["created", "closed"] = "created"
    oracle_b_date: str = ""
    oracle_e_date: str = ""
    oracle_timezone: str = "Europe/Moscow"
    oracle_name_equip: str = ""
    oracle_name_defect: str = ""
    oracle_executed_work: str = ""
    oracle_location: str = ""
    oracle_result_id_column: str = ""
    oracle_connect_timeout_sec: float = Field(default=30, gt=0)
    oracle_call_timeout_ms: int = Field(default=30000, gt=0)

    @property
    def oracle_enabled(self) -> bool:
        return bool(
            self.oracle_dsn.strip()
            and self.oracle_user.strip()
            and self.oracle_password.get_secret_value()
            and all(value is not None for value in (
                self.oracle_id_dept, self.oracle_id_build,
                self.oracle_id_def, self.oracle_id_monit,
            ))
        )

    # Comment on SAYMON incidents after external ticket CREATE
    ticket_saymon_comment_enabled: bool = True
    ticket_saymon_comment_template: str = "Service Desk ({system}): {external_ref}"

    @property
    def jira_enabled(self) -> bool:
        return bool(
            self.jira_base_url.strip()
            and self.jira_user.strip()
            and self.jira_api_token.get_secret_value().strip()
            and self.jira_project_key.strip()
        )

    @property
    def redmine_enabled(self) -> bool:
        return bool(
            self.redmine_base_url.strip()
            and self.redmine_api_key.get_secret_value().strip()
            and self.redmine_project_id.strip()
        )

    @property
    def freshdesk_enabled(self) -> bool:
        return bool(
            self.freshdesk_base_url.strip()
            and self.freshdesk_api_key.get_secret_value().strip()
            and self.freshdesk_requester_email.strip()
        )

    @property
    def servicenow_enabled(self) -> bool:
        if not self.servicenow_instance_url.strip():
            return False
        if self.servicenow_oauth_token.get_secret_value().strip():
            return True
        return bool(
            self.servicenow_user.strip()
            and self.servicenow_password.get_secret_value().strip()
        )

    @property
    def simpleone_enabled(self) -> bool:
        return bool(
            self.simpleone_base_url.strip()
            and self.simpleone_api_token.get_secret_value().strip()
            and self.simpleone_caller.strip()
        )

    @property
    def naumen_enabled(self) -> bool:
        return bool(
            self.naumen_base_url.strip()
            and self.naumen_access_key.get_secret_value().strip()
            and self.naumen_client.strip()
            and self.naumen_client_employee.strip()
            and self.naumen_agreement.strip()
            and self.naumen_service.strip()
        )

    @property
    def elma_enabled(self) -> bool:
        return bool(
            self.elma_base_url.strip()
            and self.elma_api_token.get_secret_value().strip()
            and self.elma_namespace.strip()
            and self.elma_app_code.strip()
        )

    @property
    def bitrix24_enabled(self) -> bool:
        return bool(
            self.bitrix24_webhook_url.strip()
            and self.bitrix24_responsible_id > 0
        )

    @property
    def hpsm_enabled(self) -> bool:
        return bool(
            self.hpsm_base_url.strip()
            and self.hpsm_user.strip()
            and self.hpsm_password.get_secret_value().strip()
        )

    @property
    def api_url(self) -> str:
        return f"{self.saymon_base_url.rstrip('/')}{self.saymon_api_prefix}"


settings = Settings()
