"""Агент-рекрутер: Claude з навичками з .claude/skills та інструментами бази."""

import asyncio
import logging
from datetime import datetime

import anthropic

from .config import Config

log = logging.getLogger(__name__)

# Після стількох повідомлень історія починається заново, щоб запити не дорожчали без меж
MAX_HISTORY_MESSAGES = 120
WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя"]

BOT_INSTRUCTIONS = """\
Ти — особиста асистентка рекрутерки, з якою вона спілкується в Telegram. Ти вмієш усе, \
що описано в навичках нижче, а також працюєш з базою кандидатів, вакансій, інтерв'ю та нагадувань \
через інструменти.

# Як працювати
- Визнач, яка навичка підходить до запиту, і дотримуйся її інструкцій.
- Коли навичка каже переглянути `my-examples/`, `resources/` чи `../../playbooks/`, \
використовуй list_knowledge_files і read_knowledge_file. Шляхи там відносно папки знань: \
`skills/<назва-навички>/my-examples/...`, `playbooks/...`. Картинки з resources/ тобі недоступні.
- Коли рекрутерка просить запам'ятати свій приклад, стиль чи висновки по ролі, збережи їх \
через save_knowledge_file.
- Поточні дату й час бачиш на початку кожного повідомлення у квадратних дужках. \
Відносні дати («завтра о 15») переводь від них.

# База кандидатів
- Коли оцінюєш кандидата чи резюме, наприкінці запропонуй додати його в базу. \
Якщо рекрутерка явно просить, додавай одразу.
- Коли рекрутерка розповідає про зміни («Олена відповіла», «відмовився»), оновлюй етап і нотатки.
- Після повідомлення кандидату пропонуй поставити дату нагадування (next_action_date): \
перше — через 5–7 днів, друге — ще через 7–10 днів.
- Перед створенням інтерв'ю в календарі назви час і дочекайся підтвердження.
- Ніколи не вигадуй дані кандидатів. Посилайся на ID з бази (#12), щоб рекрутерка могла на них відповідати.

# Формат відповідей у Telegram
- Пиши українською, стисло. Telegram підтримує лише **жирний**, _курсив_, `код` і блоки ```.
- Заголовки (#) і таблиці markdown не відображаються. Замість таблиць використовуй списки, \
а великі таблиці (оцінки, рейтинг) клади в блок ```.
- Усе, що рекрутерка копіюватиме (тексти вакансій, повідомлення, булеві запити), клади в окремі блоки ```.
"""


def build_system_prompt(config: Config) -> str:
    parts = [BOT_INSTRUCTIONS]
    if config.profile_file.exists():
        parts.append(
            "# Профіль рекрутерки та загальні правила (CLAUDE.md)\n\n"
            + config.profile_file.read_text(encoding="utf-8")
        )
    parts.append("# Навички")
    for skill_file in sorted((config.knowledge_dir / "skills").glob("*/SKILL.md")):
        name = skill_file.parent.name
        parts.append(f'<skill name="{name}">\n{skill_file.read_text(encoding="utf-8")}\n</skill>')
    return "\n\n".join(parts)


def now_marker(config: Config) -> str:
    now = datetime.now(config.timezone)
    return f"[Зараз: {now:%Y-%m-%d %H:%M}, {WEEKDAYS[now.weekday()]}, {config.timezone}]"


class RecruitingAgent:
    def __init__(self, config: Config, tools: list):
        self.config = config
        self.tools = tools
        self.client = anthropic.AsyncAnthropic()
        self.system = [
            {
                "type": "text",
                "text": build_system_prompt(config),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        self.history: list = []
        self.lock = asyncio.Lock()

    def reset(self) -> None:
        self.history = []

    async def ask(self, content: list[dict]) -> tuple[str, bool]:
        """Надсилає повідомлення рекрутерки агенту. Повертає (відповідь, чи історію скинуто)."""
        async with self.lock:
            was_reset = False
            if len(self.history) > MAX_HISTORY_MESSAGES:
                self.reset()
                was_reset = True

            content = [{"type": "text", "text": now_marker(self.config)}, *content]
            checkpoint = len(self.history)
            self.history.append({"role": "user", "content": content})
            try:
                final = await self._run()
            except Exception:
                # Повертаємо історію до стану перед цим повідомленням, щоб не лишити незакритих викликів інструментів
                del self.history[checkpoint:]
                raise

            if final.stop_reason == "refusal":
                return "Не можу допомогти з цим запитом. Спробуйте сформулювати інакше.", was_reset
            text = "\n\n".join(b.text for b in final.content if b.type == "text").strip()
            if final.stop_reason == "max_tokens":
                text += "\n\n(Відповідь обірвалася через ліміт довжини. Напишіть «продовж».)"
            return text or "Готово.", was_reset

    async def _run(self):
        runner = self.client.beta.messages.tool_runner(
            model=self.config.model,
            max_tokens=16000,
            system=self.system,
            tools=self.tools,
            messages=list(self.history),
            output_config={"effort": self.config.effort},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            max_iterations=30,
        )
        final = None
        async for message in runner:
            final = message
            self.history.append({"role": "assistant", "content": message.content})
            tool_response = await runner.generate_tool_call_response()
            if tool_response is not None:
                self.history.append(tool_response)
        if final is None:
            raise RuntimeError("Claude не повернув відповіді")
        log.info(
            "turn done: stop=%s input=%s cache_read=%s output=%s",
            final.stop_reason,
            final.usage.input_tokens,
            final.usage.cache_read_input_tokens,
            final.usage.output_tokens,
        )
        return final
