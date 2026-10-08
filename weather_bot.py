import asyncio
import os
import requests
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # ← Важно: токен берём из переменных окружения Render

# --- НОВЫЕ НАСТРОЙКИ ДЛЯ WEBHOOKS ---
# Render автоматически подставит свой домен в переменную RENDER_EXTERNAL_URL
WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:10000")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Порт, который слушает Render
PORT = int(os.getenv("PORT", 10000))
# ------------------------------------

TRIGGER_WORDS = ["погода", "weather", "погодка"]
ADDRESS_WORDS = ["бот", "bot"]

POPULAR_CITIES = {
    "Минск": "🇧🇾", "Москва": "🇷🇺", "Санкт-Петербург": "🇷🇺", "Киев": "🇺🇦",
    "Варшава": "🇵🇱", "Берлин": "🇩🇪", "Лондон": "🇬🇧", "Париж": "🇫🇷",
    "Нью-Йорк": "🇺🇸", "Токио": "🇯🇵",
}

GROUP_SETTINGS = {"delete_trigger_message": False, "reply_to_user": True}

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
chat_last_city: dict[int, str] = {}

# ... (Здесь идут все ваши функции API, парсинга, клавиатур, форматирования и хендлеры без изменений) ...
# Скопируйте их сюда из предыдущего кода.

# ==================== ГЛАВНЫЙ ХЕНДЛЕР ТЕКСТА ====================
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    # ... (Логика обработки текста без изменений)
    pass

# ==================== ГЕОЛОКАЦИЯ ====================
@dp.message(F.location)
async def handle_location(message: Message):
    # ... (Логика обработки геолокации без изменений)
    pass

# ==================== ЗАПУСК ====================
async def on_startup(bot: Bot):
    """Устанавливаем вебхук при старте."""
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook установлен: {WEBHOOK_URL}")

async def main():
    # Привязываем функцию к старту
    dp.startup.register(on_startup)

    # Создаём веб-приложение aiohttp
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    # Запускаем сервер на порту, который даёт Render
    print(f"🚀 Запускаю веб-сервер на порту {PORT}...")
    await web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    asyncio.run(main())