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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# --- Системный промпт (Твоя личность бота) ---
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

# --- FLASK ДЛЯ RENDER (Keep-alive) ---
flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (Погода и Валюта) ---
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
    # Полная очистка данных пользователя при старте
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
    
    await update.message.reply_text(
        f"Привет, {name}! Я твой персональный ассистент.\n\n"
        f"Я сохраняю историю диалога и подстраиваюсь под тебя. "
        f"Выбери кнопку или просто напиши мне.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "weather":
        await query.edit_message_text("Напиши город, для которого нужна погода (например: Москва)")
        context.user_data['awaiting'] = 'weather_city'
    elif data == "currency":
        await query.edit_message_text(await get_currency())
    elif data == "remind":
        await query.edit_message_text("Напиши: 'Напомни через 10 минут проверить код'")
        context.user_data['awaiting'] = 'reminder_text'
    elif data == "help":
        await query.edit_message_text("📖 Я умею:\n• Отвечать на вопросы (с памятью!)\n• Показывать погоду и курсы\n• Ставить напоминания\n• Работать с GigaChat", parse_mode="Markdown")
    elif data == "gigachat":
        await query.edit_message_text("Напиши свой вопрос. Я запомню контекст нашего диалога.")
        context.user_data['awaiting'] = 'gigachat'

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    chat_id = update.effective_chat.id
    awaiting = context.user_data.get('awaiting')

    # --- 1. Обработка спец. команд (Погода, Напоминания) ---
    if awaiting == 'weather_city':
        context.user_data['awaiting'] = None
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        await update.message.reply_text(f"🌤 Погода в {text}:\n{await get_weather(text)}")
        return

    if awaiting == 'reminder_text' or text.lower().startswith("напомни"):
        context.user_data['awaiting'] = None
        # Простая логика напоминания (можно усложнить)
        try:
            parts = text.lower().replace('напомни', '').strip().split()
            if 'через' in parts:
                idx = parts.index('через')
                delay = int(parts[idx + 1])
                unit = parts[idx + 2]
                if 'час' in unit: delay *= 60
                reminder_msg = " ".join(parts[idx + 3:]) or "Напоминание"
                context.job_queue.run_once(send_reminder, delay * 60, data={"chat_id": chat_id, "text": reminder_msg})
                await update.message.reply_text(f"✅ Напомню через {delay} мин: {reminder_msg}")
            else:
                await update.message.reply_text("Я не понял время. Напиши: 'Напомни через 5 минут Текст'")
        except Exception:
            await update.message.reply_text("Ошибка в формате. Пример: Напомни через 10 минут выпить воды")
        return

    # --- 2. Инициализация GigaChat (Один раз на бота) ---
    if 'gigachat_client' not in context.bot_data:
        try:
            context.bot_data['gigachat_client'] = GigaChat(
                scope="GIGACHAT_API_PERS",
                credentials=GIGACHAT_KEY,
                verify_ssl_certs=False
            )
            logging.info("✅ GigaChat клиент создан и сохранен в bot_data.")
        except Exception as e:
            logging.error(f"FATAL: Не удалось создать клиента GigaChat: {e}")
            await update.message.reply_text("❌ Ошибка подключения к GigaChat. Попробуй позже.")
            return

    # --- 3. Подготовка истории (Гарантированная защита от KeyError) ---
    if 'history' not in context.user_data:
        context.user_data['history'] = []

    # --- 4. Отправка статуса "печатает" ---
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # --- 5. Формирование запроса ---
    client = context.bot_data['gigachat_client']
    messages = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
    
    # Берем последние 20 сообщений, проверяя их целостность
    for msg in context.user_data['history'][-20:]:
        if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
            messages.append(Messages(role=msg['role'], content=msg['content']))
            
    messages.append(Messages(role=MessagesRole.USER, content=text))

    # --- 6. Запрос к API и ответ ---
    try:
        chat_request = Chat(model="GigaChat-2", messages=messages)
        response = client.chat(chat_request)

        # Проверка, что ответ вообще существует и не пустой
        if response and response.choices and response.choices[0].message.content:
            answer = response.choices[0].message.content
            
            # Сохраняем в историю
            context.user_data['history'].append({'role': MessagesRole.USER, 'content': text})
            context.user_data['history'].append({'role': MessagesRole.ASSISTANT, 'content': answer})
            
            # Ограничиваем размер истории, чтобы не переполнить контекст
            if len(context.user_data['history']) > 40:
                context.user_data['history'] = context.user_data['history'][-40:]

            await update.message.reply_text(f"🤖 <b>Ответ:</b>\n\n{answer}", parse_mode="HTML")
        else:
            logging.warning("GigaChat вернул пустой ответ.")
            await update.message.reply_text("Нейросеть ответила пустотой. Попробуй переформулировать вопрос.")
            
    except Exception as e:
        logging.error(f"Ошибка при запросе к GigaChat: {e}")
        await update.message.reply_text(f"⚠️ Произошла ошибка связи с GigaChat:\n{e}")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    await context.bot.send_message(chat_id=job.data['chat_id'], text=f"⏰ *Напоминание:* {job.data['text']}", parse_mode="Markdown")

# --- ГЛАВНАЯ ТОЧКА ЗАПУСКА ---
async def main():
    if not TOKEN or not GIGACHAT_KEY:
        logging.error("FATAL: Токены не найдены в переменных окружения.")
        return

    threading.Thread(target=run_flask, daemon=True).start()

    persistence = PicklePersistence(filepath="bot_persistence.pkl")
    
    application = ApplicationBuilder()\
        .token(TOKEN)\
        .persistence(persistence)\
        .concurrent_updates(True)\
        .build()

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
    print("✅ Бот в сети. Память сохраняется в bot_persistence.pkl.")

    await stop_event.wait()

    await application.updater.stop()
    await application.stop()
    await application.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
