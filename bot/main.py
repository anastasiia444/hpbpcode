"""Telegram-бот рекрутингового агента."""

import asyncio
import contextlib
import html
import logging
from datetime import datetime, time, timezone
from functools import wraps

import anthropic
from dotenv import load_dotenv
from telegram import InputFile, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .agent import RecruitingAgent
from .config import Config, load_config
from .db import Database
from .files import MAX_FILE_BYTES, SUPPORTED, resume_content_blocks, safe_filename
from .gcal import GoogleCalendar
from .reports import daily_digest, weekly_report
from .tg_format import plain_chunks, to_telegram_messages
from .tools import ToolContext, build_tools

log = logging.getLogger(__name__)

HELP = """\
Я ваша асистентка з рекрутингу. Пишіть звичайними словами, наприклад:

• «Розбери вакансію: …» — аналіз вакансії
• «Склади профіль ідеального кандидата» — таблиця оцінки
• «Перепиши вакансію для Djinni»
• «Де шукати кандидатів?» — пошукові запити й канали
• Надішліть резюме (PDF, DOCX) — я його оціню
• «Напиши повідомлення кандидату #12»
• «Додай Олену в базу, етап — Написали»
• «Заплануй інтерв'ю з #12 на завтра о 15:00»
• «Нагадай у п'ятницю о 10:00 подзвонити замовнику»

Команди:
/today — план на сьогодні
/report — звіт по воронці
/export — база кандидатів у CSV для Excel
/new — почати нову розмову (база не зникне)
"""


def only_owner(handler):
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        config: Config = context.application.bot_data["config"]
        user = update.effective_user
        if user is None or update.effective_message is None:
            return
        if config.allowed_user_id is None:
            await update.effective_message.reply_text(
                f"Ваш Telegram ID: {user.id}\n"
                "Впишіть його в ALLOWED_TELEGRAM_USER_ID у файлі .env і перезапустіть бота."
            )
            return
        if user.id != config.allowed_user_id:
            await update.effective_message.reply_text("Це приватний бот.")
            return
        return await handler(update, context)

    return wrapper


@contextlib.asynccontextmanager
async def typing(chat):
    async def keep_typing():
        while True:
            with contextlib.suppress(Exception):
                await chat.send_action(ChatAction.TYPING)
            await asyncio.sleep(4)

    task = asyncio.create_task(keep_typing())
    try:
        yield
    finally:
        task.cancel()


async def send_markdown(chat, text: str) -> None:
    for chunk in to_telegram_messages(text):
        try:
            await chat.send_message(chunk, parse_mode=ParseMode.HTML)
        except BadRequest:
            log.warning("HTML не пройшов, надсилаю як звичайний текст")
            for plain in plain_chunks(text):
                await chat.send_message(plain)
            return


async def talk_to_agent(update: Update, context: ContextTypes.DEFAULT_TYPE, content: list[dict]):
    agent: RecruitingAgent = context.application.bot_data["agent"]
    chat = update.effective_chat
    try:
        async with typing(chat):
            reply, was_reset = await agent.ask(content)
    except anthropic.RateLimitError:
        await chat.send_message("Забагато запитів до Claude. Спробуйте за хвилину.")
        return
    except anthropic.AuthenticationError:
        await chat.send_message("Ключ Claude API недійсний. Перевірте ANTHROPIC_API_KEY у .env.")
        return
    except anthropic.APIStatusError as error:
        log.exception("Claude API error")
        await chat.send_message(f"Помилка Claude API ({error.status_code}). Спробуйте ще раз.")
        return
    except anthropic.APIConnectionError:
        await chat.send_message("Не вдалося з'єднатися з Claude. Спробуйте ще раз.")
        return
    if was_reset:
        await chat.send_message("Розмова стала задовгою, тож я почала нову. База й файли на місці.")
    await send_markdown(chat, reply)


# --- Обробники ---


@only_owner
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP)


@only_owner
async def new_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.application.bot_data["agent"].reset()
    await update.effective_message.reply_text("Почала нову розмову. База кандидатів і файли збережені.")


@only_owner
async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.application.bot_data
    text = daily_digest(data["db"], data["config"]) or "На сьогодні немає інтерв'ю і запланованих кроків."
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@only_owner
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.application.bot_data
    await update.effective_message.reply_text(weekly_report(data["db"], data["config"]), parse_mode=ParseMode.HTML)


