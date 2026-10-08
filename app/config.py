import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://sergey_rodionov:changeme@localhost:5433/sergey_rodionov",
    )
    schema: str = os.getenv("DB_SCHEMA", "sergey_rodionov")
    session_secret: str = os.getenv("SESSION_SECRET", "")
    https_only: bool = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"
    demo_login: str = os.getenv("DEMO_LOGIN", "demo")
    demo_password: str = os.getenv("DEMO_PASSWORD", "demo")


settings = Settings()
if not re.fullmatch(r"[a-z][a-z0-9_]*", settings.schema):
    raise ValueError("DB_SCHEMA must be a lowercase PostgreSQL identifier")
