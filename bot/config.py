"""Налаштування бота з змінних оточення (.env)."""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _int_or_none(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_user_id: int | None
    model: str
    effort: str
    timezone: ZoneInfo
    data_dir: Path
    knowledge_dir: Path
    profile_file: Path
    google_calendar_id: str | None
    google_credentials_file: Path
    daily_digest_hour: int
    weekly_report_weekday: int  # 0 = понеділок

    @property
    def db_path(self) -> Path:
        return self.data_dir / "recruiting.db"

    @property
    def resumes_dir(self) -> Path:
        return self.data_dir / "resumes"

    @property
    def calendar_enabled(self) -> bool:
        return bool(self.google_calendar_id) and self.google_credentials_file.exists()


def load_config() -> Config:
    data_dir = Path(os.environ.get("DATA_DIR", PROJECT_DIR / "data"))
    return Config(
        telegram_token=os.environ["TELEGRAM_BOT_TOKEN"],
        allowed_user_id=_int_or_none(os.environ.get("ALLOWED_TELEGRAM_USER_ID")),
        model=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"),
        effort=os.environ.get("CLAUDE_EFFORT", "medium"),
        timezone=ZoneInfo(os.environ.get("TIMEZONE", "Europe/Kyiv")),
        data_dir=data_dir,
        knowledge_dir=Path(os.environ.get("KNOWLEDGE_DIR", PROJECT_DIR / ".claude")),
        profile_file=Path(os.environ.get("PROFILE_FILE", PROJECT_DIR / "CLAUDE.md")),
        google_calendar_id=os.environ.get("GOOGLE_CALENDAR_ID") or None,
        google_credentials_file=Path(
            os.environ.get("GOOGLE_CREDENTIALS_FILE", data_dir / "google-service-account.json")
        ),
        daily_digest_hour=int(os.environ.get("DAILY_DIGEST_HOUR", "9")),
        weekly_report_weekday=int(os.environ.get("WEEKLY_REPORT_WEEKDAY", "0")),
    )
