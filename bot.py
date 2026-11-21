import logging
import os
import asyncio
import aiohttp
import threading
import signal
from flask import Flask
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters,
    CallbackQueryHandler, PicklePersistence
)
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

load_dotenv()
TOKEN = os.environ.get("TELEGRAM_TOKEN")
GIGACHAT_KEY = os.environ.get("GIGACHAT_API_KEY")

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Системный промпт для GigaChat
SYSTEM_PROMPT = """Твоя роль: Ты — высококвалифицированный и проактивный ассистент...
"""

# Flask для Render
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

# Вспомогательные функции погоды и курса валют
async def get_weather(city: str) -> str:
    ...

async def get_currency() -> str:
    ...

# Обработчики команд и сообщений

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name
    context.user_data.clear()  # Очистка истории при старте
    context.user_data['awaiting'] = None
    context.user_data['history'] = []

    keyboard = [
        [InlineKeyboardButton("🌤 Погода", callback_data="weather"),
         InlineKeyboardButton("💱 Курс валют", callback_data="currency")],
        [InlineKeyboardButton("⏰ Напоминание", callback_data="remind"),
         InlineKeyboardButton("❓ Помощь", callback_data="help")],
        [InlineKeyboardButton("🤖 Спросить GigaChat", callback_data="gigachat")]
    ]

    await update.message.reply_text(
        f"Привет, {name}! Выбери действие или напиши текст.",
        reply_markup=InlineKeyboardMarkup(keyboard)
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
        await query.edit_message_text("📖 Отвечаю на вопросы (с памятью!), показываю погоду, курсы, ставлю напоминания.")
    elif data == "gigachat":
        await query.edit_message_text("Напиши свой вопрос для GigaChat. Я запомню наш диалог.")
        context.user_data['awaiting'] = 'gigachat'

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    chat_id = update.effective_chat.id

    # Проверка ожидаемого состояния
    awaiting = context.user_data.get('awaiting')

    if awaiting == 'weather_city':
        city = text
        context.user_data['awaiting'] = None
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        weather = await get_weather(city)
        await update.message.reply_text(f"🌤 {city}:\n{weather}")
        return

    if awaiting == 'reminder_text' or text.lower().startswith("напомни"):
        context.user_data['awaiting'] = None
        # Логика установки напоминания
        ...

    # Создание клиента GigaChat при первом обращении
    client = context.bot_data.get('gigachat_client')
    if not client:
        try:
            client = GigaChat(scope="GIGACHAT_API_PERS", credentials=GIGACHAT_KEY, verify_ssl_certs=False)
            context.bot_data['gigachat_client'] = client
            logging.info("✅ GigaChat клиент создан в обработчике echo.")
        except Exception as e:
            logging.error(f"Не удалось создать клиента GigaChat: {e}")
            await update.message.reply_text(f"Ошибка инициализации GigaChat: {e}")
            return

    # Отправляем сигнал набора текста
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # Формируем массив сообщений для передачи в GigaChat
    messages = [
        Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)
    ]

    # Добавляем последние 20 сообщений из истории диалога
    history = context.user_data.get('history', [])
    for msg_dict in history[-20:]:
        messages.append(Messages(role=msg_dict['role'], content=msg_dict['content']))

    # Добавляем текущее сообщение пользователя
    messages.append(Messages(role=MessagesRole.USER, content=text))

    # Запрашиваем ответ от GigaChat
    try:
        chat_request = Chat(model="GigaChat-2", messages=messages)
        response = client.chat(chat_request)

        if response and response.choices:
            answer = response.choices[0].message.content

            # Сохраняем сообщение пользователя и ответ ассистента в историю
            context.user_data['history'].append({'role': MessagesRole.USER, 'content': text})
            context.user_data['history'].append({'role': MessagesRole.ASSISTANT, 'content': answer})

            # Ограничиваем длину истории до 40 сообщений
            if len(context.user_data['history']) > 40:
                context.user_data['history'] = context.user_data['history'][-40:]

            # Отправляем ответ пользователю
            await update.message.reply_text(f"🤖 <b>Ответ:</b>\n\n{answer}", parse_mode="HTML")
        else:
            await update.message.reply_text("Пустой ответ от нейросети.")
    except Exception as e:
        logging.error(f"GigaChat error: {e}")
        await update.message.reply_text(f"Ошибка:\n{e}")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    await context.bot.send_message(chat_id=job.data['chat_id'], text=f"⏰ *Напоминание:* {job.data['text']}", parse_mode="Markdown")

# Основная функция запуска бота
async def main():
    if not TOKEN or not GIGACHAT_KEY:
        logging.error("FATAL: Токены не найдены.")
        return

    # Запускаем Flask-сервер в отдельном потоке
    threading.Thread(target=run_flask, daemon=True).start()
    print("✅ Flask-сервер для Render запущен.")

    # Создаем экземпляр Persistence для сохранения состояния между перезапусками
    persistence = PicklePersistence(filepath="bot_persistence.pkl")

    # Создаем и настраиваем приложение Telegram
    app = ApplicationBuilder() \
        .token(TOKEN) \
        .persistence(persistence) \
        .concurrent_updates(True) \
        .build()

    # Добавляем обработчики команд и сообщений
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Сигнал остановки приложения
    stop_event = asyncio.Event()
    def handle_sigterm():
        logging.info("Получен SIGTERM, останавливаю...")
        stop_event.set()
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, handle_sigterm)
    except NotImplementedError:
        signal.signal(signal.SIGTERM, lambda s, f: handle_sigterm())

    # Запуск бота
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    print("✅ Бот в сети. Память сохраняется в bot_persistence.pkl.")

    await stop_event.wait()

    print("Останавливаю...")
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    print("✅ Бот остановлен.")

if __name__ == "__main__":
    asyncio.run(main())
