# Bookworm (Debian 12): Playwright 1.49 can install its browser dependencies here (not on trixie).
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DB_PATH=/data/finder.sqlite3 \
    P4H_SESSION_PATH=/data/p4h_session.json PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && chmod -R a+rx /ms-playwright
COPY maine_coon_finder ./maine_coon_finder
COPY templates ./templates
RUN useradd -m finder && mkdir -p /data && chown finder /data
USER finder
VOLUME ["/data"]
CMD ["python", "-m", "maine_coon_finder"]
