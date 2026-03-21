# Python API only (статику отдаёт nginx из docker-compose)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir ".[web]"

EXPOSE 8000

CMD ["uvicorn", "nwn_translator.web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
