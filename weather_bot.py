import asyncio
import os
import re
import time
import requests
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")  # Новый ключ weatherapi.com

WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:10000")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

PORT = int(os.getenv("PORT", 10000))

# Триггер-слова — ищутся в любом месте сообщения
TRIGGER_WORDS = [
    "погода", "погодка", "погоду", "погоде", "погоды", "погодой",
    "метео", "метеосводка",
    "weather",
    "прогноз",
    "температура",
]

# Предлоги между триггером и городом
LINK_WORDS = ["в", "во", "на", "для", "по", "for", "in", "at"]

# ==================== КЕШ ====================
# Кеш городов: "минск" -> "Минск" (для случаев, когда API вернул другое имя)
_city_cache: dict[str, str] = {}
# Кеш погоды: "минск" -> (timestamp, data)
_weather_cache: dict[str, tuple] = {}
WEATHER_TTL = 900  # 15 минут

POPULAR_CITIES = {
    "Минск": "🇧🇾",
    "Москва": "🇷🇺",
    "Санкт-Петербург": "🇷🇺",
    "Киев": "🇺🇦",
    "Варшава": "🇵🇱",
    "Берлин": "🇩🇪",
    "Лондон": "🇬🇧",
    "Париж": "🇫🇷",
    "Нью-Йорк": "🇺🇸",
    "Токио": "🇯🇵",
}

GROUP_SETTINGS = {
    "reply_to_user": True,
}

AUTHOR = '@PHARAOH_G6'
AUTHOR_URL = 'https://t.me/PHARAOH_G6'

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
chat_last_city: dict[int, str] = {}


# ==================== API (weatherapi.com) ====================
def get_weather_data(city: str):
    """
    Получает текущую погоду и прогноз на день через weatherapi.com.
    Кеширует на 15 минут.
    """
    key = city.lower().strip()
    now = time.time()

    # Кеш погоды
    if key in _weather_cache:
        ts, data = _weather_cache[key]
        if now - ts < WEATHER_TTL:
            return data

    url = "https://api.weatherapi.com/v1/forecast.json"
    params = {
        "key": WEATHER_API_KEY,
        "q": city,
        "days": 1,
        "aqi": "no",
        "alerts": "no",
        "lang": "ru",
    }

    try:
        r = requests.get(url, params=params, timeout=15)

        if r.status_code == 400:
            # Город не найден
            data = r.json()
            error_msg = data.get("error", {}).get("message", "")
            if "No matching location found" in error_msg:
                print(f"⚠️ Город не найден: {city}")
                return None
            print(f"⚠️ WeatherAPI 400: {error_msg}")
            return None

        if r.status_code == 401:
            print("⚠️ WeatherAPI: неверный API-ключ")
            return None

        if r.status_code == 403:
            print("⚠️ WeatherAPI: лимит запросов исчерпан")
            return None

        if r.status_code != 200:
            print(f"⚠️ WeatherAPI {r.status_code}: {r.text[:200]}")
            return None

        data = r.json()
        _weather_cache[key] = (now, data)
        return data

    except Exception as e:
        print(f"⚠️ WeatherAPI error: {e}")
        return None


