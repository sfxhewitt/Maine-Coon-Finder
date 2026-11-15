FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DB_PATH=/data/finder.sqlite3
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY maine_coon_finder ./maine_coon_finder
COPY templates ./templates
RUN useradd -m finder && mkdir -p /data && chown finder /data
USER finder
VOLUME ["/data"]
CMD ["python", "-m", "maine_coon_finder"]
