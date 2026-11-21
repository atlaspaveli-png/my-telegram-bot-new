import logging
import os
import asyncio
import datetime
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

import aiohttp
import caldav

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler,
    CallbackQueryHandler, filters
)

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

load_dotenv()
TOKEN = os.environ.get("TELEGRAM_TOKEN")
GIGACHAT_KEY = os.environ.get("GIGACHAT_API_KEY")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Твоя роль: Ты — высококвалифицированный и проактивный ассистент. Твоя главная цель — помогать мне решать задачи максимально эффективно, глубоко и качественно.
Правила и стиль общения:
1. Структурированность: Всегда представляй информацию в удобном виде: используй списки, подзаголовки, таблицы или выделение жирным, чтобы текст легко читался.
2. Проактивность: Не просто отвечай на вопрос, а старайся предвидеть следующий шаг. Если я прошу проанализировать текст, предложи также варианты его улучшения. Если я ставлю цель, предложи план её достижения. Задавай уточняющие вопросы, если тебе не хватает данных.
3. Честность и прозрачность: Если ты не знаешь ответа или не уверен в чем-то — прямо скажи об этом. Не придумывай факты, даты или источники. Если для качественного ответа нужно что-то уточнить у меня, задай вопрос.
4. Краткость и суть: Избегай «воды», общих фраз и повторов. Переходи сразу к делу. Если ответ можно дать в трех предложениях, не пиши три абзаца.
5. Адаптивность: Подстраивайся под мой контекст. Если я пишу неформально — отвечай так же. Если мы обсуждаем серьезный документ — используй деловой язык.
Как мы будем работать:
• Когда я ставлю задачу, сначала кратко перескажи её своими словами, чтобы я убедился, что мы поняли друг друга.
• Если задача сложная, предложи пошаговый план её решения перед тем, как начать действовать.
Начнём."""


# --- Health-сервер для Render ---
def run_health_server():
    """Крошечный HTTP-сервер, чтобы Render видел открытый порт."""
    port = int(os.environ.get("PORT", 10000))

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format, *args):
            pass  # отключаем спам в логах

    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"✅ Health-сервер запущен на порту {port}")
    server.serve_forever()


# --- CalDAV Mail.ru ---
async def add_mailru_event(summary: str, start_time: datetime.datetime) -> bool:
    """Создаёт событие в календаре Mail.ru. Возвращает True при успехе."""
    email = os.environ.get("MAILRU_EMAIL")
    password = os.environ.get("MAILRU_PASSWORD")
    if not email or not password:
        logger.error("MAILRU_EMAIL или MAILRU_PASSWORD не заданы.")
        return False

    def _sync_create():
        with caldav.DAVClient(
            url="https://caldav.mail.ru/",
            username=email,
            password=password
        ) as client:
            principal = client.principal()
            calendars = principal.calendars()
            if not calendars:
                raise RuntimeError("В аккаунте Mail.ru не найдено календарей.")
            calendar = calendars[0]
            end_time = start_time + datetime.timedelta(hours=1)
            calendar.save_event(
                dtstart=start_time,
                dtend=end_time,
                summary=summary
            )
            return True

    try:
        return await asyncio.to_thread(_sync_create)
    except Exception as e:
        logger.error(f"Ошибка CalDAV Mail.ru: {e}")
        return False


# --- Вспомогательные функции ---
async def get_weather(city: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://wttr.in/{city}?format=3&lang=ru") as resp:
                if resp.status == 200:
                    return await resp.text()
                return "Не удалось получить погоду."
    except Exception as e:
        return f"Ошибка: {e}"


async def get_currency() -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.cbr-xml-daily.ru/daily_json.js") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    usd = data['Valute']['USD']['Value']
                    eur = data['Valute']['EUR']['Value']
                    return f"💵 USD: {usd:.2f} руб.\n💶 EUR: {eur:.2f} руб."
                return "Не удалось получить курсы."
    except Exception as e:
        return f"Ошибка: {e}"


# --- Обработчики ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name or "Пользователь"
    context.user_data.clear()
    context.user_data['history'] = []
    context.user_data['awaiting'] = None

    keyboard = [
        [InlineKeyboardButton("🌤 Погода", callback_data="weather"),
         InlineKeyboardButton("💱 Курс валют", callback_data="currency")],
        [InlineKeyboardButton("⏰ Напоминание", callback_data="remind"),
         InlineKeyboardButton("❓ Помощь", callback_data="help")],
        [InlineKeyboardButton("🤖 Спросить GigaChat", callback_data="gigachat")]
    ]

    greeting = (
        f"Привет, *{name}*! Я твой персональный ассистент.\n\n"
        "*Я сохраняю историю диалога и подстраиваюсь под тебя.*\n"
        "Выбери кнопку или напиши мне."
    )

    await update.message.reply_html(greeting, reply_markup=InlineKeyboardMarkup(keyboard))


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if 'history' not in context.user_data:
        context.user_data['history'] = []
    if 'awaiting' not in context.user_data:
        context.user_data['awaiting'] = None

    if data == "weather":
        await query.edit_message_text("Напиши город, для которого нужна погода (например: Москва)")
        context.user_data['awaiting'] = {'type': 'city_input'}

    elif data == "currency":
        message_text = await get_currency()
        try:
            await query.edit_message_text(message_text, parse_mode='HTML')
        except Exception:
            await query.message.reply_text(message_text, parse_mode='HTML')

    elif data == "remind":
        text = "⏰ Напиши текст напоминания.\n\nПример:\nНапомнить мне через 5 минут позвонить маме."
        await query.edit_message_text(text)
        context.user_data['awaiting'] = {'type': 'reminder_setup'}

    elif data == "help":
        help_text = """📖 Я умею:
