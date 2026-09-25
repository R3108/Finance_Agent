# Ledgerly API. SQLite and uploaded receipts live under /app/data — mount a volume there.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
RUN useradd --uid 10001 --create-home ledgerly && mkdir -p /app/data && chown -R ledgerly /app/data
USER ledgerly

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=4).status == 200 else 1)"

# One worker: the background jobs and the in-process rate limiter assume a single process
# (see README → Roadmap). Scale by moving jobs to a worker and limits to Redis first.
# No --proxy-headers: the app reads X-Forwarded-For itself, trusting only TRUSTED_PROXY_HOPS entries.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
