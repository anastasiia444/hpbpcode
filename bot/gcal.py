"""Google Calendar через сервісний акаунт.

Сервісний акаунт бачить лише календар, до якого власниця відкрила йому доступ
(«Вносити зміни в події»). Запрошувати учасників він не може без Google Workspace
domain-wide delegation, тому контакти кандидата пишемо в опис події.
"""

import asyncio
from datetime import datetime
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendar:
    def __init__(self, credentials_file: Path, calendar_id: str, timezone_name: str):
        credentials = service_account.Credentials.from_service_account_file(
            str(credentials_file), scopes=SCOPES
        )
        self.service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        self.calendar_id = calendar_id
        self.timezone_name = timezone_name

    async def create_event(
        self, title: str, start: datetime, end: datetime, description: str = ""
    ) -> dict:
        body = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start.isoformat(), "timeZone": self.timezone_name},
            "end": {"dateTime": end.isoformat(), "timeZone": self.timezone_name},
        }
        request = self.service.events().insert(calendarId=self.calendar_id, body=body)
        return await asyncio.to_thread(request.execute)

    async def busy_slots(self, start: datetime, end: datetime) -> list[dict]:
        body = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "timeZone": self.timezone_name,
            "items": [{"id": self.calendar_id}],
        }
        request = self.service.freebusy().query(body=body)
        result = await asyncio.to_thread(request.execute)
        calendar = result["calendars"][self.calendar_id]
        if calendar.get("errors"):
            raise RuntimeError(f"Google Calendar: {calendar['errors']}")
        return calendar["busy"]
