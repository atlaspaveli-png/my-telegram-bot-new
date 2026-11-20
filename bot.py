import logging
import os
import asyncio
import aiohttp
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# --- Импорты для GigaChat ---
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

# --- Загрузка ключей из файла .env ---
load_dotenv()
TOKEN = os.environ.get("TELEGRAM_TOKEN")
GIGACHAT_KEY = os.environ.get("GIGACHAT_API_KEY")

# --- Настройка логирования ---
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

print("=" * 60)
print("СТАРТ: Бот V-FINAL + КНОПКИ + ВЕБ-СЕРВЕР (Python 3.11+)")
print("=" * 60)

# --- ФИКТИВНЫЙ ВЕБ-СЕРВЕР ДЛЯ RENDER ---
# Render требует, чтобы приложение слушало порт, иначе деплой зависает.
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyHandler)
    server.serve_forever()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

async def get_weather(city: str) -> str:
    """Получает погоду через wttr.in (бесплатно, без ключей)"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://wttr.in/{city}?format=3&lang=ru") as resp:
                if resp.status == 200:
                    return await resp.text()
                return "Не удалось получить погоду. Проверьте название города."
    except Exception as e:
        return f"Ошибка при получении погоды: {e}"

async def get_currency() -> str:
    """Получает курсы валют ЦБ РФ (бесплатно, без ключей)"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.cbr-xml-daily.ru/daily_json.js") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    usd = data['Valute']['USD']['Value']
                    eur = data['Valute']['EUR']['Value']
                    return f"💵 USD: {usd:.2f} руб.\n💶 EUR: {eur:.2f} руб."
                return "Не удалось получить курсы валют."
    except Exception as e:
        return f"Ошибка при получении курсов: {e}"

# --- ОБРАБОТЧИК КОМАНДЫ /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name
    keyboard = [
        [InlineKeyboardButton("🌤 Погода", callback_data="weather"),
         InlineKeyboardButton("💱 Курс валют", callback_data="currency")],
        [InlineKeyboardButton("⏰ Напоминание", callback_data="remind"),
         InlineKeyboardButton("❓ Помощь", callback_data="help")],
        [InlineKeyboardButton("🤖 Спросить GigaChat", callback_data="gigachat")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Привет, {name}! 👋\n"
        f"Я бот V-Final. Выбери действие на кнопках ниже или просто напиши мне текст.",
        reply_markup=reply_markup,
    )

# --- ОБРАБОТЧИК КНОПОК ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "weather":
        await query.edit_message_text("Напиши город, например: Москва")
        context.user_data['awaiting'] = 'weather_city'
    elif data == "currency":
        result = await get_currency()
        await query.edit_message_text(result)
    elif data == "remind":
        await query.edit_message_text("Напиши: 'Напомни через 5 минут ...' (например: Напомни через 5 минут выпить чай)")
        context.user_data['awaiting'] = 'reminder_text'
    elif data == "help":
        await query.edit_message_text(
            "📖 *Что я умею:*\n"
            "1. Отвечать на любые вопросы через GigaChat.\n"
            "2. Показывать погоду (напиши: Погода Москва).\n"
            "3. Показывать курсы валют (кнопка).\n"
            "4. Ставить напоминания (напиши: Напомни через 5 минут ...).\n"
            "5. Кнопки в меню — просто нажимай!",
            parse_mode="Markdown"
        )
    elif data == "gigachat":
        await query.edit_message_text("Напиши свой вопрос, и я передам его GigaChat.")
        context.user_data['awaiting'] = 'gigachat'

# --- ОБРАБОТЧИК ТЕКСТА ---
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    chat_id = update.effective_chat.id
    awaiting = context.user_data.get('awaiting')

    # 1. Обработка ожидания города для погоды
    if awaiting == 'weather_city':
        context.user_data['awaiting'] = None
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        weather = await get_weather(text)
        await update.message.reply_text(f"🌤 Погода в {text}:\n{weather}")
        return

    # 2. Обработка напоминания
    if awaiting == 'reminder_text' or text.lower().startswith("напомни"):
        context.user_data['awaiting'] = None
        parts = text.lower().split()
        delay = 5
        reminder_text = text

        if "через" in parts:
            idx = parts.index("через")
            if idx + 2 < len(parts):
                try:
                    delay = int(parts[idx + 1])
                    unit = parts[idx + 2]
                    if "час" in unit:
                        delay *= 60
                    elif "сек" in unit:
                        delay = max(1, delay // 60)
                    reminder_text = " ".join(parts[idx + 3:]) or "Напоминание"
                except ValueError:
                    delay = 5

        context.job_queue.run_once(
            send_reminder,
            delay * 60,
            data={"chat_id": chat_id, "text": reminder_text}
        )
        await update.message.reply_text(f"✅ Напоминание установлено через {delay} мин.: {reminder_text}")
        return

    # 3. Обычный запрос к GigaChat
    client = context.bot_data.get('gigachat_client')
    if not client:
        await update.message.reply_text("Клиент GigaChat не инициализирован.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        chat_request = Chat(
            model="GigaChat-2",
            messages=[
                Messages(role=MessagesRole.USER, content=text)
            ]
        )
        response = client.chat(chat_request)

        if response and hasattr(response, 'choices') and response.choices:
            answer = response.choices[0].message.content
            await update.message.reply_text(
                f"🤖 <b>Ответ GigaChat:</b>\n\n{answer}",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("Я получил пустой ответ от нейросети.")

    except Exception as e:
        logging.error(f"Ошибка GigaChat: {e}")
        await update.message.reply_text(f"Произошла ошибка:\n{e}")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    await context.bot.send_message(
        chat_id=job.data['chat_id'],
        text=f"⏰ *Напоминание:* {job.data['text']}",
        parse_mode="Markdown"
    )

# --- ГЛАВНАЯ ФУНКЦИЯ ---
async def main():
    if not TOKEN or not GIGACHAT_KEY:
        logging.error("FATAL: Токены не найдены в .env")
        return

    # Запускаем фиктивный веб-сервер в отдельном потоке для Render
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("✅ Фиктивный веб-сервер запущен для Render.")

    giga_client = GigaChat(
        scope="GIGACHAT_API_PERS", 
        credentials=GIGACHAT_KEY,
        verify_ssl_certs=False
    )

    application = Application.builder().token(TOKEN).build()
    application.bot_data['gigachat_client'] = giga_client

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    print("✅ Бот готов к работе.")
    
    loop = asyncio.get_event_loop()
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    
    print("✅ Бот в сети. Жду сообщений...")
    await asyncio.Event().wait()

    await application.updater.stop()
    await application.stop()
    loop.close()

if __name__ == "__main__":
    asyncio.run(main())
