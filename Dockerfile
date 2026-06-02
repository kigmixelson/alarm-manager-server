FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY alarm_manager_server ./alarm_manager_server

RUN pip install --upgrade pip && pip install .

EXPOSE 4800

CMD ["alarm-manager-server"]
