"""База кандидатів, вакансій, інтерв'ю та нагадувань (SQLite)."""

import csv
import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

STAGES = [
    "Знайдено",
    "Написали",
    "Відповів",
    "Скринінг",
    "Інтерв'ю",
    "Офер",
    "Вийшов",
    "Відмова",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS vacancies (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'відкрита',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    vacancy_id INTEGER REFERENCES vacancies(id),
    stage TEXT NOT NULL,
    source TEXT,
    contact TEXT,
    profile_url TEXT,
    fit_score INTEGER,
    summary TEXT,
    notes TEXT,
    next_action TEXT,
    next_action_date TEXT,
    resume_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stage_history (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    stage TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interviews (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    starts_at TEXT NOT NULL,
    duration_min INTEGER NOT NULL,
    kind TEXT,
    calendar_event_id TEXT,
    feedback TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    remind_at TEXT NOT NULL,
    text TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

CANDIDATE_FIELDS = {
    "name",
    "vacancy_id",
    "stage",
    "source",
    "contact",
    "profile_url",
    "fit_score",
    "summary",
    "notes",
    "next_action",
    "next_action_date",
    "resume_path",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_stage(stage: str) -> str:
    if stage not in STAGES:
        raise ValueError(f"Невідомий етап «{stage}». Можливі: {', '.join(STAGES)}")
    return stage


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # --- Вакансії ---

    def add_vacancy(self, title: str, description: str | None = None) -> dict:
        cur = self.conn.execute(
            "INSERT INTO vacancies (title, description, created_at) VALUES (?, ?, ?)",
            (title, description, _now()),
        )
        self.conn.commit()
        return self.get_vacancy(cur.lastrowid)

    def get_vacancy(self, vacancy_id: int) -> dict | None:
        return self._one("SELECT * FROM vacancies WHERE id = ?", (vacancy_id,))

    def list_vacancies(self, include_closed: bool = False) -> list[dict]:
        sql = """
            SELECT v.id, v.title, v.status, v.created_at,
                   COUNT(c.id) AS candidates
            FROM vacancies v LEFT JOIN candidates c ON c.vacancy_id = v.id
            {where}
            GROUP BY v.id ORDER BY v.id DESC
        """
        where = "" if include_closed else "WHERE v.status = 'відкрита'"
        return self._all(sql.format(where=where))

    def set_vacancy_status(self, vacancy_id: int, status: str) -> dict:
        self.conn.execute("UPDATE vacancies SET status = ? WHERE id = ?", (status, vacancy_id))
        self.conn.commit()
        vacancy = self.get_vacancy(vacancy_id)
        if vacancy is None:
            raise ValueError(f"Вакансію #{vacancy_id} не знайдено")
        return vacancy

    # --- Кандидати ---

    def add_candidate(self, name: str, stage: str = "Знайдено", **fields) -> dict:
        check_stage(stage)
        unknown = set(fields) - CANDIDATE_FIELDS
        if unknown:
            raise ValueError(f"Невідомі поля: {', '.join(sorted(unknown))}")
        fields = {k: v for k, v in fields.items() if v is not None}
        now = _now()
        columns = ["name", "stage", "created_at", "updated_at", *fields]
        values = [name, stage, now, now, *fields.values()]
        cur = self.conn.execute(
            f"INSERT INTO candidates ({', '.join(columns)}) VALUES ({', '.join('?' * len(values))})",
            values,
        )
        self.conn.execute(
            "INSERT INTO stage_history (candidate_id, stage, changed_at) VALUES (?, ?, ?)",
            (cur.lastrowid, stage, now),
        )
        self.conn.commit()
        return self.get_candidate(cur.lastrowid)

    def get_candidate(self, candidate_id: int) -> dict | None:
        return self._one(
            """SELECT c.*, v.title AS vacancy_title FROM candidates c
               LEFT JOIN vacancies v ON v.id = c.vacancy_id WHERE c.id = ?""",
            (candidate_id,),
        )

    def update_candidate(self, candidate_id: int, **fields) -> dict:
        current = self.get_candidate(candidate_id)
        if current is None:
            raise ValueError(f"Кандидата #{candidate_id} не знайдено")
        unknown = set(fields) - CANDIDATE_FIELDS
        if unknown:
            raise ValueError(f"Невідомі поля: {', '.join(sorted(unknown))}")
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            return current
        now = _now()
        if "stage" in fields:
            check_stage(fields["stage"])
            if fields["stage"] != current["stage"]:
                self.conn.execute(
                    "INSERT INTO stage_history (candidate_id, stage, changed_at) VALUES (?, ?, ?)",
                    (candidate_id, fields["stage"], now),
                )
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE candidates SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), now, candidate_id),
        )
        self.conn.commit()
        return self.get_candidate(candidate_id)

    def find_candidates(
        self,
        query: str | None = None,
        vacancy_id: int | None = None,
        stage: str | None = None,
        limit: int = 30,
    ) -> list[dict]:
        conditions, params = [], []
        if query:
            conditions.append(
                "(c.name LIKE ? OR c.summary LIKE ? OR c.notes LIKE ? OR c.contact LIKE ? OR c.source LIKE ?)"
            )
            params += [f"%{query}%"] * 5
        if vacancy_id is not None:
            conditions.append("c.vacancy_id = ?")
            params.append(vacancy_id)
        if stage:
            conditions.append("c.stage = ?")
            params.append(check_stage(stage))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return self._all(
            f"""SELECT c.id, c.name, c.stage, c.fit_score, c.source, c.contact,
                       c.next_action, c.next_action_date, v.title AS vacancy_title
                FROM candidates c LEFT JOIN vacancies v ON v.id = c.vacancy_id
                {where} ORDER BY c.updated_at DESC LIMIT ?""",
            (*params, limit),
        )

    def due_actions(self, today: str) -> list[dict]:
        """Кандидати, у яких дата наступної дії сьогодні або вже минула."""
        return self._all(
            """SELECT c.id, c.name, c.stage, c.next_action, c.next_action_date,
                      v.title AS vacancy_title
               FROM candidates c LEFT JOIN vacancies v ON v.id = c.vacancy_id
               WHERE c.next_action_date IS NOT NULL AND c.next_action_date <= ?
                 AND c.stage NOT IN ('Вийшов', 'Відмова')
               ORDER BY c.next_action_date""",
            (today,),
        )

    # --- Інтерв'ю ---

    def add_interview(
        self,
        candidate_id: int,
        starts_at: str,
        duration_min: int,
        kind: str | None = None,
        calendar_event_id: str | None = None,
    ) -> dict:
        if self.get_candidate(candidate_id) is None:
            raise ValueError(f"Кандидата #{candidate_id} не знайдено")
        cur = self.conn.execute(
            """INSERT INTO interviews (candidate_id, starts_at, duration_min, kind,
                                       calendar_event_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (candidate_id, starts_at, duration_min, kind, calendar_event_id, _now()),
        )
        self.conn.commit()
        return self.get_interview(cur.lastrowid)

    def get_interview(self, interview_id: int) -> dict | None:
        return self._one(
            """SELECT i.*, c.name AS candidate_name FROM interviews i
               JOIN candidates c ON c.id = i.candidate_id WHERE i.id = ?""",
            (interview_id,),
        )

    def list_interviews(self, start: str, end: str) -> list[dict]:
        return self._all(
            """SELECT i.*, c.name AS candidate_name, v.title AS vacancy_title
               FROM interviews i JOIN candidates c ON c.id = i.candidate_id
               LEFT JOIN vacancies v ON v.id = c.vacancy_id
               WHERE i.starts_at >= ? AND i.starts_at < ? ORDER BY i.starts_at""",
            (start, end),
        )

    def save_interview_feedback(self, interview_id: int, feedback: str) -> dict:
        if self.get_interview(interview_id) is None:
            raise ValueError(f"Інтерв'ю #{interview_id} не знайдено")
        self.conn.execute(
            "UPDATE interviews SET feedback = ? WHERE id = ?", (feedback, interview_id)
        )
        self.conn.commit()
        return self.get_interview(interview_id)

    # --- Нагадування ---

    def add_reminder(self, remind_at_utc: str, text: str) -> dict:
        cur = self.conn.execute(
            "INSERT INTO reminders (remind_at, text, created_at) VALUES (?, ?, ?)",
            (remind_at_utc, text, _now()),
        )
        self.conn.commit()
        return self._one("SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,))

    def pop_due_reminders(self, now_utc: str) -> list[dict]:
        due = self._all(
            "SELECT * FROM reminders WHERE sent = 0 AND remind_at <= ? ORDER BY remind_at",
            (now_utc,),
        )
        if due:
            self.conn.executemany(
                "UPDATE reminders SET sent = 1 WHERE id = ?", [(r["id"],) for r in due]
            )
            self.conn.commit()
        return due

    def pending_reminders(self) -> list[dict]:
        return self._all("SELECT * FROM reminders WHERE sent = 0 ORDER BY remind_at")

    # --- Звіти ---

    def pipeline_counts(self, vacancy_id: int | None = None) -> dict[str, int]:
        where, params = ("WHERE vacancy_id = ?", (vacancy_id,)) if vacancy_id else ("", ())
        rows = self._all(
            f"SELECT stage, COUNT(*) AS n FROM candidates {where} GROUP BY stage", params
        )
        counts = {stage: 0 for stage in STAGES}
        counts.update({r["stage"]: r["n"] for r in rows})
        return counts

    def stage_changes_since(self, since_utc: str) -> dict[str, int]:
        rows = self._all(
            "SELECT stage, COUNT(*) AS n FROM stage_history WHERE changed_at >= ? GROUP BY stage",
            (since_utc,),
        )
        return {r["stage"]: r["n"] for r in rows}

    def source_stats(self) -> list[dict]:
        return self._all(
            """SELECT COALESCE(source, 'не вказано') AS source,
                      COUNT(*) AS total,
                      SUM(stage IN ('Відповів', 'Скринінг', 'Інтерв''ю', 'Офер', 'Вийшов')) AS replied,
                      SUM(stage IN ('Інтерв''ю', 'Офер', 'Вийшов')) AS interviewed,
                      SUM(stage = 'Вийшов') AS hired
               FROM candidates GROUP BY 1 ORDER BY total DESC"""
        )

    def export_candidates_csv(self) -> bytes:
        rows = self._all(
            """SELECT c.id, c.name, v.title AS vacancy, c.stage, c.fit_score, c.source,
                      c.contact, c.profile_url, c.next_action, c.next_action_date,
                      c.summary, c.notes, c.created_at, c.updated_at
               FROM candidates c LEFT JOIN vacancies v ON v.id = c.vacancy_id
               ORDER BY c.id"""
        )
        buffer = io.StringIO()
        headers = [
            "ID", "Ім'я", "Вакансія", "Етап", "Відповідність %", "Джерело", "Контакт",
            "Профіль", "Наступна дія", "Дата дії", "Короткий опис", "Нотатки",
            "Створено", "Оновлено",
        ]
        writer = csv.writer(buffer)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row.values())
        # BOM, щоб Excel правильно показав кирилицю
        return ("﻿" + buffer.getvalue()).encode("utf-8")
