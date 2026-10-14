FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot ./bot
COPY .claude ./.claude
COPY CLAUDE.md .

RUN useradd --create-home --uid 1000 recruiter && mkdir -p /app/data && chown -R recruiter /app
USER recruiter

CMD ["python", "-m", "bot.main"]
