import os
from dotenv import load_dotenv

load_dotenv()

# Токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ID чатов (ЗАМЕНИТЕ НА СВОИ!)
MODERATION_CHAT_ID = -1004215820695   # Чат для модерации
MARKET_CHANNEL_ID = -1004382971748   # Канал с объявлениями

# ID групп для сбора объявлений (ДОБАВЬ СВОЙ ID НОВОЙ ГРУППЫ)
COLLECTION_GROUPS = {
    -5147960141: "main",   # Основная группа (ЗАМЕНИ НА СВОЙ)
  # -1003829119548: "new",     # НОВАЯ группа (ВСТАВЬ СВОЙ ID)
}

# ID модераторов (ЗАМЕНИТЕ НА СВОИ)
MODERATOR_IDS = [1364254252, 8324957143, 8075221689]

# Ограничения
MAX_MESSAGE_LENGTH = 1000
MAX_POSTS_PER_DAY = 3
POST_COOLDOWN_HOURS = 2
FLOOD_LIMIT = 3
FLOOD_PERIOD = 60  # секунд
BLOCK_DURATION = 3600  # 1 час в секундах

# Ключевые слова для группы
GROUP_KEYWORDS = [
    "куплю", "продам", "продаю", "продажа",
    "обмен", "обменяю", "отдам", "цена",
]
