#!/bin/zsh
# Розпізнає голосове повідомлення: tools/transcribe.sh <аудіофайл>
# Python-оточення встановлюється один раз: tools/setup-voice.command
VENV="$HOME/.local/share/recruiting-voice/venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "Розпізнавання мови не встановлено. Запустіть tools/setup-voice.command" >&2
  exit 1
fi
exec "$VENV/bin/python" "$(dirname "$0")/transcribe.py" "$@" 2>/dev/null
