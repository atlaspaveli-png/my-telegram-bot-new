import os
from bot import main

if __name__ == "__main__":
    # Рендер поймет эту точку входа и запустит бота постоянно
    import asyncio
    asyncio.run(main())