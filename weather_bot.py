import asyncio
import os
import re
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

WEBHOOK_HOST = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:10000")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

PORT = int(os.getenv("PORT", 10000))

# Триггер-слова — ищутся в ЛЮБОМ месте сообщения
TRIGGER_WORDS = [
    "погода", "погодка", "погоду", "погоде", "погоды", "погодой",
    "метео", "метеосводка",
    "weather",
    "прогноз",
    "температура",
]

# Слова-мусор, которые точно не являются городом
SKIP_WORDS = {
    # предлоги и союзы
    "в", "во", "на", "для", "по", "о", "об", "про", "с", "со", "из", "от", "до",
    "и", "а", "но", "же", "ли", "бы", "не", "ни", "у", "к", "ко", "при", "над",
    "под", "за", "без", "через", "между",
    "for", "in", "at", "on", "the", "of", "to", "with", "and", "or",
    # вопросительные / вводные
    "какая", "какой", "какое", "какие", "какую", "каком", "каких",
    "покажи", "скажи", "подскажи", "узнай", "хочу", "можно", "надо", "дай",
    "сегодня", "завтра", "вчера", "сейчас", "будет", "была", "был", "были",
    "пожалуйста", "плиз", "please", "давай", "давайте", "лучше",
    "нужно", "хотел", "хотела", "хотелось",
    # сам триггер
    "погода", "погодка", "погоду", "погоде", "погоды", "погодой",
    "метео", "метеосводка", "weather", "прогноз", "температура",
    # общие слова
    "ну", "что", "эту", "этот", "эта", "эти", "там", "тут", "где", "когда",
    "сделал", "сделай", "сделать", "показал", "показать", "покажешь",
    "меню", "список", "кнопка", "кнопки", "кнопку",
    "бот", "боте", "бота", "боту", "ботом",
    # мат и ругань
    "ебашь", "ебал", "ебать", "ебала", "ебет", "ебут",
    "блядскую", "блядь", "блять", "бля", "блят",
    "хуй", "хуя", "хую", "хуем", "хуе", "хуё",
    "пиздец", "пизда", "пизды", "пизду",
    "пидорас", "пидор", "пидр",
    "сука", "суки", "суку", "сучка", "сучки",
    "нах", "нахуй", "нахер", "нахрен",
    "нет", "да", "бог", "боже", "господи",
}

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

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
chat_last_city: dict[int, str] = {}


# ==================== API ====================
def get_coordinates(city_name: str):
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": city_name, "count": 1, "language": "ru", "format": "json"}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if "results" in data and data["results"]:
            res = data["results"][0]
            return res["latitude"], res["longitude"], res.get("name", city_name)
    except Exception:
        pass
    return None, None, None


def get_weather(lat: float, lon: float):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                   "weather_code,wind_speed_10m,pressure_msl",
        "daily": "temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max,sunrise,sunset",
        "timezone": "auto",
        "forecast_days": 1,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def decode_weather_code(code: int):
    codes = {
        0: ("Ясно", "☀️"), 1: ("Преимущественно ясно", "🌤"),
        2: ("Переменная облачность", "⛅"), 3: ("Пасмурно", "☁️"),
        45: ("Туман", "🌫"), 48: ("Оседающий туман", "🌫"),
        51: ("Лёгкая морось", "🌦"), 53: ("Умеренная морось", "🌦"),
        55: ("Плотная морось", "🌦"),
        61: ("Небольшой дождь", "🌧"), 63: ("Умеренный дождь", "🌧"),
        65: ("Сильный дождь", "🌧"),
        71: ("Небольшой снег", "🌨"), 73: ("Умеренный снег", "🌨"),
        75: ("Сильный снег", "🌨"), 77: ("Снежная крупа", "🌨"),
        80: ("Ливень", "🌧"), 81: ("Сильный ливень", "🌧"),
        82: ("Очень сильный ливень", "⛈"),
        85: ("Снежный ливень", "🌨"), 86: ("Сильный снежный ливень", "🌨"),
        95: ("Гроза", "⛈"), 96: ("Гроза с градом", "⛈"),
        99: ("Сильная гроза с градом", "⛈"),
    }
    return codes.get(code, ("Неизвестно", "❓"))


