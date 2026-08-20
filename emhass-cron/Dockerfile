FROM python:3.12-alpine

RUN apk add --no-cache supercronic docker-cli

WORKDIR /app
COPY lib/weather_cache_stale.py ./
COPY emhass-cron/fetch_prices_emhass.py emhass-cron/planned_profit.py emhass-cron/crontab emhass-cron/entrypoint.sh ./
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
