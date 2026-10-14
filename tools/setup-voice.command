#!/bin/zsh
# Встановлює розпізнавання голосових повідомлень (Whisper, локально й безкоштовно).
# Двічі клікніть цей файл у Finder. Займе кілька хвилин і ~2 ГБ місця.
set -e
VENV="$HOME/.local/share/recruiting-voice/venv"
echo "Встановлюю розпізнавання мови…"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet faster-whisper
echo "Завантажую модель (перший раз — кілька хвилин)…"
"$VENV/bin/python" -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')"
echo "Готово! Тепер агент розумітиме голосові повідомлення."
read -k1 "?Натисніть будь-яку клавішу, щоб закрити"
