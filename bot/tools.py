"""Інструменти, які агент викликає сам: база кандидатів, інтерв'ю, нагадування, файли знань."""

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from anthropic import beta_async_tool

from .config import Config
from .db import STAGES, Database
from .gcal import GoogleCalendar

# Куди агенту дозволено записувати файли знань (відносно .claude/)
WRITABLE_PREFIXES = ("playbooks/",)
WRITABLE_PATTERN = "skills/*/my-examples/*"


@dataclass
class ToolContext:
    config: Config
    db: Database
    calendar: GoogleCalendar | None


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, default=str)


def parse_local_datetime(value: str, config: Config) -> datetime:
    try:
        naive = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError(f"Дата «{value}» має бути у форматі РРРР-ММ-ДД ГГ:ХХ") from None
    return naive.replace(tzinfo=config.timezone)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"Дата «{value}» має бути у форматі РРРР-ММ-ДД") from None


def _knowledge_path(config: Config, relative: str) -> Path:
    root = config.knowledge_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Шлях має бути всередині папки знань")
    return path


def build_tools(ctx: ToolContext) -> list:
    config, db = ctx.config, ctx.db

    # --- Вакансії ---

    @beta_async_tool
    async def add_vacancy(title: str, description: str | None = None) -> str:
        """Додати вакансію в базу. Викликай, коли рекрутерка починає роботу над новою вакансією.

        Args:
            title: Назва позиції, наприклад «Middle QA Engineer, FinFlow».
            description: Опис вакансії або короткий бриф.
        """
        return _json(db.add_vacancy(title, description))

    @beta_async_tool
    async def list_vacancies(include_closed: bool = False) -> str:
        """Список вакансій з кількістю кандидатів.

        Args:
            include_closed: Показати також закриті вакансії.
        """
        return _json(db.list_vacancies(include_closed))

    @beta_async_tool
    async def set_vacancy_status(vacancy_id: int, status: str) -> str:
        """Змінити статус вакансії: «відкрита», «на паузі» або «закрита».

        Args:
            vacancy_id: ID вакансії.
            status: Новий статус.
        """
        if status not in ("відкрита", "на паузі", "закрита"):
            raise ValueError("Статус має бути «відкрита», «на паузі» або «закрита»")
        return _json(db.set_vacancy_status(vacancy_id, status))

    # --- Кандидати ---

    @beta_async_tool
    async def add_candidate(
        name: str,
        vacancy_id: int | None = None,
        stage: str = "Знайдено",
        source: str | None = None,
        contact: str | None = None,
        profile_url: str | None = None,
        fit_score: int | None = None,
        summary: str | None = None,
        notes: str | None = None,
        next_action: str | None = None,
        next_action_date: str | None = None,
        resume_file: str | None = None,
    ) -> str:
        """Додати кандидата в базу. Перед додаванням перевір через find_candidates, чи його там ще немає.

        Args:
            name: Ім'я та прізвище (для анонімних профілів Djinni — короткий опис, наприклад «Djinni: Senior QA, 6 років»).
            vacancy_id: ID вакансії, на яку розглядаємо.
            stage: Етап воронки: Знайдено, Написали, Відповів, Скринінг, Інтерв'ю, Офер, Вийшов, Відмова.
            source: Звідки кандидат: LinkedIn, Djinni, DOU, Work.ua, Telegram, рекомендація тощо.
            contact: Email, телефон чи нік у Telegram.
            profile_url: Посилання на профіль.
            fit_score: Відповідність профілю ідеального кандидата у відсотках (0–100), якщо вже оцінювали.
            summary: 1–3 речення про кандидата.
            notes: Довільні нотатки.
            next_action: Що зробити далі, наприклад «надіслати нагадування».
            next_action_date: Коли, у форматі РРРР-ММ-ДД. Бот нагадає зранку цього дня.
            resume_file: Назва файлу резюме, яку бот повідомив при завантаженні.
        """
        if next_action_date:
            parse_date(next_action_date)
        if resume_file:
            _resume_path(resume_file)
        candidate = db.add_candidate(
            name,
            stage=stage,
            vacancy_id=vacancy_id,
            source=source,
            contact=contact,
            profile_url=profile_url,
            fit_score=fit_score,
            summary=summary,
            notes=notes,
            next_action=next_action,
            next_action_date=next_action_date,
            resume_path=resume_file,
        )
        return _json(candidate)

    @beta_async_tool
    async def update_candidate(
        candidate_id: int,
        stage: str | None = None,
        vacancy_id: int | None = None,
        source: str | None = None,
        contact: str | None = None,
        profile_url: str | None = None,
        fit_score: int | None = None,
        summary: str | None = None,
        notes: str | None = None,
        next_action: str | None = None,
        next_action_date: str | None = None,
        resume_file: str | None = None,
    ) -> str:
        """Оновити дані кандидата. Передавай лише поля, що змінюються. Нотатки замінюються повністю, тож щоб доповнити, передай старий текст разом з новим.

        Args:
            candidate_id: ID кандидата.
            stage: Новий етап: Знайдено, Написали, Відповів, Скринінг, Інтерв'ю, Офер, Вийшов, Відмова.
            vacancy_id: ID вакансії.
            source: Джерело.
            contact: Контакт.
            profile_url: Посилання на профіль.
            fit_score: Відповідність у відсотках (0–100).
            summary: Короткий опис.
            notes: Нотатки (повний новий текст).
            next_action: Наступна дія.
            next_action_date: Дата наступної дії, РРРР-ММ-ДД.
            resume_file: Назва файлу резюме.
        """
        if next_action_date:
            parse_date(next_action_date)
        if resume_file:
            _resume_path(resume_file)
        candidate = db.update_candidate(
            candidate_id,
            stage=stage,
            vacancy_id=vacancy_id,
            source=source,
            contact=contact,
            profile_url=profile_url,
            fit_score=fit_score,
            summary=summary,
            notes=notes,
            next_action=next_action,
            next_action_date=next_action_date,
            resume_path=resume_file,
        )
        return _json(candidate)

    @beta_async_tool
    async def find_candidates(
        query: str | None = None,
        vacancy_id: int | None = None,
        stage: str | None = None,
    ) -> str:
        """Знайти кандидатів за ім'ям чи словом в описі, вакансією або етапом. Без параметрів повертає останніх 30.

        Args:
            query: Частина імені, контакту, джерела або тексту в описі чи нотатках.
            vacancy_id: ID вакансії.
            stage: Етап воронки.
        """
        return _json(db.find_candidates(query, vacancy_id, stage))

    @beta_async_tool
    async def get_candidate(candidate_id: int) -> str:
        """Повна картка кандидата разом з його інтерв'ю.

        Args:
            candidate_id: ID кандидата.
        """
        candidate = db.get_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"Кандидата #{candidate_id} не знайдено")
        candidate["interviews"] = db._all(
            "SELECT * FROM interviews WHERE candidate_id = ? ORDER BY starts_at", (candidate_id,)
        )
        return _json(candidate)

    def _resume_path(filename: str) -> Path:
        path = (config.resumes_dir / filename).resolve()
        if not path.is_relative_to(config.resumes_dir.resolve()) or not path.exists():
            raise ValueError(f"Файл резюме «{filename}» не знайдено")
        return path

    # --- Нагадування та звіти ---

    @beta_async_tool
    async def add_reminder(when: str, text: str) -> str:
        """Поставити нагадування: бот надішле повідомлення в Telegram у вказаний час.

        Args:
            when: Локальний час у форматі РРРР-ММ-ДД ГГ:ХХ.
            text: Текст нагадування.
        """
        moment = parse_local_datetime(when, config)
        if moment < datetime.now(config.timezone):
            raise ValueError("Цей час уже минув")
        utc = moment.astimezone(timezone.utc).isoformat(timespec="seconds")
        return _json(db.add_reminder(utc, text) | {"local_time": when})

    @beta_async_tool
    async def pipeline_report(vacancy_id: int | None = None) -> str:
        """Звіт по воронці: скільки кандидатів на кожному етапі, рух за 7 днів, ефективність джерел, найближчі інтерв'ю.

        Args:
            vacancy_id: Обмежити звіт однією вакансією.
        """
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
        now_local = datetime.now(config.timezone)
        return _json(
            {
                "stages": db.pipeline_counts(vacancy_id),
                "stage_changes_last_7_days": db.stage_changes_since(week_ago),
                "sources": db.source_stats(),
                "due_actions": db.due_actions(now_local.date().isoformat()),
                "upcoming_interviews": db.list_interviews(
                    now_local.isoformat(), (now_local + timedelta(days=7)).isoformat()
                ),
            }
        )

    # --- Інтерв'ю та календар ---

    @beta_async_tool
    async def check_free_time(date_from: str, date_to: str) -> str:
        """Показати зайняті проміжки в Google Calendar рекрутерки, щоб запропонувати кандидату вільний час.

        Args:
            date_from: Початкова дата, РРРР-ММ-ДД.
            date_to: Кінцева дата включно, РРРР-ММ-ДД.
        """
        if ctx.calendar is None:
            raise ValueError("Google Calendar не підключено. Запитай рекрутерку про її вільний час")
        start = datetime.combine(parse_date(date_from), datetime.min.time(), config.timezone)
        end = datetime.combine(parse_date(date_to) + timedelta(days=1), datetime.min.time(), config.timezone)
        busy = await ctx.calendar.busy_slots(start, end)
        local = [
            {
                "start": datetime.fromisoformat(b["start"]).astimezone(config.timezone).strftime("%Y-%m-%d %H:%M"),
                "end": datetime.fromisoformat(b["end"]).astimezone(config.timezone).strftime("%Y-%m-%d %H:%M"),
            }
            for b in busy
        ]
        return _json({"timezone": str(config.timezone), "busy": local})

    @beta_async_tool
    async def schedule_interview(
        candidate_id: int,
        start: str,
        duration_min: int = 60,
        kind: str = "Інтерв'ю",
        notes: str | None = None,
    ) -> str:
        """Запланувати інтерв'ю: створює подію в Google Calendar (якщо підключено), зберігає в базі, переводить кандидата на етап «Інтерв'ю» і ставить нагадування за 30 хвилин. Перед викликом переконайся, що рекрутерка підтвердила час.

        Args:
            candidate_id: ID кандидата.
            start: Локальний час початку, РРРР-ММ-ДД ГГ:ХХ.
            duration_min: Тривалість у хвилинах.
            kind: Тип зустрічі: скринінг, технічне інтерв'ю, фінальне тощо.
            notes: Що додати в опис події (посилання на дзвінок, теми).
        """
        candidate = db.get_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"Кандидата #{candidate_id} не знайдено")
        begin = parse_local_datetime(start, config)
        end = begin + timedelta(minutes=duration_min)

        event_id, event_link = None, None
        if ctx.calendar is not None:
            description = "\n".join(
                filter(
                    None,
                    [
                        f"Кандидат: {candidate['name']}",
                        f"Вакансія: {candidate['vacancy_title']}" if candidate["vacancy_title"] else None,
                        f"Контакт: {candidate['contact']}" if candidate["contact"] else None,
                        f"Профіль: {candidate['profile_url']}" if candidate["profile_url"] else None,
                        notes,
                    ],
                )
            )
            event = await ctx.calendar.create_event(
                f"{kind}: {candidate['name']}", begin, end, description
            )
            event_id, event_link = event.get("id"), event.get("htmlLink")

        interview = db.add_interview(candidate_id, begin.isoformat(), duration_min, kind, event_id)
        if STAGES.index(candidate["stage"]) < STAGES.index("Інтерв'ю"):
            db.update_candidate(candidate_id, stage="Інтерв'ю")
        remind_at = begin - timedelta(minutes=30)
        if remind_at > datetime.now(config.timezone):
            db.add_reminder(
                remind_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                f"Через 30 хвилин: {kind} з {candidate['name']} ({begin:%H:%M})",
            )
        return _json(
            interview
            | {
                "calendar_event_link": event_link,
                "calendar": "створено подію" if event_id else "Google Calendar не підключено, збережено лише в базі",
            }
        )

    @beta_async_tool
    async def list_interviews(date_from: str, date_to: str) -> str:
        """Інтерв'ю з бази за період.

        Args:
            date_from: Початкова дата, РРРР-ММ-ДД.
            date_to: Кінцева дата включно, РРРР-ММ-ДД.
        """
        start = datetime.combine(parse_date(date_from), datetime.min.time(), config.timezone)
        end = datetime.combine(parse_date(date_to) + timedelta(days=1), datetime.min.time(), config.timezone)
        return _json(db.list_interviews(start.isoformat(), end.isoformat()))

    @beta_async_tool
    async def save_interview_feedback(interview_id: int, feedback: str) -> str:
        """Зберегти підсумок інтерв'ю: враження, сильні сторони, ризики, рішення.

        Args:
            interview_id: ID інтерв'ю.
            feedback: Структурований підсумок.
        """
        return _json(db.save_interview_feedback(interview_id, feedback))

    # --- Файли знань (.claude/) ---

    @beta_async_tool
    async def list_knowledge_files() -> str:
        """Список файлів знань рекрутерки: playbooks, її приклади в my-examples, шаблони."""
        root = config.knowledge_dir
        files = sorted(
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_file() and p.suffix in (".md", ".txt") and p.name != "SKILL.md"
        )
        return _json(files)

    @beta_async_tool
    async def read_knowledge_file(path: str) -> str:
        """Прочитати файл знань, наприклад «playbooks/qa-engineer.md» чи «skills/outreach-writer/my-examples/tone.md».

        Args:
            path: Шлях відносно папки знань, як у list_knowledge_files.
        """
        file = _knowledge_path(config, path)
        if not file.is_file():
            raise ValueError(f"Файл «{path}» не знайдено")
        return file.read_text(encoding="utf-8")

    @beta_async_tool
    async def save_knowledge_file(path: str, content: str) -> str:
        """Зберегти або перезаписати файл знань: playbook ролі («playbooks/<роль>.md») чи приклад стилю («skills/<навичка>/my-examples/<назва>.md»). Викликай лише коли рекрутерка просить запам'ятати приклад чи висновки. Якщо файл існує, спершу прочитай його і збережи старий вміст разом з новим.

        Args:
            path: Шлях відносно папки знань, закінчується на .md.
            content: Повний вміст файлу.
        """
        file = _knowledge_path(config, path)
        relative = file.relative_to(config.knowledge_dir.resolve())
        in_examples = relative.match(WRITABLE_PATTERN) and file.parent.parent.is_dir()
        allowed = str(relative).startswith(WRITABLE_PREFIXES) or in_examples
        if not allowed or file.suffix != ".md":
            raise ValueError("Зберігати можна лише .md у playbooks/ або skills/<навичка>/my-examples/")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
        return f"Збережено: {relative}"

    return [
        add_vacancy,
        list_vacancies,
        set_vacancy_status,
        add_candidate,
        update_candidate,
        find_candidates,
        get_candidate,
        add_reminder,
        pipeline_report,
        check_free_time,
        schedule_interview,
        list_interviews,
        save_interview_feedback,
        list_knowledge_files,
        read_knowledge_file,
        save_knowledge_file,
    ]
