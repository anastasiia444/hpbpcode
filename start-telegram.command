#!/bin/zsh
# Запуск агента в Telegram: двічі клікніть цей файл у Finder.
# Агент працює, поки відкрите це вікно Терміналу. Mac не засинатиме, доки воно відкрите.

cd "$(dirname "$0")"
export PATH="$HOME/.bun/bin:$HOME/.local/bin:$PATH"

CLAUDE_BIN=$(command -v claude)
if [ -z "$CLAUDE_BIN" ]; then
  # Claude Code, вбудований у розширення для VS Code
  CLAUDE_BIN=$(ls -t ~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | head -1)
fi
if [ -z "$CLAUDE_BIN" ]; then
  echo "Не знайдено Claude Code. Встановіть його: https://claude.com/claude-code"
  read -k1 "?Натисніть будь-яку клавішу, щоб закрити"
  exit 1
fi
if ! command -v bun >/dev/null; then
  echo "Не знайдено Bun. Встановіть його командою: curl -fsSL https://bun.sh/install | bash"
  read -k1 "?Натисніть будь-яку клавішу, щоб закрити"
  exit 1
fi

echo "Запускаю агента для Telegram… Не закривайте це вікно."
caffeinate -is "$CLAUDE_BIN" --channels plugin:telegram@claude-plugins-official --permission-mode acceptEdits
