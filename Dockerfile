FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App
COPY . .

# Writable dirs for runtime data
RUN mkdir -p tle_cache && chmod 777 tle_cache

EXPOSE 5001

ENV PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    ASTRUM_LIVE_TLE=1

CMD ["python", "app.py"]
