import logging
import os
import asyncio
from datetime import timedelta
from dotenv import load_dotenv

# Импорты из python-telegram-bot
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler,
    CallbackQueryHandler, PicklePersistence, filters
)

# Импорты GigaChat
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

# --- Вспомогательные функции (Погода и Валюта) ---
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
        text = f"⏰ Напиши текст напоминания.\n\nПример:\nНапомнить мне через 5 минут позвонить маме."
        await query.edit_message_text(text)
        context.user_data['awaiting'] = {'type': 'reminder_setup'}
        
    elif data == "help":
        help_text = """📖 Я умею:
• Отвечать на вопросы (с памятью!)
• Показывать погоду 🌤
• Курсы валют 💱
• Ставить напоминания ⏰
• Работать с GigaChat 🤖"""
        await query.edit_message_text(help_text, parse_mode='Markdown')
        
    elif data == "gigachat":
        await query.edit_message_text("❓ Задай свой вопрос здесь.")
        context.user_data['awaiting'] = {'type': 'free_question'}

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    chat_id = update.effective_chat.id
    awaiting_info = context.user_data.get('awaiting')

    # --- 1. Проверка на ввод данных после кнопок ---
    if awaiting_info and awaiting_info.get('type') == 'city_input':
        city = user_text.strip()
        weather_msg = await get_weather(city)
        await update.message.reply_html(f"<b>Погода в {city}:</b>\n{weather_msg}")
        context.user_data.pop('awaiting', None)
        return
        
    if awaiting_info and awaiting_info.get('type') == 'reminder_setup':
        reminder_parts = user_text.lower().split()
        time_idx = next((i for i, w in enumerate(reminder_parts) if w.isdigit()), None)
        
        if not time_idx or len(reminder_parts) < time_idx+2:
            await update.message.reply_text("Не понял формат времени. Пример: Напомни через 15 минут проверить почту")
            return
            
        delay_min = int(reminder_parts[time_idx])
        unit_word = reminder_parts[time_idx+1]
        multiplier = 60 if unit_word.endswith(('час', 'часа')) else 1
        
        final_delay = delay_min * multiplier
        reminder_text = " ".join(reminder_parts[time_idx+2:])
        if not reminder_text:
            reminder_text = "Ваше напоминание"
            
        job_context = {"chat_id": chat_id, "text": reminder_text}
        context.job_queue.run_once(send_reminder, final_delay, data=job_context)
        await update.message.reply_html(f"✅ Готово! Напомню вам через <b>{final_delay} мин</b>: {reminder_text}")
        context.user_data.pop('awaiting', None)
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

    # --- 3. Отправка статуса "печатает" ---
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # --- 4. Формирование запроса ---
    client = context.bot_data['gigachat_client']
    messages = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
    
    history_limit = min(len(context.user_data['history']), 20)
    recent_history = context.user_data['history'][-history_limit:]
    
    for msg_dict in reversed(recent_history):
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
                messages.insert(-1, Messages(role=mapped_role, content=content)) 

    messages.append(Messages(role=MessagesRole.USER, content=user_text))

    # --- 5. Запрос к API и ответ ---
    try:
        response = client.chat(Chat(model="GigaChat-2", messages=messages))
        
        answer_content = ""
        if response and response.choices and response.choices[0].message.content:
            answer_content = response.choices[0].message.content
            
            context.user_data['history'].append({'role':'user','content':user_text})
            context.user_data['history'].append({'role':'assistant','content':answer_content})
            
            if len(context.user_data['history']) > 40:
                context.user_data['history'] = context.user_data['history'][-40:]
                
            await update.message.reply_html(f"<b>Ответ:</b>\n\n{answer_content}")
        else:
            logging.warning("GigaChat вернул пустой ответ.")
            await update.message.reply_text("Нейросеть ответила пустотой. Попробуй переформулировать вопрос.")
            
    except Exception as e:
        error_msg = str(e).replace("\n", " ").replace("\r", "")
        await update.message.reply_text(f"⚠️ Ошибка связи с GigaChat:\n<code>{error_msg}</code>", parse_mode='HTML')

async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    await context.bot.send_message(chat_id=job.data['chat_id'], text=f"⏰ *Напоминание:* {job.data['text']}", parse_mode='Markdown')
