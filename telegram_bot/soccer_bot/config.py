"""
Bot settings loaded from environment variables (or a local .env file).
"""
import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    allowed_chat_ids: frozenset[int]
    data_source: str  # "mock" | "databricks"
    catalog: str
    databricks_profile: str | None
    databricks_host: str | None
    databricks_http_path: str | None
    databricks_token: str | None
    cache_ttl_seconds: int
    access_mode: str  # "allowlist" | "public"
    admin_chat_ids: frozenset[int]
    nl_enabled: bool
    nl_max_questions: int
    nl_window_hours: int
    nl_max_chars: int
    usage_db_path: str


def _parse_chat_ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


def load_settings() -> Settings:
    load_dotenv()

    data_source = os.getenv("DATA_SOURCE", "mock").lower()
    if data_source not in ("mock", "databricks"):
        raise ValueError(f"DATA_SOURCE must be 'mock' or 'databricks', got: {data_source}")

    # catalog is interpolated into SQL as an identifier, so only allow safe names
    catalog = os.getenv("DATABRICKS_CATALOG", "soccer")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", catalog):
        raise ValueError(f"Invalid catalog name: {catalog}")

    access_mode = os.getenv("ACCESS_MODE", "allowlist").lower()
    if access_mode not in ("allowlist", "public"):
        raise ValueError(f"ACCESS_MODE must be 'allowlist' or 'public', got: {access_mode}")

    return Settings(
        telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
        allowed_chat_ids=_parse_chat_ids(os.getenv("ALLOWED_CHAT_IDS", "")),
        data_source=data_source,
        catalog=catalog,
        databricks_profile=os.getenv("DATABRICKS_CONFIG_PROFILE"),
        databricks_host=os.getenv("DATABRICKS_HOST"),
        databricks_http_path=os.getenv("DATABRICKS_HTTP_PATH"),
        databricks_token=os.getenv("DATABRICKS_TOKEN"),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600")),
        access_mode=access_mode,
        admin_chat_ids=_parse_chat_ids(os.getenv("ADMIN_CHAT_IDS", "")),
        nl_enabled=os.getenv("NL_ENABLED", "false").lower() == "true",
        nl_max_questions=int(os.getenv("NL_MAX_QUESTIONS", "3")),
        nl_window_hours=int(os.getenv("NL_WINDOW_HOURS", "72")),
        nl_max_chars=int(os.getenv("NL_MAX_CHARS", "300")),
        usage_db_path=os.getenv("USAGE_DB_PATH", "usage.sqlite3"),
    )
