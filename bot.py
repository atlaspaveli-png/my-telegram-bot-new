import logging
import os
import asyncio
from dotenv import load_dotenv
from telegram import Update, ForceReply
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

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
print("СТАРТ: Бот V-FINAL (Python 3.11)")
print("=" * 60)


# --- ОБРАБОТЧИК КОМАНДЫ /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Привет, {name}! 👋\n"
        f"Я бот V-Final. Я живу на Python 3.11.\n\n"
        "Напиши мне что-нибудь, и я отвечу.",
        reply_markup=ForceReply(selective=True),
    )

# --- ОБРАБОТЧИК СООБЩЕНИЙ ---
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    chat_id = update.effective_chat.id

    # Достаем клиента из "рюкзака" бота
    client = context.bot_data.get('gigachat_client')
    if not client:
        await update.message.reply_text("Клиент GigaChat не инициализирован.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        # --- ИСПРАВЛЕННЫЙ БЛОК ЗАПРОСА ---
        # Создаем объект Chat с актуальной моделью
        chat_request = Chat(
            model="GigaChat-2",  # <-- ИСПРАВЛЕНИЕ: заменили "GigaChat" на "GigaChat-2"
            messages=[
                Messages(role=MessagesRole.USER, content=text)
            ]
        )
        
        # Отправляем запрос в GigaChat
        response = client.chat(chat_request)
        
        # Проверяем, пришел ли ответ и есть ли в нем текст
        if response and hasattr(response, 'choices') and response.choices:
            answer = response.choices[0].message.content
            await update.message.reply_text(
                f"🤖 <b>Ответ GigaChat:</b>\n\n{answer}",
                parse_mode="HTML"
            )
        else:
            print(f"WARNING: GigaChat вернул пустой ответ. Response: {response}")
            await update.message.reply_text("Я получил пустой ответ от нейросети.")

    except Exception as e:
        logging.error(f"Ошибка GigaChat: {e}")
        await update.message.reply_text(
            f"Произошла ошибка при генерации ответа:\n{e}"
        )

# --- ГЛАВНАЯ ФУНКЦИЯ (Windows-friendly) ---
async def main():
    if not TOKEN or not GIGACHAT_KEY:
        logging.error("FATAL: Токены не найдены в .env")
        return

    # Создаем клиента GigaChat ОДИН раз при старте
    giga_client = GigaChat(
        scope="GIGACHAT_API_PERS", 
        credentials=GIGACHAT_KEY,
        verify_ssl_certs=False
    )

    application = Application.builder().token(TOKEN).build()
    
    # Кладем клиента в "рюкзак" бота, чтобы функция echo могла его достать
    application.bot_data['gigachat_client'] = giga_client

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    print("✅ Бот готов к работе.")
    
    # Ручной цикл для обхода ошибок Python 3.11/3.14 в Windows
    loop = asyncio.get_event_loop()
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    
    print("✅ Бот в сети. Жду сообщений...")
    await asyncio.Event().wait() # Держим программу включенной

    await application.updater.stop()
    await application.stop()
    loop.close()

if __name__ == "__main__":
    # Запускаем асинхронную функцию
    asyncio.run(main())
