# -*- coding: utf-8 -*-
"""
Разовый скрипт: сообщает Telegram, куда слать апдейты (webhook).

Запускается вручную ОДИН РАЗ (и потом — заново, если поменяли домен или
секрет) из Bash-консоли на PythonAnywhere:

    python3 set_webhook.py

Перед запуском убедитесь, что веб-приложение на PythonAnywhere уже создано
и работает (иначе Telegram не сможет достучаться до адреса).
"""
import asyncio
import os

import config

# Домен вашего приложения на PythonAnywhere, например: myusername.pythonanywhere.com
# Можно задать через переменную окружения PA_DOMAIN в .env, либо вписать прямо тут.
PA_DOMAIN = os.getenv("PA_DOMAIN", "")


async def main():
    if not PA_DOMAIN:
        print("Не задан домен! Впишите PA_DOMAIN в .env (например myusername.pythonanywhere.com)")
        return

    import bot as botmod

    url = f"https://{PA_DOMAIN}/webhook/{config.WEBHOOK_SECRET}"
    ok = await botmod.bot.set_webhook(url, drop_pending_updates=True)
    info = await botmod.bot.get_webhook_info()
    await botmod.bot.session.close()

    print("Установлен:" if ok else "Не удалось установить:", url)
    print("Текущий webhook по данным Telegram:", info.url)
    if info.last_error_message:
        print("Последняя ошибка Telegram:", info.last_error_message)


if __name__ == "__main__":
    asyncio.run(main())
