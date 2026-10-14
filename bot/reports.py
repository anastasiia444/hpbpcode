"""Тексти щоденного дайджесту і тижневого звіту (без виклику Claude)."""

from datetime import datetime, timedelta, timezone

from .config import Config
from .db import STAGES, Database


def _local_time(iso: str, config: Config) -> str:
    return datetime.fromisoformat(iso).astimezone(config.timezone).strftime("%H:%M")


def daily_digest(db: Database, config: Config) -> str | None:
    now = datetime.now(config.timezone)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    interviews = db.list_interviews(start.isoformat(), (start + timedelta(days=1)).isoformat())
    actions = db.due_actions(now.date().isoformat())
    if not interviews and not actions:
        return None

    lines = [f"☀️ <b>План на {now:%d.%m}</b>"]
    if interviews:
        lines.append("\n<b>Інтерв'ю сьогодні:</b>")
        for i in interviews:
            lines.append(f"• {_local_time(i['starts_at'], config)} — {i['kind'] or 'інтерв’ю'}: {i['candidate_name']} (#{i['candidate_id']})")
    if actions:
        lines.append("\n<b>Час нагадати або зробити наступний крок:</b>")
        for a in actions:
            overdue = " ⚠️ прострочено" if a["next_action_date"] < now.date().isoformat() else ""
            vacancy = f", {a['vacancy_title']}" if a["vacancy_title"] else ""
            lines.append(
                f"• #{a['id']} {a['name']}{vacancy} — {a['next_action'] or 'наступний крок'}{overdue}"
            )
        lines.append("\nНапишіть, наприклад: «напиши нагадування для #12».")
    return "\n".join(lines)


def weekly_report(db: Database, config: Config) -> str:
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
    counts = db.pipeline_counts()
    changes = db.stage_changes_since(week_ago)

    lines = ["📊 <b>Звіт по воронці</b>", "\n<b>Зараз на етапах</b> (за тиждень: +нових):"]
    for stage in STAGES:
        delta = changes.get(stage, 0)
        if counts[stage] or delta:
            lines.append(f"• {stage}: {counts[stage]}" + (f" (+{delta})" if delta else ""))
    if not any(counts.values()):
        lines.append("• У базі поки немає кандидатів")

    sources = db.source_stats()
    if sources:
        lines.append("\n<b>Джерела</b> (всього → відповіли → інтерв'ю → вийшли):")
        for s in sources:
            reply_rate = f", {100 * s['replied'] // s['total']}% відповідей" if s["total"] else ""
            lines.append(
                f"• {s['source']}: {s['total']} → {s['replied']} → {s['interviewed']} → {s['hired']}{reply_rate}"
            )

    vacancies = db.list_vacancies()
    if vacancies:
        lines.append("\n<b>Відкриті вакансії:</b>")
        lines += [f"• #{v['id']} {v['title']} — {v['candidates']} канд." for v in vacancies]
    return "\n".join(lines)
