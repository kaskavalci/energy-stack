FROM python:3.12-alpine

RUN apk add --no-cache supercronic docker-cli

WORKDIR /app
COPY lib/weather_cache_stale.py ./
COPY monitor/health_notify.py monitor/crontab ./
RUN chmod +x /app/health_notify.py

ENTRYPOINT ["supercronic", "-passthrough-logs", "/app/crontab"]
