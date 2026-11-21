import logging
import os
import asyncio
import datetime
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

SYSTEM_PROMPT = """Твоя роль: Ты — высококвалифицированный и проактивный ассистент... (оставьте ваш промпт)"""


# --- CalDAV Mail.ru ---
async def add_mailru_event(summary: str, start_time: datetime.datetime) -> bool:
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
            calendar.save_event(dtstart=start_time, dtend=end_time, summary=summary)
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

    # --- Напоминание в календарь Mail.ru ---
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
        start_time = datetime.datetime.now(moscow_tz) + datetime.timedelta(seconds=final_delay_seconds)

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

    # --- Погода ---
    if awaiting_info and awaiting_info.get('type') == 'city_input':
        city = user_text.strip()
        weather_msg = await get_weather(city)
        await update.message.reply_html(f"<b>Погода в {city}:</b>\n{weather_msg}")
        context.user_data.pop('awaiting', None)
        return

    # --- GigaChat ---
    if 'gigachat_client' not in context.bot_data:
        try:
            context.bot_data['gigachat_client'] = GigaChat(
                scope="GIGACHAT_API_PERS",
                credentials=GIGACHAT_KEY,
                verify_ssl_certs=False
            )
            logger.info("✅ GigaChat клиент создан.")
        except Exception as e:
            logger.error(f"FATAL: GigaChat: {e}")
            await update.message.reply_text("❌ Ошибка подключения к GigaChat.")
            return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    client = context.bot_data['gigachat_client']
    messages = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]

    history = context.user_data.get('history', [])
    recent = history[-20:] if len(history) > 20 else history
    for msg in recent:
        if msg.get('role') and msg.get('content'):
            role_map = {'user': MessagesRole.USER, 'assistant': MessagesRole.ASSISTANT}
            if msg['role'] in role_map:
                messages.append(Messages(role=role_map[msg['role']], content=msg['content']))
    messages.append(Messages(role=MessagesRole.USER, content=user_text))

    try:
        response = await asyncio.to_thread(
            client.chat, Chat(model="GigaChat-2", messages=messages)
        )
        if response and response.choices and response.choices[0].message.content:
            answer = response.choices[0].message.content
            context.user_data['history'].append({'role': 'user', 'content': user_text})
            context.user_data['history'].append({'role': 'assistant', 'content': answer})
            if len(context.user_data['history']) > 40:
                context.user_data['history'] = context.user_data['history'][-40:]
            await update.message.reply_html(f"<b>Ответ:</b>\n\n{answer}")
        else:
            await update.message.reply_text("Нейросеть ответила пустотой.")
    except Exception as e:
        error_msg = str(e).replace("\n", " ").replace("\r", "")
        logger.error(f"GigaChat: {error_msg}")
        await update.message.reply_text(f"⚠️ Ошибка GigaChat:\n<code>{error_msg}</code>", parse_mode='HTML')


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Внутренняя ошибка. Попробуйте ещё раз.")
        except Exception:
            pass


def main() -> None:
    if not TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не задан!")
        return
    if not GIGACHAT_KEY:
        logger.error("❌ GIGACHAT_API_KEY не задан!")
        return

    logger.info("🚀 Запуск бота...")
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    application.add_error_handler(error_handler)

    logger.info("✅ Бот запущен в режиме polling.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