@only_owner
async def export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.application.bot_data["db"]
    filename = f"candidates-{datetime.now():%Y-%m-%d}.csv"
    await update.effective_message.reply_document(
        InputFile(db.export_candidates_csv(), filename=filename),
        caption="База кандидатів. Відкривається в Excel чи Google Sheets.",
    )


@only_owner
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await talk_to_agent(update, context, [{"type": "text", "text": update.effective_message.text}])


@only_owner
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    document = message.document
    original = document.file_name or "resume"
    suffix = "." + original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if suffix not in SUPPORTED:
        await message.reply_text("Я читаю резюме у форматах PDF, DOCX, TXT і MD.")
        return
    if document.file_size and document.file_size > MAX_FILE_BYTES:
        await message.reply_text("Файл більший за 20 МБ, Telegram не дає боту його завантажити.")
        return

    config.resumes_dir.mkdir(parents=True, exist_ok=True)
    path = config.resumes_dir / safe_filename(original)
    telegram_file = await document.get_file()
    await telegram_file.download_to_drive(path)
    try:
        blocks = resume_content_blocks(path)
    except Exception as error:
        await message.reply_text(f"Не вдалося прочитати файл: {error}")
        return

    request = message.caption or (
        "Оціни це резюме. Якщо в розмові є профіль ідеального кандидата, оцінюй за ним; "
        "якщо ні, запитай, на яку вакансію розглядаємо."
    )
    note = (
        f"(Файл збережено як «{path.name}». Щоб прив'язати його до кандидата в базі, "
        f"передай resume_file=«{path.name}».)"
    )
    await talk_to_agent(update, context, [*blocks, {"type": "text", "text": f"{request}\n\n{note}"}])


@only_owner
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Надішліть резюме файлом (PDF чи DOCX), а не фото.")


# --- Розклад ---


async def send_due_reminders(context: ContextTypes.DEFAULT_TYPE):
    data = context.application.bot_data
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for reminder in data["db"].pop_due_reminders(now):
        await context.bot.send_message(
            data["config"].allowed_user_id, f"⏰ {html.escape(reminder['text'])}", parse_mode=ParseMode.HTML
        )


async def send_daily_digest(context: ContextTypes.DEFAULT_TYPE):
    data = context.application.bot_data
    text = daily_digest(data["db"], data["config"])
    if text:
        await context.bot.send_message(data["config"].allowed_user_id, text, parse_mode=ParseMode.HTML)


async def send_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    data = context.application.bot_data
    await context.bot.send_message(
        data["config"].allowed_user_id, weekly_report(data["db"], data["config"]), parse_mode=ParseMode.HTML
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Помилка обробки оновлення", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        with contextlib.suppress(Exception):
            await update.effective_chat.send_message("Щось пішло не так. Спробуйте ще раз.")


def build_application(config: Config) -> Application:
    db = Database(config.db_path)
    calendar = None
    if config.calendar_enabled:
        calendar = GoogleCalendar(config.google_credentials_file, config.google_calendar_id, str(config.timezone))
        log.info("Google Calendar підключено: %s", config.google_calendar_id)
    else:
        log.info("Google Calendar не підключено")
    agent = RecruitingAgent(config, build_tools(ToolContext(config, db, calendar)))

    application = Application.builder().token(config.telegram_token).concurrent_updates(True).build()
    application.bot_data.update(config=config, db=db, agent=agent)

    application.add_handler(CommandHandler(["start", "help"], start))
    application.add_handler(CommandHandler("new", new_conversation))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("report", report))
    application.add_handler(CommandHandler("export", export))
    application.add_handler(MessageHandler(filters.Document.ALL, on_document))
    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)

    if config.allowed_user_id is not None:
        jobs = application.job_queue
        digest_time = time(hour=config.daily_digest_hour, tzinfo=config.timezone)
        jobs.run_repeating(send_due_reminders, interval=60, first=10)
        # У python-telegram-bot дні тижня рахуються з неділі: 0 = неділя, 1 = понеділок
        jobs.run_daily(send_daily_digest, digest_time, days=(1, 2, 3, 4, 5))
        report_time = time(hour=config.daily_digest_hour, minute=5, tzinfo=config.timezone)
        jobs.run_daily(send_weekly_report, report_time, days=((config.weekly_report_weekday + 1) % 7,))
    return application


def main() -> None:
    load_dotenv()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = load_config()
    build_application(config).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
