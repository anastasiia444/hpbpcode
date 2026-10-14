import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from docx import Document

from bot.agent import build_system_prompt
from bot.config import PROJECT_DIR, Config
from bot.db import Database
from bot.files import resume_content_blocks, safe_filename
from bot.reports import daily_digest, weekly_report
from bot.tg_format import TELEGRAM_LIMIT, to_telegram_messages
from bot.tools import ToolContext, build_tools
from zoneinfo import ZoneInfo


@pytest.fixture
def config(tmp_path: Path) -> Config:
    knowledge = tmp_path / "knowledge"
    (knowledge / "skills" / "outreach-writer" / "my-examples").mkdir(parents=True)
    (knowledge / "skills" / "outreach-writer" / "SKILL.md").write_text("Пиши коротко")
    (knowledge / "playbooks").mkdir()
    return Config(
        telegram_token="x",
        allowed_user_id=1,
        model="claude-opus-5-5",
        effort="medium",
        timezone=ZoneInfo("Europe/Kyiv"),
        data_dir=tmp_path / "data",
        knowledge_dir=knowledge,
        profile_file=tmp_path / "CLAUDE.md",
        google_calendar_id=None,
        google_credentials_file=tmp_path / "missing.json",
        daily_digest_hour=9,
        weekly_report_weekday=0,
    )


@pytest.fixture
def db(config: Config) -> Database:
    return Database(config.db_path)


@pytest.fixture
def tools(config, db):
    return {t.name: t for t in build_tools(ToolContext(config, db, None))}


def call(tool, **kwargs):
    return asyncio.run(tool.call(kwargs))


def test_candidate_lifecycle(db):
    vacancy = db.add_vacancy("Middle QA")
    c = db.add_candidate("Олена Коваль", vacancy_id=vacancy["id"], source="Djinni")
    assert c["stage"] == "Знайдено" and c["vacancy_title"] == "Middle QA"
    db.update_candidate(c["id"], stage="Написали", next_action_date="2000-01-01")
    assert db.find_candidates(query="Коваль")[0]["stage"] == "Написали"
    assert [a["id"] for a in db.due_actions("2026-01-01")] == [c["id"]]
    assert db.pipeline_counts()["Написали"] == 1
    with pytest.raises(ValueError):
        db.update_candidate(c["id"], stage="Нема такого")
    csv_text = db.export_candidates_csv().decode("utf-8")
    assert csv_text.startswith("﻿ID,Ім'я") and "Олена Коваль" in csv_text


def test_reminders_pop_once(db):
    db.add_reminder("2000-01-01T00:00:00+00:00", "старе")
    db.add_reminder("2999-01-01T00:00:00+00:00", "майбутнє")
    now = "2026-01-01T00:00:00+00:00"
    assert [r["text"] for r in db.pop_due_reminders(now)] == ["старе"]
    assert db.pop_due_reminders(now) == []


def test_tool_schemas_are_valid(tools):
    for tool in tools.values():
        schema = tool.to_dict()
        assert schema["description"], tool.name
        assert schema["input_schema"]["type"] == "object"


def test_tools_candidate_and_interview(tools, db):
    added = json.loads(call(tools["add_candidate"], name="Ігор", stage="Написали"))
    start = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 15:00")
    interview = json.loads(call(tools["schedule_interview"], candidate_id=added["id"], start=start))
    assert "не підключено" in interview["calendar"]
    assert db.get_candidate(added["id"])["stage"] == "Інтерв'ю"
    assert len(db.pending_reminders()) == 1  # нагадування за 30 хвилин
    day = start[:10]
    assert json.loads(call(tools["list_interviews"], date_from=day, date_to=day))[0]["candidate_name"] == "Ігор"


def test_tools_reject_bad_input(tools):
    with pytest.raises(ValueError):
        asyncio.run(tools["add_reminder"].call({"when": "завтра", "text": "x"}))
    with pytest.raises(ValueError):
        asyncio.run(tools["check_free_time"].call({"date_from": "2026-01-01", "date_to": "2026-01-02"}))


def test_knowledge_files_sandbox(tools, config):
    ok = call(tools["save_knowledge_file"], path="playbooks/qa.md", content="Djinni працює")
    assert "playbooks/qa.md" in ok
    call(tools["save_knowledge_file"], path="skills/outreach-writer/my-examples/tone.md", content="на ви")
    assert call(tools["read_knowledge_file"], path="playbooks/qa.md") == "Djinni працює"
    assert "playbooks/qa.md" in json.loads(call(tools["list_knowledge_files"]))
    for bad in ["../outside.md", "skills/outreach-writer/SKILL.md", "playbooks/x.txt", "skills/new/my-examples/a.md"]:
        with pytest.raises(ValueError):
            asyncio.run(tools["save_knowledge_file"].call({"path": bad, "content": "x"}))
    with pytest.raises(ValueError):
        asyncio.run(tools["read_knowledge_file"].call({"path": "../../etc/passwd"}))


def test_telegram_formatting():
    text = "# Заголовок\n**жирне** і `код` <тег>\n```\n| a | b |\n```\n[посилання](https://djinni.co)"
    [message] = to_telegram_messages(text)
    assert "<b>Заголовок</b>" in message and "<b>жирне</b>" in message
    assert "<code>код</code>" in message and "&lt;тег&gt;" in message
    assert "<pre>| a | b |</pre>" in message
    assert '<a href="https://djinni.co">посилання</a>' in message
    long = "```\n" + "рядок\n" * 2000 + "```"
    parts = to_telegram_messages(long)
    assert len(parts) > 1 and all(len(p) <= TELEGRAM_LIMIT + 50 for p in parts)
    assert all(p.startswith("<pre>") and p.endswith("</pre>") for p in parts)


def test_reports(db, config):
    assert daily_digest(db, config) is None
    c = db.add_candidate("Марта", source="LinkedIn", stage="Відповів")
    db.update_candidate(c["id"], next_action="подзвонити", next_action_date="2000-01-01")
    assert "прострочено" in daily_digest(db, config)
    report = weekly_report(db, config)
    assert "Відповів: 1" in report and "LinkedIn: 1 → 1" in report


def test_resume_reading(tmp_path):
    document = Document()
    document.add_paragraph("Олена, QA Engineer, 4 роки")
    path = tmp_path / safe_filename("Резюме Олени.docx")
    document.save(path)
    [block] = resume_content_blocks(path)
    assert "QA Engineer" in block["text"] and path.suffix == ".docx"
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    assert resume_content_blocks(pdf)[0]["type"] == "document"


def test_system_prompt_includes_real_skills(config):
    real = Config(**{**config.__dict__, "knowledge_dir": PROJECT_DIR / ".claude", "profile_file": PROJECT_DIR / "CLAUDE.md"})
    prompt = build_system_prompt(real)
    for skill in ["job-requirement-analysis", "candidate-icp-builder", "job-seller",
                  "sourcing-strategy-generator", "candidate-screener", "outreach-writer"]:
        assert f'<skill name="{skill}">' in prompt
    assert "Про мене" in prompt
