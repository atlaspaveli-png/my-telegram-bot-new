# run_flask.py

from flask import Flask
import os

flask_app = Flask(__name__)

@flask_app.route('/')
def health():
    # Этот URL Render будет проверять каждые 30 секунд
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    # host="0.0.0.0" обязательно, чтобы рендер мог достучаться
    flask_app.run(host="0.0.0.0", port=port)