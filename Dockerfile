FROM python:3.12-slim

# OpenCV-Laufzeitbibliotheken (headless-fähig, keine GUI nötig)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies zuerst (Layer-Cache)
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

COPY app ./app

ENV PYTHONUNBUFFERED=1
# Im Container ist ein Passwort Pflicht (App startet sonst nicht)
ENV APP_REQUIRE_AUTH=1
EXPOSE 8765
# Persistente Daten (DB, Scans, Stapel-PDFs) liegen im Volume
VOLUME ["/app/data"]

CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
