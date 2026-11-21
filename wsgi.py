import os
from bot import main

# Имя функции должно быть 'main', чтобы gunicorn мог ее найти
def app():
    return main()

if __name__ == "__main__":
    # Теперь рендер найдет функцию 'app'
    import asyncio
    asyncio.run(app())