# ==================== ПАРСИНГ ====================
def extract_city_from_text(text: str, bot_username: str):
    """
    Простой парсер: <триггер> [<предлог>] <город>
    Возвращает:
      - строку с городом — если триггер + город
      - ""              — только триггер → меню
      - None            — нет триггера → молчать
    """
    if not text:
        return None

    original = text.strip()

    # Убираем упоминание бота в начале
    cleaned = re.sub(
        rf"^@{re.escape(bot_username)}\b[,:\s]*", "", original, flags=re.IGNORECASE
    ).strip()

    # Ищем триггер в начале сообщения
    trigger_found = False
    for word in TRIGGER_WORDS:
        pattern = rf"^{re.escape(word)}\w*\b[\s,:!?-]*"
        if re.match(pattern, cleaned, flags=re.IGNORECASE):
            trigger_found = True
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
            break

    if not trigger_found:
        # Триггер может быть в середине
        lowered = cleaned.lower()
        if not any(word in lowered for word in TRIGGER_WORDS):
            return None
        # Убираем триггер из любого места
        for word in TRIGGER_WORDS:
            cleaned = re.sub(
                rf"\b{re.escape(word)}\w*\b", " ", cleaned, flags=re.IGNORECASE
            )
        cleaned = cleaned.strip()

    # Убираем предлог в начале
    for link in LINK_WORDS:
        cleaned = re.sub(
            rf"^{re.escape(link)}\s+", "", cleaned, flags=re.IGNORECASE
        ).strip()

    # Чистим пунктуацию
    cleaned = cleaned.strip(" ?!.,:;")

    # Если пусто — только триггер был
    if not cleaned or len(cleaned) < 2 or len(cleaned) > 50:
        return ""

    # Слишком много слов — вряд ли название города
    if len(cleaned.split()) > 3:
        return ""

    return cleaned


# ==================== КЛАВИАТУРЫ ====================
def main_menu_kb() -> InlineKeyboardMarkup:
    rows = []
    cities = list(POPULAR_CITIES.items())
    for i in range(0, len(cities), 2):
        row = [
            InlineKeyboardButton(text=f"{flag} {city}", callback_data=f"city:{city}")
            for city, flag in cities[i:i + 2]
        ]
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="✏️ Ввести город вручную", callback_data="manual"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def city_actions_kb(city: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"refresh:{city}"),
            InlineKeyboardButton(text="📅 Прогноз", callback_data=f"daily:{city}"),
        ],
        [
            InlineKeyboardButton(text="🏙 Сменить город", callback_data="back_to_menu"),
        ],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")],
    ])


# ==================== ФОРМАТИРОВАНИЕ ====================
def format_current_weather(city: str, data: dict) -> str:
    cur = data["current"]
    location = data["location"]

    return (
        f"<b>🌤 Погода в {location['name']}</b>\n"
        f"<i>{datetime.now().strftime('%d.%m.%Y %H:%M')}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌡 <b>Температура:</b> {cur['temp_c']}°C\n"
        f"🤔 <b>Ощущается:</b> {cur['feelslike_c']}°C\n"
        f"☁️ <b>Состояние:</b> {cur['condition']['text']}\n"
        f"💧 <b>Влажность:</b> {cur['humidity']}%\n"
        f"🌬 <b>Ветер:</b> {cur['wind_kph']} км/ч ({cur['wind_dir']})\n"
        f"📊 <b>Давление:</b> {round(cur['pressure_mb'] * 0.750064)} мм рт. ст.\n"
        f"\n<i>🤖 {AUTHOR}</i>"
    )


def format_daily_forecast(city: str, data: dict) -> str:
    day = data["forecast"]["forecastday"][0]["day"]
    astro = data["forecast"]["forecastday"][0]["astro"]

    return (
        f"<b>📅 Прогноз на сегодня — {city}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔺 <b>Максимум:</b> {day['maxtemp_c']}°C\n"
        f"🔻 <b>Минимум:</b> {day['mintemp_c']}°C\n"
        f"☔ <b>Осадки:</b> {day['daily_chance_of_rain']}%\n"
        f"🌅 <b>Восход:</b> {astro['sunrise']}\n"
        f"🌇 <b>Закат:</b> {astro['sunset']}\n"
        f"\n<i>🤖 {AUTHOR}</i>"
    )


# ==================== ОТПРАВКА ====================
async def send_weather(
    message: Message,
    city: str,
    is_group: bool = False,
    reply_to: Message | None = None,
):
    data = get_weather_data(city)
    if not data:
        text = f"❌ Не удалось получить погоду для «{city}».\nПроверь название города."
        kb = None if is_group else back_kb()
        kwargs = {}
        if is_group and reply_to and GROUP_SETTINGS["reply_to_user"]:
            kwargs["reply_to_message_id"] = reply_to.message_id
        await message.answer(text, reply_markup=kb, **kwargs)
        return

    resolved = data["location"]["name"]
    chat_last_city[message.chat.id] = resolved
    kb = None if is_group else city_actions_kb(resolved)

    kwargs = {}
    if is_group and reply_to and GROUP_SETTINGS["reply_to_user"]:
        kwargs["reply_to_message_id"] = reply_to.message_id

    await message.answer(
        format_current_weather(resolved, data),
        reply_markup=kb,
        **kwargs,
    )


