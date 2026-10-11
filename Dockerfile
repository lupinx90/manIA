FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY db.py main.py schemas.py ./
COPY routers ./routers
COPY templates ./templates

RUN mkdir -p /data \
    && useradd --system --uid 10001 --create-home app \
    && chown app:app /data

ENV DB_FILE=/data/system_hub.db
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/api/state', timeout=3)"]

USER app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