# ==================== ПАРСИНГ ====================
def normalize_city(word: str) -> str:
    """Грубо отсекает падежные окончания для лучшего поиска в API."""
    w = word.strip(" ?!.,:;()\"'—–-").lower()
    if not w or len(w) < 2:
        return ""
    for suffix in ("ой", "ей", "е", "у", "ю", "а", "я", "ы", "и"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[:-len(suffix)]
    return w


def is_likely_city(word: str, position: int) -> bool:
    """Эвристика: слово похоже на название города?"""
    w = word.strip()
    if len(w) < 3:
        return False

    wl = w.lower()

    # Отсеиваем глагольные/наречные окончания
    bad_suffixes = (
        "ать", "ить", "уть", "ыть", "еть",
        "ешь", "ишь", "ёшь",
        "ал", "ил", "ел", "ул", "ыл",
        "ла", "ло", "ли", "ле",
        "но", "то", "же", "бы",
        "ся", "сь",
    )
    for suf in bad_suffixes:
        if wl.endswith(suf) and len(wl) - len(suf) >= 2:
            return False

    # С большой буквы — вероятно имя собственное
    if w[0].isupper():
        return True

    # Слово в нижнем регистре в начале — вряд ли город
    if position == 0:
        return False

    return True


def extract_city_from_text(text: str, bot_username: str):
    """
    Возвращает:
      - строку с городом — если найден через API
      - ""              — триггер есть, но города нет → показать меню
      - None            — триггера нет вообще → молчать
    """
    if not text:
        return None

    original = text.strip()

    cleaned = re.sub(
        rf"@{re.escape(bot_username)}\b", " ", original, flags=re.IGNORECASE
    )

    lowered = cleaned.lower()
    trigger_found = any(word in lowered for word in TRIGGER_WORDS)
    if not trigger_found:
        return None

    # Убираем триггер-слова
    for word in TRIGGER_WORDS:
        cleaned = re.sub(
            rf"\b{re.escape(word)}\w*\b", " ", cleaned, flags=re.IGNORECASE
        )

    cleaned = re.sub(r"[?!.,:;()\"'—–\-]", " ", cleaned)
    words = [w for w in cleaned.split() if w]

    if not words:
        return ""

    # Собираем кандидатов: 3, 2, 1 слово
    candidates = []
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            phrase_words = words[i:i + size]

            if any(w.lower() in SKIP_WORDS for w in phrase_words):
                continue

            if size == 1:
                w = phrase_words[0]
                if not is_likely_city(w, i):
                    continue
            else:
                if any(len(w) < 3 for w in phrase_words):
                    continue

            candidates.append(" ".join(phrase_words))

    # Нормализация окончаний
    extra = []
    for c in candidates:
        norm = " ".join(normalize_city(w) for w in c.split())
        if norm and norm != c.lower():
            extra.append(norm)
    candidates.extend(extra)

    # Убираем дубли
    seen = set()
    unique_candidates = []
    for c in candidates:
        if c.lower() not in seen:
            seen.add(c.lower())
            unique_candidates.append(c)

    # Проверяем через API
    for candidate in unique_candidates:
        if len(candidate) < 3:
            continue
        if candidate.islower() and len(candidate) < 4:
            continue

        lat, lon, resolved = get_coordinates(candidate)
        if lat is not None and resolved:
            # Защита: имя из API должно быть похоже на кандидата
            if len(resolved) < 3:
                continue
            rl = resolved.lower()
            cl = candidate.lower()
            if not (rl.startswith(cl[:3]) or cl.startswith(rl[:3])):
                continue
            return resolved

    return ""


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
    desc, emoji = decode_weather_code(cur["weather_code"])

    return (
        f"<b>{emoji} Погода в {city}</b>\n"
        f"<i>{datetime.now().strftime('%d.%m.%Y %H:%M')}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌡 <b>Температура:</b> {cur['temperature_2m']}°C\n"
        f"🤔 <b>Ощущается:</b> {cur['apparent_temperature']}°C\n"
        f"{emoji} <b>Состояние:</b> {desc}\n"
        f"💧 <b>Влажность:</b> {cur['relative_humidity_2m']}%\n"
        f"🌬 <b>Ветер:</b> {cur['wind_speed_10m']} км/ч\n"
        f"📊 <b>Давление:</b> {round(cur['pressure_msl'] * 0.750064)} мм рт. ст."
    )


def format_daily_forecast(city: str, data: dict) -> str:
    d = data["daily"]
    return (
        f"<b>📅 Прогноз на сегодня — {city}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔺 <b>Максимум:</b> {d['temperature_2m_max'][0]}°C\n"
        f"🔻 <b>Минимум:</b> {d['temperature_2m_min'][0]}°C\n"
        f"☔ <b>Осадки:</b> {d['precipitation_probability_max'][0]}%\n"
        f"🌅 <b>Восход:</b> {d['sunrise'][0].split('T')[1]}\n"
        f"🌇 <b>Закат:</b> {d['sunset'][0].split('T')[1]}"
    )


# ==================== ОТПРАВКА ====================
async def send_weather(
    message: Message,
    city: str,
    is_group: bool = False,
    reply_to: Message | None = None,
):
    lat, lon, resolved = get_coordinates(city)
    if lat is None:
        text = f"❌ Город «{city}» не найден."
        kb = None if is_group else back_kb()
        kwargs = {}
        if is_group and reply_to and GROUP_SETTINGS["reply_to_user"]:
            kwargs["reply_to_message_id"] = reply_to.message_id
        await message.answer(text, reply_markup=kb, **kwargs)
        return

    data = get_weather(lat, lon)
    if not data:
        await message.answer("❌ Не удалось получить данные о погоде.")
        return

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
    lat, lon, resolved = get_coordinates(city)
    if lat is None:
        await message.edit_text(f"❌ Город «{city}» не найден.", reply_markup=back_kb())
        return
    data = get_weather(lat, lon)
    if not data:
        await message.edit_text("❌ Не удалось получить погоду.", reply_markup=back_kb())
        return
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
            "• <code>погода в Москве</code>\n"
            "• <code>метео Париж</code>\n"
            "• или просто <code>погода</code> — открою меню\n\n"
            "Команды: /weather, /help"
        )
        await message.answer(text)
    else:
        text = (
            "👋 <b>Привет! Я бот погоды.</b>\n\n"
            "Выбери город из списка ниже или отправь его название в чат."
        )
        await message.answer(text, reply_markup=main_menu_kb())


