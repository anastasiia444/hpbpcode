"""Розпізнавання мови з голосового повідомлення (локально, безкоштовно, модель Whisper).

Використання: transcribe.py <файл.ogg|.mp3|.m4a|.wav>
Друкує розпізнаний текст. Перший запуск завантажує модель (кілька сотень МБ).
"""

import os
import sys

from faster_whisper import WhisperModel

MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Використання: transcribe.py <аудіофайл>")
    model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    # Мову не фіксуємо: Whisper сам визначить українську, англійську чи іншу
    segments, info = model.transcribe(sys.argv[1], vad_filter=True, beam_size=5)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    print(text or "(тиша або нерозбірливо)")
    print(f"[мова: {info.language}, тривалість: {info.duration:.0f} с]", file=sys.stderr)


if __name__ == "__main__":
    main()