async def show_weather_edit(message: Message, city: str):
    data = get_weather_data(city)
    if not data:
        await message.edit_text(
            f"❌ Город «{city}» не найден.",
            reply_markup=back_kb(),
        )
        return

    resolved = data["location"]["name"]
    await message.edit_text(
        format_current_weather(resolved, data),
        reply_markup=city_actions_kb(resolved),
    )


# ==================== КОМАНДЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: Message):
    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    if is_group:
        text = (
            "👋 <b>Привет!</b>\n\n"
            "Чтобы узнать погоду, напиши:\n"
            "• <code>погода Минск</code>\n"
            "• <code>метео Париж</code>\n"
            "• или просто <code>погода</code> — открою меню\n\n"
            "<i>⚠️ Город указывай в именительном падеже</i>\n\n"
            "Команды: /weather, /help\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Бот создан <a href=\"{AUTHOR_URL}\">{AUTHOR}</a>"
        )
        await message.answer(text)
    else:
        text = (
            "👋 <b>Привет! Я бот погоды.</b>\n\n"
            "Выбери город из списка ниже или отправь его название в чат.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Бот создан <a href=\"{AUTHOR_URL}\">{AUTHOR}</a>"
        )
        await message.answer(text, reply_markup=main_menu_kb())


@dp.message(Command("help"))
async def cmd_help(message: Message):
    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    if is_group:
        text = (
            "ℹ️ <b>Как пользоваться ботом в группе</b>\n\n"
            "<b>Формат:</b> <code>погода Город</code>\n\n"
            "<b>Примеры:</b>\n"
            "• <code>погода Минск</code>\n"
            "• <code>метео Москва</code>\n"
            "• <code>weather London</code>\n"
            "• <code>погода</code> — открою меню\n\n"
            "<b>Команды:</b>\n"
            "• /weather <i>город</i> — погода\n"
            "• /weather — повторить последний город\n"
            "• /help — эта справка\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Бот создан <a href=\"{AUTHOR_URL}\">{AUTHOR}</a>"
        )
    else:
        text = (
            "ℹ️ <b>Как пользоваться ботом</b>\n\n"
            "• Нажми на кнопку с городом\n"
            "• Или напиши название города вручную\n"
            "• В группе: <code>погода Минск</code>"
        )

    await message.answer(text)


@dp.message(Command("weather"))
async def cmd_weather(message: Message):
    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    args = message.text.split(maxsplit=1)

    if len(args) > 1:
        city = args[1].strip()
    else:
        city = chat_last_city.get(message.chat.id)
        if not city:
            if is_group:
                await message.answer(
                    "🏙 <b>Выбери город:</b>",
                    reply_markup=main_menu_kb(),
                )
            else:
                await message.answer(
                    "📍 Укажи город: <code>/weather Минск</code>"
                )
            return

    await send_weather(
        message,
        city,
        is_group=is_group,
        reply_to=message if is_group else None,
    )


# ==================== INLINE-КНОПКИ ====================
@dp.callback_query(F.data == "back_to_menu")
async def cb_back_to_menu(callback: CallbackQuery):
    await callback.message.edit_text("🏙 <b>Выбери город:</b>", reply_markup=main_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "manual")
async def cb_manual(callback: CallbackQuery):
    await callback.message.edit_text(
        "✏️ <b>Напиши название города в чат</b>\n"
        "Например: <code>Гомель</code> или <code>New York</code>",
        reply_markup=back_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("city:"))
async def cb_city(callback: CallbackQuery):
    city = callback.data.split(":", 1)[1]
    await show_weather_edit(callback.message, city)
    chat_last_city[callback.message.chat.id] = city
    await callback.answer(f"Погода: {city}")