@dp.message(Command("help"))
async def cmd_help(message: Message):
    is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

    if is_group:
        text = (
            "ℹ️ <b>Как пользоваться ботом в группе</b>\n\n"
            "<b>Примеры:</b>\n"
            "• <code>погода Минск</code>\n"
            "• <code>погода в Москве</code>\n"
            "• <code>метео Париж</code>\n"
            "• <code>weather London</code>\n"
            "• просто <code>погода</code> — открою меню\n\n"
            "<b>Команды:</b>\n"
            "• /weather <i>город</i> — погода\n"
            "• /weather — повторить последний город\n"
            "• /help — эта справка"
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
    await show_weather_edit(callback.message, city)
    await callback.answer("🔄 Обновлено")


@dp.callback_query(F.data.startswith("daily:"))
async def cb_daily(callback: CallbackQuery):
    city = callback.data.split(":", 1)[1]
    lat, lon, resolved = get_coordinates(city)
    if lat is None:
        await callback.answer("Город не найден", show_alert=True)
        return
    data = get_weather(lat, lon)
    if not data or "daily" not in data:
        await callback.answer("Не удалось получить прогноз", show_alert=True)
        return

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
        city = message.text.strip()
        if not city:
            return

        wait = await message.answer(f"🔍 Ищу погоду для «{city}»...")
        lat, lon, resolved = get_coordinates(city)
        if lat is None:
            await wait.edit_text(
                f"❌ Город «{city}» не найден.\nПопробуй другое название.",
                reply_markup=back_kb(),
            )
            return
        data = get_weather(lat, lon)
        if not data:
            await wait.edit_text("❌ Не удалось получить данные.")
            return

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
    data = get_weather(lat, lon)
    if not data:
        await message.answer("❌ Не удалось получить погоду по геолокации.")
        return

    await message.answer(
        format_current_weather("Ваше местоположение", data),
        reply_markup=None if is_group else city_actions_kb("Ваше местоположение"),
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

    return app


if __name__ == "__main__":
    print(f"🚀 Запускаю веб-сервер на порту {PORT}...")
    web.run_app(main(), host="0.0.0.0", port=PORT)
