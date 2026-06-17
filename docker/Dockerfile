# Python API only (статику отдаёт nginx из docker-compose)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir ".[web]"

# Run as an unprivileged user. Pre-create the data dirs and hand them to the
# user so the (initially empty) mounted volumes inherit non-root ownership.
RUN useradd --system --uid 10001 --create-home appuser \
    && mkdir -p /data/tasks /data/db \
    && chown -R appuser:appuser /data
USER appuser

EXPOSE 8000

CMD ["uvicorn", "nwn_translator.web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
