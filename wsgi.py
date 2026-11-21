import asyncio
from bot import main

# Создаем асинхронное приложение для Gunicorn
async def async_app():
    # Запускаем твою основную логику бота
    await main()

# Эта функция нужна Gunicorn. Она будет запускать асинхронный код.
def wsgi_app(environ, start_response):
    # Здесь создается цикл событий, который запустит async_app
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(async_app())
    finally:
        loop.close()

if __name__ == "__main__":
    # Для локального запуска
    import uvicorn
    uvicorn.run(wsgi_app, host="0.0.0.0", port=8000)
