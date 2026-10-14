"""Перетворення markdown-відповіді Claude у HTML, який розуміє Telegram, з поділом на повідомлення."""

import html
import re

TELEGRAM_LIMIT = 4000  # з запасом від ліміту 4096

FENCE = re.compile(r"```[^\n]*\n(.*?)(?:```|\Z)", re.DOTALL)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*")
HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _inline(text: str) -> str:
    codes: list[str] = []

    def keep_code(match: re.Match) -> str:
        codes.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = INLINE_CODE.sub(keep_code, text)
    text = html.escape(text, quote=False)
    text = HEADING.sub(r"<b>\1</b>", text)
    text = BOLD.sub(r"<b>\1</b>", text)
    text = LINK.sub(lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def _split_lines(text: str, limit: int) -> list[str]:
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            chunks.append(current + line[: limit - len(current)])
            line = line[limit - len(current):]
            current = ""
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


def _segments(markdown: str) -> list[str]:
    """Ділить текст на HTML-шматки, кожен з яких можна надіслати окремо."""
    segments, position = [], 0
    for match in FENCE.finditer(markdown):
        before = markdown[position:match.start()].strip("\n")
        if before:
            segments += [_inline(p) for p in _split_lines(before, TELEGRAM_LIMIT // 2)]
        code = match.group(1).rstrip("\n")
        segments += [f"<pre>{html.escape(p)}</pre>" for p in _split_lines(code, TELEGRAM_LIMIT - 20)]
        position = match.end()
    rest = markdown[position:].strip("\n")
    if rest:
        segments += [_inline(p) for p in _split_lines(rest, TELEGRAM_LIMIT // 2)]
    return segments


def to_telegram_messages(markdown: str) -> list[str]:
    messages, current = [], ""
    for segment in _segments(markdown):
        if current and len(current) + len(segment) + 2 > TELEGRAM_LIMIT:
            messages.append(current)
            current = ""
        current = f"{current}\n\n{segment}" if current else segment
    if current:
        messages.append(current)
    return messages or ["Готово."]


def plain_chunks(text: str) -> list[str]:
    return _split_lines(text, TELEGRAM_LIMIT) or ["Готово."]
