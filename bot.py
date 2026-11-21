import logging
import os
import asyncio
import aiohttp
import threading
import signal
from flask import Flask
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

load_dotenv()
TOKEN = os.environ.get("TELEGRAM_TOKEN")
GIGACHAT_KEY = os.environ.get("GIGACHAT_API_KEY")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- FLASK ДЛЯ RENDER ---
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
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

# --- ОБРАБОТЧИКИ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name
    keyboard = [
        [InlineKeyboardButton("🌤 Погода", callback_data="weather"),
         InlineKeyboardButton("💱 Курс валют", callback_data="currency")],
        [InlineKeyboardButton("⏰ Напоминание", callback_data="remind"),
         InlineKeyboardButton("❓ Помощь", callback_data="help")],
        [InlineKeyboardButton("🤖 Спросить GigaChat", callback_data="gigachat")]
    ]
    await update.message.reply_text(
        f"Привет, {name}! Выбери действие или напиши текст.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "weather":
        await query.edit_message_text("Напиши город, например: Москва")
        context.user_data['awaiting'] = 'weather_city'
    elif data == "currency":
        await query.edit_message_text(await get_currency())
    elif data == "remind":
        await query.edit_message_text("Напиши: 'Напомни через 5 минут ...'")
        context.user_data['awaiting'] = 'reminder_text'
    elif data == "help":
        await query.edit_message_text("📖 Отвечаю на вопросы, показываю погоду, курсы, ставлю напоминания.", parse_mode="Markdown")
    elif data == "gigachat":
        await query.edit_message_text("Напиши свой вопрос для GigaChat.")
        context.user_data['awaiting'] = 'gigachat'

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    chat_id = update.effective_chat.id
    awaiting = context.user_data.get('awaiting')

    if awaiting == 'weather_city':
        context.user_data['awaiting'] = None
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        await update.message.reply_text(f"🌤 {text}:\n{await get_weather(text)}")
        return

    if awaiting == 'reminder_text' or text.lower().startswith("напомни"):
        context.user_data['awaiting'] = None
        parts = text.lower().split()
        delay, reminder_text = 5, text
        if "через" in parts:
            idx = parts.index("через")
            if idx + 2 < len(parts):
                try:
                    delay = int(parts[idx + 1])
                    unit = parts[idx + 2]
                    if "час" in unit: delay *= 60
                    elif "сек" in unit: delay = max(1, delay // 60)
                    reminder_text = " ".join(parts[idx + 3:]) or "Напоминание"
                except ValueError: delay = 5
        context.job_queue.run_once(send_reminder, delay * 60, data={"chat_id": chat_id, "text": reminder_text})
        await update.message.reply_text(f"✅ Напоминание через {delay} мин.: {reminder_text}")
        return

    client = context.bot_data.get('gigachat_client')
    if not client:
        await update.message.reply_text("GigaChat не инициализирован.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    try:
        chat_request = Chat(model="GigaChat-2", messages=[Messages(role=MessagesRole.USER, content=text)])
        response = client.chat(chat_request)
        if response and response.choices:
            await update.message.reply_text(f"🤖 <b>Ответ:</b>\n\n{response.choices[0].message.content}", parse_mode="HTML")
        else:
            await update.message.reply_text("Пустой ответ от нейросети.")
    except Exception as e:
        logging.error(f"GigaChat error: {e}")
        await update.message.reply_text(f"Ошибка:\n{e}")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    await context.bot.send_message(chat_id=job.data['chat_id'], text=f"⏰ *Напоминание:* {job.data['text']}", parse_mode="Markdown")

# --- ГЛАВНАЯ ФУНКЦИЯ ---
async def main():
    if not TOKEN or not GIGACHAT_KEY:
        logging.error("FATAL: Токены не найдены.")
        return

    # Запускаем Flask в отдельном потоке ДО инициализации бота
    threading.Thread(target=run_flask, daemon=True).start()
    print("✅ Flask-сервер для Render запущен.")

    giga_client = GigaChat(scope="GIGACHAT_API_PERS", credentials=GIGACHAT_KEY, verify_ssl_certs=False)
    application = Application.builder().token(TOKEN).build()
    application.bot_data['gigachat_client'] = giga_client

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    stop_event = asyncio.Event()
    def handle_sigterm():
        logging.info("Получен SIGTERM, останавливаю...")
        stop_event.set()
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, handle_sigterm)
    except NotImplementedError:
        signal.signal(signal.SIGTERM, lambda s, f: handle_sigterm())

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    print("✅ Бот в сети.")

    await stop_event.wait()

    print("Останавливаю...")
    await application.updater.stop()
    await application.stop()
    await application.shutdown()
    print("✅ Бот остановлен.")

if __name__ == "__main__":
    asyncio.run(main())