@dp.callback_query(F.data.startswith("refresh:"))
async def cb_refresh(callback: CallbackQuery):
    city = callback.data.split(":", 1)[1]
    # Сбрасываем кеш для этого города
    _weather_cache.pop(city.lower().strip(), None)
    await show_weather_edit(callback.message, city)
    await callback.answer("🔄 Обновлено")


@dp.callback_query(F.data.startswith("daily:"))
async def cb_daily(callback: CallbackQuery):
    city = callback.data.split(":", 1)[1]
    data = get_weather_data(city)
    if not data or "forecast" not in data:
        await callback.answer("Не удалось получить прогноз", show_alert=True)
        return

    resolved = data["location"]["name"]
    await callback.message.edit_text(
        format_daily_forecast(resolved, data),
        reply_markup=city_actions_kb(city),
    )
    await callback.answer()


# ==================== ГЛАВНЫЙ ХЕНДЛЕР ====================
@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    bot_username = (await bot.me()).username

    if is_group:
        result = extract_city_from_text(message.text, bot_username)

        # Reply на сообщение бота — повторяем последний город
        if result is None and message.reply_to_message:
            if message.reply_to_message.from_user.id == bot.id:
                last = chat_last_city.get(message.chat.id)
                if last:
                    await send_weather(message, last, is_group=True, reply_to=message)
                return

        # Триггера нет — молчим
        if result is None:
            return

        # Триггер есть, но город не найден — показываем меню
        if result == "":
            await message.answer(
                "🏙 <b>Выбери город:</b>\n"
                "<i>Или напиши: погода Минск</i>",
                reply_markup=main_menu_kb(),
                reply_to_message_id=(
                    message.message_id if GROUP_SETTINGS["reply_to_user"] else None
                ),
            )
            return

        # Триггер + город — показываем погоду
        await send_weather(message, result, is_group=True, reply_to=message)

    else:
        # В личке — любое сообщение = название города
        city = message.text.strip()
        if not city:
            return

        wait = await message.answer(f"🔍 Ищу погоду для «{city}»...")
        data = get_weather_data(city)
        if not data:
            await wait.edit_text(
                f"❌ Город «{city}» не найден.\nПопробуй другое название.",
                reply_markup=back_kb(),
            )
            return

        resolved = data["location"]["name"]
        chat_last_city[message.chat.id] = resolved
        await wait.edit_text(
            format_current_weather(resolved, data),
            reply_markup=city_actions_kb(resolved),
        )


# ==================== ГЕОЛОКАЦИЯ ====================
@dp.message(F.location)
async def handle_location(message: Message):
    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    lat = message.location.latitude
    lon = message.location.longitude

    # weatherapi.com принимает координаты как "lat,lon"
    city = f"{lat},{lon}"
    data = get_weather_data(city)
    if not data:
        await message.answer("❌ Не удалось получить погоду по геолокации.")
        return

    resolved = data["location"]["name"]
    await message.answer(
        format_current_weather(resolved, data),
        reply_markup=None if is_group else city_actions_kb(resolved),
    )


# ==================== WEBHOOKS И ЗАПУСК ====================
async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook установлен: {WEBHOOK_URL}")


async def set_commands():
    private_commands = [
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="weather", description="🌤 Погода (можно: /weather Минск)"),
        BotCommand(command="help", description="ℹ️ Помощь"),
    ]
    group_commands = [
        BotCommand(command="weather", description="🌤 Погода: /weather Минск"),
        BotCommand(command="help", description="ℹ️ Как пользоваться ботом"),
    ]
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())


async def main():
    dp.startup.register(on_startup)
    await set_commands()

    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    async def healthcheck(request):
        return web.Response(text="OK")

    app.router.add_get("/", healthcheck)
    app.router.add_get("/health", healthcheck)

    return app


if __name__ == "__main__":
    print(f"🚀 Запускаю веб-сервер на порту {PORT}...")
    web.run_app(main(), host="0.0.0.0", port=PORT)