• Отвечать на вопросы (с памятью!)
• Показывать погоду 🌤
• Курсы валют 💱
• Ставить напоминания в календарь Mail.ru ⏰
• Работать с GigaChat 🤖"""
        await query.edit_message_text(help_text, parse_mode='Markdown')

    elif data == "gigachat":
        await query.edit_message_text("❓ Задай свой вопрос здесь.")
        context.user_data['awaiting'] = {'type': 'free_question'}


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    chat_id = update.effective_chat.id

    if 'history' not in context.user_data:
        context.user_data['history'] = []
    if 'awaiting' not in context.user_data:
        context.user_data['awaiting'] = None

    awaiting_info = context.user_data.get('awaiting')

    # --- 1. Напоминание в календарь Mail.ru ---
    if awaiting_info and awaiting_info.get('type') == 'reminder_setup':
        parts = user_text.lower().split()
        time_idx = next((i for i, w in enumerate(parts) if w.isdigit()), None)

        if not time_idx or len(parts) < time_idx + 2:
            await update.message.reply_text(
                "Не понял формат. Пример: Напомни через 15 минут позвонить маме"
            )
            return

        delay_value = int(parts[time_idx])
        unit_word = parts[time_idx + 1]
        multiplier = 60 if unit_word.startswith(('час', 'часа')) else 1
        final_delay_seconds = delay_value * multiplier

        reminder_text = " ".join(parts[time_idx + 2:]).strip() or "Напоминание"

        moscow_tz = datetime.timezone(datetime.timedelta(hours=3))
        start_time = datetime.datetime.now(moscow_tz) + datetime.timedelta(
            seconds=final_delay_seconds
        )

        await update.message.reply_text("⏳ Создаю событие в календаре Mail.ru...")
        success = await add_mailru_event(reminder_text, start_time)

        if success:
            time_str = start_time.strftime("%H:%M")
            await update.message.reply_html(
                f"✅ Напоминание добавлено в календарь Mail.ru на <b>{time_str}</b>: {reminder_text}"
            )
        else:
            await update.message.reply_text(
                "❌ Не удалось создать событие. Проверьте логи или настройки календаря."
            )

        context.user_data.pop('awaiting', None)
        return

    # --- 2. Погода ---
    if awaiting_info and awaiting_info.get('type') == 'city_input':
        city = user_text.strip()
        weather_msg = await get_weather(city)
        await update.message.reply_html(f"<b>Погода в {city}:</b>\n{weather_msg}")
        context.user_data.pop('awaiting', None)
        return

    # --- 3. Инициализация GigaChat ---
    if 'gigachat_client' not in context.bot_data:
        try:
            context.bot_data['gigachat_client'] = GigaChat(
                scope="GIGACHAT_API_PERS",
                credentials=GIGACHAT_KEY,
                verify_ssl_certs=False
            )
            logger.info("✅ GigaChat клиент создан и сохранен в bot_data.")
        except Exception as e:
            logger.error(f"FATAL: Не удалось создать клиента GigaChat: {e}")
            await update.message.reply_text("❌ Ошибка подключения к GigaChat. Попробуй позже.")
            return

    # --- 4. Статус "печатает" ---
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # --- 5. Формирование запроса ---
    client = context.bot_data['gigachat_client']
    messages = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]

    history = context.user_data.get('history', [])
    history_limit = min(len(history), 20)
    recent_history = history[-history_limit:] if history_limit else []

    for msg_dict in recent_history:
        role_str = msg_dict.get('role')
        content = msg_dict.get('content')
        if role_str and content:
            roles_map = {
                'user': MessagesRole.USER,
                'assistant': MessagesRole.ASSISTANT,
                'system': MessagesRole.SYSTEM
            }
            mapped_role = roles_map.get(role_str.lower())
            if mapped_role:
                messages.append(Messages(role=mapped_role, content=content))

    messages.append(Messages(role=MessagesRole.USER, content=user_text))

    # --- 6. Запрос к API и ответ ---
    try:
        response = await asyncio.to_thread(
            client.chat, Chat(model="GigaChat-2", messages=messages)
        )

        answer_content = ""
        if response and response.choices and response.choices[0].message.content:
            answer_content = response.choices[0].message.content

            context.user_data['history'].append({'role': 'user', 'content': user_text})
            context.user_data['history'].append({'role': 'assistant', 'content': answer_content})

            if len(context.user_data['history']) > 40:
                context.user_data['history'] = context.user_data['history'][-40:]

            await update.message.reply_html(f"<b>Ответ:</b>\n\n{answer_content}")
        else:
            logger.warning("GigaChat вернул пустой ответ.")
            await update.message.reply_text("Нейросеть ответила пустотой. Попробуй переформулировать вопрос.")

    except Exception as e:
        error_msg = str(e).replace("\n", " ").replace("\r", "")
        logger.error(f"Ошибка GigaChat: {error_msg}")
        await update.message.reply_text(
            f"⚠️ Ошибка связи с GigaChat:\n<code>{error_msg}</code>",
            parse_mode='HTML'
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирует ошибки и сообщает пользователю."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Произошла внутренняя ошибка. Попробуйте ещё раз."
            )
        except Exception:
            pass


# --- Точка входа ---
def main() -> None:
    if not TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не найден в переменных окружения!")
        return
    if not GIGACHAT_KEY:
        logger.error("❌ GIGACHAT_API_KEY не найден в переменных окружения!")
        return

    # Запускаем health-сервер в фоне, чтобы Render видел открытый порт
    threading.Thread(target=run_health_server, daemon=True).start()

    logger.info("🚀 Запуск бота...")

    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    application.add_error_handler(error_handler)

    logger.info("✅ Хендлеры зарегистрированы. Начинаю polling.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
