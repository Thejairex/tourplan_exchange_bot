FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy

WORKDIR /app

ENV TZ=America/Argentina/Buenos_Aires
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron tzdata \
    && ln -fs /usr/share/zoneinfo/$TZ /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# El cron corre a las 9am hora local (TZ definida arriba) y manda stdout/stderr
# al log de Docker (docker compose logs).
RUN echo "0 8 * * * root cd /app && $(which python3) /app/main.py >> /proc/1/fd/1 2>> /proc/1/fd/2" \
    > /etc/cron.d/tourplan-fx-bot \
    && chmod 0644 /etc/cron.d/tourplan-fx-bot \
    && crontab /etc/cron.d/tourplan-fx-bot

CMD ["cron", "-f"]
