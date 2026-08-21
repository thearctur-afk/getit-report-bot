# -*- coding: utf-8 -*-
"""
Точка входа для хостинга на PythonAnywhere.

PythonAnywhere на бесплатном тарифе не позволяет держать процесс запущенным
постоянно (polling), зато позволяет держать обычное веб-приложение — поэтому
бот здесь работает через вебхук: Telegram сам присылает апдейты HTTP-запросом
на наш адрес.

Ежедневные напоминание (18:30) и сводку (19:15) на бесплатном тарифе PythonAnywhere
запускать нечем — там больше нет плановых задач (scheduled tasks) для новых
аккаунтов. Поэтому эти два действия дёргаются СНАРУЖИ, специальным запросом от
GitHub Actions по расписанию (см. .github/workflows/schedule.yml), на два
защищённых секретом адреса ниже.

Как это подключается на сервере — см. инструкцию, раздел про PythonAnywhere.
"""
import asyncio
import logging

from flask import Flask, request, abort

import bot as botmod
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("flask_app")

app = Flask(__name__)


@app.route("/")
def index():
    return "Бот жив. Это техническая страница, сюда заходить не нужно."


@app.route(f"/webhook/{config.WEBHOOK_SECRET}", methods=["POST"])
def telegram_webhook():
    """Сюда Telegram присылает все сообщения и нажатия кнопок."""
    data = request.get_json(force=True, silent=True)
    if data is None:
        abort(400)
    asyncio.run(botmod.process_update(data))
    return "OK", 200


@app.route(f"/trigger/reminder/{config.WEBHOOK_SECRET}", methods=["GET", "POST"])
def trigger_reminder():
    """Дёргается расписанием GitHub Actions в 18:30 МСК, пн-пт."""
    log.info("Внешний триггер: напоминание менеджерам.")
    asyncio.run(_run_and_close(botmod.scheduled_survey_reminder()))
    return "reminder sent", 200


@app.route(f"/trigger/summary/{config.WEBHOOK_SECRET}", methods=["GET", "POST"])
def trigger_summary():
    """Дёргается расписанием GitHub Actions в 19:15 МСК, пн-пт."""
    log.info("Внешний триггер: дневная сводка.")
    asyncio.run(_run_and_close(botmod.scheduled_daily_summary()))
    return "summary sent", 200


async def _run_and_close(coro):
    """Выполнить корутину и закрыть HTTP-сессию бота после неё (см. process_update)."""
    try:
        await coro
    finally:
        await botmod.bot.session.close()


if __name__ == "__main__":
    # Только для локальной проверки: `python flask_app.py`, затем можно
    # прогнать curl на localhost. На PythonAnywhere этот блок не используется —
    # там сервер запускает `app` через WSGI сам.
    app.run(port=8000, debug=True)
