import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Callable, Awaitable, List
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from config import *
from database import *
from messages import *

# ---------------- ЛОГИРОВАНИЕ ----------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# ---------------- DATACLASSES ----------------
@dataclass
class SimpleUser:
    """Простой класс пользователя для безопасного использования"""
    id: int
    username: str = ""
    full_name: str = ""
    
    @property
    def display_name(self) -> str:
        if self.username:
            return f"@{self.username}"
        return f"ID:{self.id} ({self.full_name})"
    
    @property
    def contact_url(self) -> str:
        if self.username:
            return f"https://t.me/{self.username}"
        return f"tg://user?id={self.id}"

@dataclass
class LimitCheckConfig:
    """Конфигурация проверки лимитов"""
    skip_verification: bool = False
    delete_on_fail: bool = False
    skip_flood: bool = False
    skip_post_limit: bool = False

# ---------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------------
def has_trade_keywords(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    result = any(keyword in text_lower for keyword in GROUP_KEYWORDS)
    logger.info(f"Проверка текста на ключевые слова: '{text[:50]}...' -> {result}")
    return result

def get_user_display(user) -> str:
    if user.username:
        return f"@{user.username}"
    return f"ID:{user.id} ({user.full_name})"

def get_user_contact(user) -> str:
    if user.username:
        return f"https://t.me/{user.username}"
    return f"tg://user?id={user.id}"

# ---------------- RATE LIMITER ДЛЯ CALLBACK'ОВ ----------------
class CallbackLimiter:
    def __init__(self):
        self.callbacks: Dict[int, List[float]] = defaultdict(list)
    
    def check(self, user_id: int, limit: int = 10, period: int = 60) -> bool:
        now = time.time()
        self.callbacks[user_id] = [
            t for t in self.callbacks[user_id] 
            if now - t < period
        ]
        
        if len(self.callbacks[user_id]) >= limit:
            return False
        
        self.callbacks[user_id].append(now)
        return True

# ---------------- Middleware для логирования ----------------
class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        user = event.from_user
        logger.info(f"Обработка события от {user.id} в чате {event.chat.id}")
        
        try:
            result = await handler(event, data)
            return result
        except Exception as e:
            logger.error(f"Ошибка в обработчике: {e}", exc_info=True)
            if hasattr(event, 'chat') and event.chat.type == "private":
                await event.answer(ERROR_MESSAGE)
            raise

dp.message.middleware(LoggingMiddleware())

# ---------------- ФУНКЦИЯ ДЛЯ УДАЛЕНИЯ СООБЩЕНИЙ С ЗАДЕРЖКОЙ ----------------
async def delete_message_after_delay(message: Message, delay_seconds: int):
    """Удаляет сообщение через указанное количество секунд"""
    await asyncio.sleep(delay_seconds)
    try:
        await message.delete()
        logger.info(f"Сообщение {message.message_id} удалено через {delay_seconds} сек")
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение {message.message_id}: {e}")

# ---------------- АНТИ-СПАМ С ОЧИСТКОЙ КЭША ----------------
class AntiSpam:
    def __init__(self, max_cache_size: int = 10000):
        self.user_messages: Dict[int, List[datetime]] = defaultdict(list)
        self.user_posts: Dict[int, List[datetime]] = defaultdict(list)
        self.blocked_users: Dict[int, datetime] = {}
        self.max_cache_size = max_cache_size
        self._bot_start_time = datetime.now()

    def _clean_overflow(self):
        if len(self.user_messages) > self.max_cache_size:
            try:
                oldest_users = sorted(
                    self.user_messages.items(),
                    key=lambda x: min(x[1]) if x[1] else datetime.min
                )[:len(self.user_messages) - self.max_cache_size]
                
                for user_id, _ in oldest_users:
                    if user_id in self.user_messages:
                        del self.user_messages[user_id]
                    if user_id in self.user_posts:
                        del self.user_posts[user_id]
            except Exception as e:
                logger.warning(f"Ошибка при очистке кэша: {e}")

    def get_uptime(self) -> str:
        delta = datetime.now() - self._bot_start_time
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        
        parts = []
        if days > 0:
            parts.append(f"{days}д")
        if hours > 0:
            parts.append(f"{hours}ч")
        if minutes > 0:
            parts.append(f"{minutes}м")
        
        return " ".join(parts) if parts else "только что"

    def clean_old_data(self):
        now = datetime.now()
        
        for user_id in list(self.user_messages.keys()):
            self.user_messages[user_id] = [
                t for t in self.user_messages[user_id] 
                if now - t < timedelta(minutes=10)
            ]
            if not self.user_messages[user_id]:
                del self.user_messages[user_id]
        
        for user_id in list(self.user_posts.keys()):
            self.user_posts[user_id] = [
                t for t in self.user_posts[user_id] 
                if now - t < timedelta(hours=24)
            ]
            if not self.user_posts[user_id]:
                del self.user_posts[user_id]
        
        for user_id in list(self.blocked_users.keys()):
            if now >= self.blocked_users[user_id]:
                del self.blocked_users[user_id]
        
        self._clean_overflow()

    def is_blocked(self, user_id: int) -> bool:
        if user_id in self.blocked_users:
            if datetime.now() < self.blocked_users[user_id]:
                return True
            del self.blocked_users[user_id]
        return False

    def get_block_time(self, user_id: int) -> str:
        if user_id not in self.blocked_users:
            return "0 сек"
        delta = self.blocked_users[user_id] - datetime.now()
        h, m, s = delta.seconds // 3600, (delta.seconds % 3600) // 60, delta.seconds % 60
        parts = [f"{v} {l}" for v, l in [(h, "ч"), (m, "мин"), (s, "сек")] if v > 0]
        return " ".join(parts) if parts else "0 сек"

    def check_flood(self, user_id: int, limit: int = FLOOD_LIMIT, period: int = FLOOD_PERIOD) -> bool:
        now = datetime.now()
        self.user_messages[user_id] = [t for t in self.user_messages[user_id] if now - t < timedelta(seconds=period)]
        self.user_messages[user_id].append(now)
        if len(self.user_messages[user_id]) > limit:
            self.blocked_users[user_id] = now + timedelta(seconds=BLOCK_DURATION)
            logger.warning(f"Пользователь {user_id} заблокирован за флуд на {BLOCK_DURATION//3600} час")
            return True
        return False

    def check_post_limit(self, user_id: int, limit: int = MAX_POSTS_PER_DAY, hours: int = POST_COOLDOWN_HOURS) -> bool:
        now = datetime.now()
        self.user_posts[user_id] = [t for t in self.user_posts[user_id] if now - t < timedelta(hours=24)]
        if len(self.user_posts[user_id]) >= limit:
            logger.info(f"Пользователь {user_id} превысил лимит постов за 24 часа")
            return True
        if self.user_posts[user_id] and now - self.user_posts[user_id][-1] < timedelta(hours=hours):
            logger.info(f"Пользователь {user_id} слишком часто постит (интервал меньше {hours} часов)")
            return True
        return False

    def get_remaining_posts(self, user_id: int) -> int:
        self.user_posts[user_id] = [t for t in self.user_posts[user_id] if datetime.now() - t < timedelta(hours=24)]
        return MAX_POSTS_PER_DAY - len(self.user_posts[user_id])

    def get_next_post_time(self, user_id: int) -> str:
        if not self.user_posts[user_id]:
            return "сейчас"
        next_time = self.user_posts[user_id][-1] + timedelta(hours=POST_COOLDOWN_HOURS)
        if next_time <= datetime.now():
            return "сейчас"
        delta = next_time - datetime.now()
        h, m = delta.seconds // 3600, (delta.seconds % 3600) // 60
        return f"через {h} ч {m} мин" if h > 0 else f"через {m} мин"

    def add_post(self, user_id: int):
        self.user_posts[user_id].append(datetime.now())
        logger.info(f"Пользователь {user_id} добавил пост. Всего постов за 24ч: {len(self.user_posts[user_id])}")

anti_spam = AntiSpam()
callback_limiter = CallbackLimiter()

# ---------------- ВЕРИФИКАЦИЯ ----------------
class Verification:
    def __init__(self):
        self.verified_users = set()

    def is_verified(self, user_id: int) -> bool:
        return user_id in self.verified_users

    def verify_user(self, user_id: int):
        self.verified_users.add(user_id)
        logger.info(f"Пользователь {user_id} верифицирован")

verification = Verification()

# ---------------- ФУНКЦИЯ ДЛЯ ВРЕМЕНИ ОЖИДАНИЯ ----------------
def get_wait_time_text(user_id: int) -> str:
    if not anti_spam.user_posts[user_id]:
        return "✅ можно отправлять сейчас"
    
    now = datetime.now()
    last_post = anti_spam.user_posts[user_id][-1]
    wait_until = last_post + timedelta(hours=POST_COOLDOWN_HOURS)
    
    if now >= wait_until:
        return "✅ можно отправлять сейчас"
    
    delta = wait_until - now
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    
    if hours > 0:
        return f"⏰ осталось ждать {hours} ч {minutes} мин"
    else:
        return f"⏰ осталось ждать {minutes} мин"

# ---------------- КНОПКИ ----------------
def get_verify_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, мне есть 21 год", callback_data="verify_age")],
        [InlineKeyboardButton(text="❌ Нет, мне нет 21 года", callback_data="verify_reject")]
    ])

def mod_kb(post_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{post_id}"),
         InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{post_id}")]
    ])

def post_kb(user: SimpleUser):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Написать продавцу", url=user.contact_url)],
        [InlineKeyboardButton(text="📝 Подать объявление", url="https://t.me/krd_vapebot")],
        [InlineKeyboardButton(text="📢 Реклама", url="https://t.me/a_operay")]
    ])

# ---------------- ПРОВЕРКА ЛИМИТОВ ДЛЯ ЛИЧКИ ----------------
async def check_user_limits(message: Message, config: Optional[LimitCheckConfig] = None) -> bool:
    """Проверяет все лимиты пользователя. Возвращает True если можно постить"""
    if config is None:
        config = LimitCheckConfig()
    
    user_id = message.from_user.id
    
    async def delete_and_warn(warning_text: str):
        """Отправляет предупреждение и удаляет сообщение если нужно"""
        if config.delete_on_fail:
            try:
                await message.delete()
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение: {e}")
        
        try:
            await message.answer(warning_text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Не удалось отправить предупреждение: {e}")
    
    if not config.skip_verification:
        if not verification.is_verified(user_id):
            logger.info(f"Пользователь {user_id} не верифицирован")
            await delete_and_warn(WELCOME_UNVERIFIED)
            return False
    
    if not config.skip_flood:
        if anti_spam.is_blocked(user_id):
            block_time = anti_spam.get_block_time(user_id)
            logger.info(f"Пользователь {user_id} заблокирован")
            await delete_and_warn(BLOCK_WARNING.format(block_time=block_time))
            return False
    
    if not config.skip_flood:
        if anti_spam.check_flood(user_id):
            block_time = anti_spam.get_block_time(user_id)
            logger.info(f"Пользователь {user_id} заблокирован за флуд")
            await delete_and_warn(BLOCK_WARNING.format(block_time=block_time))
            return False
    
    if not config.skip_post_limit:
        if anti_spam.check_post_limit(user_id):
            remaining = anti_spam.get_remaining_posts(user_id)
            next_time = anti_spam.get_next_post_time(user_id)
            wait_text = get_wait_time_text(user_id)
            
            logger.info(f"Пользователь {user_id} превысил лимит постов. Осталось: {remaining}")
            
            if remaining > 0:
                warning = COOLDOWN_WARNING.format(
                    remaining=remaining,
                    max_posts=MAX_POSTS_PER_DAY,
                    wait_text=wait_text,
                    next_time=next_time
                )
            else:
                warning = LIMIT_WARNING.format(max_posts=MAX_POSTS_PER_DAY)
            
            await delete_and_warn(warning)
            return False
    
    logger.info(f"Пользователь {user_id} прошёл проверку лимитов")
    return True

# ---------------- ОТПРАВКА НА МОДЕРАЦИЮ ----------------
async def send_moderation_post(user, text: str, file_id: str = None, source_group: str = "", post_id: int = None, file_type: str = "text"):
    """Отправляет пост на модерацию С КНОПКАМИ"""
    user_display = get_user_display(user)
    source_info = f"\n📢 Из группы: {source_group}" if source_group else ""
    base_msg = f"📋 <b>Новое объявление #ID{post_id}</b>{source_info}\n\n{text}\n\n👤 {user_display}"
    
    keyboard = mod_kb(post_id) if post_id else None
    
    try:
        logger.info(f"Пытаемся отправить в чат модерации {MODERATION_CHAT_ID}")
        
        if file_type == "photo" and file_id:
            result = await bot.send_photo(MODERATION_CHAT_ID, file_id, caption=base_msg, reply_markup=keyboard, parse_mode="HTML")
        elif file_type == "video" and file_id:
            result = await bot.send_video(MODERATION_CHAT_ID, file_id, caption=base_msg, reply_markup=keyboard, parse_mode="HTML")
        elif file_type == "document" and file_id:
            result = await bot.send_document(MODERATION_CHAT_ID, file_id, caption=base_msg, reply_markup=keyboard, parse_mode="HTML")
        else:
            result = await bot.send_message(MODERATION_CHAT_ID, base_msg, reply_markup=keyboard, parse_mode="HTML")
        
        logger.info(f"✅ Пост #{post_id} успешно отправлен в чат модерации, message_id: {result.message_id}")
        return result
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА отправки в чат модерации {MODERATION_CHAT_ID}: {e}", exc_info=True)
        raise

# ---------------- ОБРАБОТЧИКИ ДЛЯ ГРУППЫ ----------------
async def handle_post_from_group(message: Message, file_id: str = None, post_type: str = "text", group_type: str = "main"):
    """Обработчик для постов из группы"""
    user = message.from_user
    user_id = user.id
    text = message.caption or message.text or ""
    source_group = message.chat.title or "Группа"
    
    logger.info(f"Обработка поста из группы ({group_type}) от {user_id}")
    
    if not text.strip():
        temp_msg = await message.answer("❌ Добавьте текст к объявлению.")
        asyncio.create_task(delete_message_after_delay(temp_msg, 600))
        return
    
    if len(text) > MAX_MESSAGE_LENGTH:
        temp_msg = await message.answer(f"❌ Текст слишком длинный!\nМаксимум {MAX_MESSAGE_LENGTH} символов.")
        asyncio.create_task(delete_message_after_delay(temp_msg, 600))
        return
    
    # Сохраняем пост в БД
    post_id = await add_post(user_id, user.username, text, file_id, post_type)
    anti_spam.add_post(user_id)
    remaining = anti_spam.get_remaining_posts(user_id)
    
    logger.info(f"Пост #{post_id} сохранён в БД. Отправляем на модерацию...")
    
    # Отправляем на модерацию
    try:
        await send_moderation_post(user, text, file_id, source_group, post_id, post_type)
        logger.info(f"✅ Пост #{post_id} успешно отправлен на модерацию")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при отправке на модерацию: {e}")
        temp_msg = await message.answer(ERROR_MESSAGE)
        asyncio.create_task(delete_message_after_delay(temp_msg, 600))
        return
    
    # Отправляем уведомление в зависимости от типа группы
    next_time = anti_spam.get_next_post_time(user_id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 ПЕРЕЙТИ В НОВЫЙ КАНАЛ", url="https://t.me/krd_vape1234")],
        [InlineKeyboardButton(text="🤖 Подать объявление через бота", url="https://t.me/krd_vapebot")]
    ])
    
    if group_type == "main":
        confirmation_text = GROUP_POST_SUBMITTED
    else:
        confirmation_text = NEW_GROUP_POST_SUBMITTED
    
    confirmation_msg = await message.reply(
        confirmation_text.format(
            remaining=remaining,
            max_posts=MAX_POSTS_PER_DAY,
            next_time=next_time
        ),
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True
    )
    
    # Удаляем сообщение бота через 10 минут
    asyncio.create_task(delete_message_after_delay(confirmation_msg, 600))
    
    logger.info(f"✅ Пост #{post_id} обработан в группе {group_type}")

# ---------------- ОБРАБОТЧИКИ ДЛЯ ЛИЧНЫХ СООБЩЕНИЙ ----------------
async def handle_post(message: Message, file_id: str = None, post_type: str = "text"):
    user = message.from_user
    user_id = user.id
    text = message.caption or message.text or ""
    
    logger.info(f"Обработка поста из ЛС от {user_id}. Текст: {text[:100]}...")
    
    if not text.strip():
        await message.answer("❌ Добавьте текст к объявлению.")
        return
    
    if len(text) > MAX_MESSAGE_LENGTH:
        await message.answer(f"❌ Текст слишком длинный!\nМаксимум {MAX_MESSAGE_LENGTH} символов.\nВаш текст: {len(text)} символов.")
        return
    
    post_id = await add_post(user_id, user.username, text, file_id, post_type)
    anti_spam.add_post(user_id)
    remaining = anti_spam.get_remaining_posts(user_id)
    
    logger.info(f"Пост #{post_id} сохранён. Отправляем на модерацию...")
    
    try:
        await send_moderation_post(user, text, file_id, "", post_id, post_type)
        logger.info(f"Пост #{post_id} отправлен на модерацию")
    except Exception as e:
        logger.error(f"Ошибка при отправке на модерацию: {e}")
        await message.answer(ERROR_MESSAGE)
        return
    
    next_time = anti_spam.get_next_post_time(user_id)
    
    await message.answer(
        POST_SUBMITTED.format(
            remaining=remaining,
            max_posts=MAX_POSTS_PER_DAY,
            next_time=next_time
        ),
        parse_mode="HTML"
    )
    logger.info(f"Ответ пользователю {user_id} отправлен")

# ---------------- ФОНОВЫЕ ЗАДАЧИ ----------------
async def clean_old_data_periodically():
    while True:
        try:
            anti_spam.clean_old_data()
            await clean_old_posts(days=30)
            logger.info("Периодическая очистка данных выполнена")
        except Exception as e:
            logger.error(f"Ошибка при очистке данных: {e}")
        await asyncio.sleep(3600)

# ---------------- START ----------------
@dp.message(Command("start"))
async def start(message: Message):
    logger.info(f"Команда /start от {message.from_user.id} в чате {message.chat.type}")
    
    if message.chat.type != "private":
        await message.answer("Бот работает только в личных сообщениях", parse_mode="HTML")
        return
        
    if not verification.is_verified(message.from_user.id):
        await message.answer(
            WELCOME_UNVERIFIED,
            reply_markup=get_verify_kb(), 
            parse_mode="HTML"
        )
        return
    
    await message.answer(
        WELCOME_VERIFIED.format(max_posts=MAX_POSTS_PER_DAY, cooldown=POST_COOLDOWN_HOURS),
        parse_mode="HTML"
    )

@dp.message(Command("rules"))
async def rules(message: Message):
    await message.answer(
        RULES.format(max_posts=MAX_POSTS_PER_DAY, cooldown=POST_COOLDOWN_HOURS),
        parse_mode="HTML"
    )

@dp.message(Command("id"))
async def get_id(message: Message):
    user = message.from_user
    chat_id = message.chat.id
    username = f"@{user.username}" if user.username else "не указан"
    
    await message.answer(
        ID_INFO.format(
            user_id=user.id,
            username=username,
            full_name=user.full_name,
            chat_id=chat_id
        ),
        parse_mode="HTML"
    )

@dp.message(Command("status"))
async def check_status(message: Message):
    user_id = message.from_user.id
    
    if not verification.is_verified(user_id):
        await message.answer("🔞 Сначала подтвердите возраст через /start")
        return
    
    remaining = anti_spam.get_remaining_posts(user_id)
    wait_text = get_wait_time_text(user_id)
    used = MAX_POSTS_PER_DAY - remaining
    
    if anti_spam.is_blocked(user_id):
        block_time = anti_spam.get_block_time(user_id)
        await message.answer(BLOCK_WARNING.format(block_time=block_time), parse_mode="HTML")
        return
    
    next_action = ""
    if remaining > 0:
        next_time = anti_spam.get_next_post_time(user_id)
        next_action = f"📌 Следующее объявление можно отправить: {next_time}\n\n💡 Просто отправьте мне фото с описанием!"
    else:
        next_action = f"⏰ Новый лимит будет доступен завтра с 00:00\n\n🌟 Подпишитесь на канал: @bottestcanal1"
    
    await message.answer(
        STATUS_TEXT.format(
            used=used,
            max_posts=MAX_POSTS_PER_DAY,
            remaining=remaining,
            wait_text=wait_text,
            next_action=next_action
        ),
        parse_mode="HTML"
    )

@dp.message(Command("stats"))
async def moderation_stats(message: Message):
    if message.from_user.id not in MODERATOR_IDS:
        await message.answer("⛔ Нет доступа")
        return
    
    stats = await get_today_stats()
    
    stats_text = "📊 <b>Статистика за сегодня</b>\n\n"
    total = 0
    
    status_names = {
        "pending": "⏳ Ожидает",
        "approved": "✅ Одобрено",
        "rejected": "❌ Отклонено"
    }
    
    for status, count in stats.items():
        status_name = status_names.get(status, status)
        stats_text += f"{status_name}: {count}\n"
        total += count
    
    stats_text += f"\n📈 Всего: {total}"
    await message.answer(stats_text, parse_mode="HTML")

@dp.message(Command("cancel"))
async def cancel_action(message: Message):
    await message.answer("✅ Нет активных действий для отмены")

@dp.message(Command("health"))
async def health_check(message: Message):
    if message.from_user.id not in MODERATOR_IDS:
        return
    
    pending_posts = await get_pending_posts_count()
    
    health_text = f"""
🤖 <b>Health Check</b>

✅ Статус: running
👥 Верифицировано: {len(verification.verified_users)}
🚫 Заблокировано: {len(anti_spam.blocked_users)}
⏳ Ожидают модерации: {pending_posts}
⏱️ Uptime: {anti_spam.get_uptime()}
💾 Кэш пользователей: {len(anti_spam.user_messages)}
    """
    
    await message.answer(health_text, parse_mode="HTML")

@dp.message(Command("metrics"))
async def get_metrics(message: Message):
    if message.from_user.id not in MODERATOR_IDS:
        return
    
    metrics = f"""
📊 <b>Метрики бота</b>

👥 Активных пользователей: {len(anti_spam.user_messages)}
💬 Сообщений в кэше: {sum(len(v) for v in anti_spam.user_messages.values())}
📝 Постов в кэше: {sum(len(v) for v in anti_spam.user_posts.values())}
    """
    
    await message.answer(metrics, parse_mode="HTML")

@dp.message(Command("test_moderation"))
async def test_moderation(message: Message):
    """Тест отправки в чат модерации (только для модераторов)"""
    if message.from_user.id not in MODERATOR_IDS:
        await message.answer("⛔ Нет доступа")
        return
    
    try:
        test_msg = await bot.send_message(
            MODERATION_CHAT_ID, 
            "🧪 Тестовое сообщение от бота\n\nЕсли вы это видите, бот успешно подключён к чату модерации!",
            parse_mode="HTML"
        )
        await message.answer(f"✅ Успешно отправлено в чат модерации!\nID сообщения: {test_msg.message_id}")
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки в чат модерации:\n```\n{e}\n```", parse_mode="HTML")

@dp.message(Command("clean"))
async def clean_group_messages(message: Message):
    """Очищает последние сообщения бота в группе (только для модераторов)"""
    if message.from_user.id not in MODERATOR_IDS:
        await message.answer("⛔ Нет доступа")
        return
    
    # Проверяем, является ли чат группой для сбора
    if message.chat.id not in COLLECTION_GROUPS:
        await message.answer("❌ Эта команда работает только в группах сбора объявлений")
        return
    
    # Удаляем команду
    await message.delete()
    
    # Проходим по последним 100 сообщениям и удаляем сообщения бота
    deleted_count = 0
    async for msg in bot.get_chat_history(message.chat.id, limit=100):
        if msg.from_user and msg.from_user.id == bot.id:
            try:
                await msg.delete()
                deleted_count += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение {msg.message_id}: {e}")
    
    # Отправляем временное уведомление
    temp_msg = await message.answer(f"✅ Удалено {deleted_count} сообщений бота")
    asyncio.create_task(delete_message_after_delay(temp_msg, 5))

# ---------------- ВЕРИФИКАЦИЯ CALLBACKS ----------------
@dp.callback_query(F.data == "verify_age")
async def verify_age(callback: CallbackQuery):
    user = callback.from_user
    verification.verify_user(user.id)
    logger.info(f"Пользователь {user.id} верифицирован")
    
    await callback.message.delete()
    await callback.message.answer(VERIFICATION_SUCCESS, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "verify_reject")
async def verify_reject(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(VERIFICATION_FAIL)
    await callback.answer()

# ---------------- ОБРАБОТЧИКИ СООБЩЕНИЙ ДЛЯ ЛИЧКИ ----------------
@dp.message(F.text, F.chat.type == "private")
async def text_post_private(message: Message):
    logger.info(f"Получен текст в ЛС от {message.from_user.id}: {message.text[:50]}...")
    
    if message.text and not message.text.startswith("/"):
        if not verification.is_verified(message.from_user.id):
            await message.answer("🔞 <b>Требуется подтверждение возраста</b>\n\nИспользуйте /start для верификации", parse_mode="HTML")
            return
        
        config = LimitCheckConfig()
        if not await check_user_limits(message, config):
            return
        await handle_post(message)

@dp.message(F.photo, F.chat.type == "private")
async def photo_post_private(message: Message):
    logger.info(f"Получено фото в ЛС от {message.from_user.id}")
    
    if not verification.is_verified(message.from_user.id):
        await message.answer("🔞 <b>Требуется подтверждение возраста</b>\n\nИспользуйте /start для верификации", parse_mode="HTML")
        return
    
    config = LimitCheckConfig()
    if not await check_user_limits(message, config):
        return
    await handle_post(message, message.photo[-1].file_id, "photo")

@dp.message(F.video, F.chat.type == "private")
async def video_post_private(message: Message):
    logger.info(f"Получено видео в ЛС от {message.from_user.id}")
    
    if not verification.is_verified(message.from_user.id):
        await message.answer("🔞 <b>Требуется подтверждение возраста</b>\n\nИспользуйте /start для верификации", parse_mode="HTML")
        return
    
    config = LimitCheckConfig()
    if not await check_user_limits(message, config):
        return
    await handle_post(message, message.video.file_id, "video")

@dp.message(F.document, F.chat.type == "private")
async def document_post_private(message: Message):
    logger.info(f"Получен документ в ЛС от {message.from_user.id}")
    
    if not verification.is_verified(message.from_user.id):
        await message.answer("🔞 <b>Требуется подтверждение возраста</b>\n\nИспользуйте /start для верификации", parse_mode="HTML")
        return
    
    config = LimitCheckConfig()
    if not await check_user_limits(message, config):
        return
    await handle_post(message, message.document.file_id, "document")

# ---------------- ОБРАБОТЧИКИ ДЛЯ ВСЕХ ГРУПП ИЗ COLLECTION_GROUPS ----------------
@dp.message(F.chat.type.in_(["group", "supergroup"]))
async def all_group_messages(message: Message):
    """Обработчик ВСЕХ сообщений в группах из COLLECTION_GROUPS"""
    
    chat_id = message.chat.id
    
    # Проверяем, является ли этот чат группой для сбора
    if chat_id not in COLLECTION_GROUPS:
        return
    
    group_type = COLLECTION_GROUPS[chat_id]
    logger.info(f"Сообщение в группе {chat_id} ({group_type}) от {message.from_user.id}")
    
    # Пропускаем команды
    if message.text and message.text.startswith("/"):
        return
    
    # Пропускаем ботов
    if message.from_user.is_bot:
        if message.from_user.id == bot.id:
            asyncio.create_task(delete_message_after_delay(message, 600))
        return
    
    # Проверка на флуд/бан - просто игнорируем (НЕ УДАЛЯЕМ, НЕ ПИШЕМ)
    if anti_spam.is_blocked(message.from_user.id):
        logger.info(f"Пользователь {message.from_user.id} в бане - игнорируем")
        return
    
    if anti_spam.check_flood(message.from_user.id):
        logger.info(f"Пользователь {message.from_user.id} зафлудил - игнорируем")
        return
    
    # Внутренняя функция для проверки ключевых слов и обработки
    async def process_if_keywords(content: str, file_id: str = None, post_type: str = "text"):
        if content.strip() and has_trade_keywords(content):
            # Проверяем КД между постами (2 часа)
            user_posts = anti_spam.user_posts.get(message.from_user.id, [])
            
            # Сортируем по времени (от старых к новым)
            user_posts_sorted = sorted(user_posts)
            
            # Проверяем, есть ли пост за последние POST_COOLDOWN_HOURS часов
            now = datetime.now()
            has_recent_post = False
            for post_time in user_posts_sorted:
                if now - post_time < timedelta(hours=POST_COOLDOWN_HOURS):
                    has_recent_post = True
                    break
            
            if has_recent_post:
                # Если отправлял меньше POST_COOLDOWN_HOURS часов назад - игнорируем
                logger.info(f"Пользователь {message.from_user.id} отправлял пост менее {POST_COOLDOWN_HOURS}ч назад - игнорируем")
                return True
            
            # Проверяем лимит постов за 24 часа
            user_posts_today = len([t for t in user_posts if now - t < timedelta(hours=24)])
            
            if user_posts_today >= MAX_POSTS_PER_DAY:
                # Если лимит за 24 часа исчерпан - игнорируем
                logger.info(f"Пользователь {message.from_user.id} использовал все {MAX_POSTS_PER_DAY} поста за 24ч - игнорируем")
                return True
            
            # Все проверки пройдены - отправляем на модерацию
            logger.info("Найдены ключевые слова! Отправляем на модерацию...")
            await handle_post_from_group(message, file_id, post_type, group_type)
            return True
        return False
    
    # Обработка текстовых сообщений
    if message.text:
        logger.info(f"Текст сообщения: {message.text[:100]}...")
        await process_if_keywords(message.text)
    
    # Обработка фото
    elif message.photo:
        caption = message.caption or ""
        logger.info(f"Подпись к фото: {caption[:100]}...")
        await process_if_keywords(caption, message.photo[-1].file_id, "photo")
    
    # Обработка видео
    elif message.video:
        caption = message.caption or ""
        logger.info(f"Видео с подписью: {caption[:100]}...")
        await process_if_keywords(caption, message.video.file_id, "video")
    
    # Обработка документов
    elif message.document:
        caption = message.caption or ""
        logger.info(f"Документ с подписью: {caption[:100]}...")
        await process_if_keywords(caption, message.document.file_id, "document")

# ---------------- CALLBACKS ДЛЯ МОДЕРАЦИИ ----------------
@dp.callback_query(F.data.startswith("approve:"))
async def approve(callback: CallbackQuery):
    if not callback_limiter.check(callback.from_user.id):
        await callback.answer("⏱️ Слишком часто! Подождите немного.", show_alert=True)
        return
    
    post_id = int(callback.data.split(":")[1])
    logger.info(f"Одобрение поста #{post_id} модератором {callback.from_user.id}")
    
    post = await get_post(post_id)
    if not post:
        await callback.answer("❌ Пост не найден")
        return
    
    _, user_id, username, text, file_id, file_type, _ = post
    
    final_text = f"{text}{DISCLAIMER}"
    user_obj = SimpleUser(id=user_id, username=username or "")
    kb = post_kb(user_obj)
    
    try:
        if file_type == "photo" and file_id:
            await bot.send_photo(MARKET_CHANNEL_ID, file_id, caption=final_text, reply_markup=kb, parse_mode="HTML")
        elif file_type == "video" and file_id:
            await bot.send_video(MARKET_CHANNEL_ID, file_id, caption=final_text, reply_markup=kb, parse_mode="HTML")
        elif file_type == "document" and file_id:
            await bot.send_document(MARKET_CHANNEL_ID, file_id, caption=final_text, reply_markup=kb, parse_mode="HTML")
        else:
            await bot.send_message(MARKET_CHANNEL_ID, final_text, reply_markup=kb, parse_mode="HTML")
        
        await update_status(post_id, "approved", callback.from_user.id)
        logger.info(f"Пост #{post_id} опубликован в канале")
        
        try:
            remaining = anti_spam.get_remaining_posts(user_id)
            
            if remaining > 0:
                remaining_text = f"✅ У вас осталось <b>{remaining}</b> объявлений"
                action_text = "Вы можете отправить следующее объявление прямо сейчас!"
                warning_text = f"⏰ Помните: следующее объявление можно отправить {anti_spam.get_next_post_time(user_id)}"
            else:
                remaining_text = f"❌ Сегодня вы использовали <b>{MAX_POSTS_PER_DAY}</b> объявления"
                action_text = "Завтра вы сможете отправить новое объявление с 00:00"
                warning_text = "Спасибо, что пользуетесь нашим сервисом!"
            
            await bot.send_message(
                user_id,
                POST_APPROVED_USER.format(
                    remaining_text=remaining_text,
                    max_posts=MAX_POSTS_PER_DAY,
                    action_text=action_text,
                    warning_text=warning_text
                ),
                parse_mode="HTML"
            )
            logger.info(f"Уведомление отправлено пользователю {user_id}")
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя {user_id}: {e}")
        
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("✅ Одобрено!")
        
    except Exception as e:
        logger.error(f"Ошибка при публикации поста #{post_id}: {e}")
        await callback.answer("❌ Ошибка публикации", show_alert=True)

@dp.callback_query(F.data.startswith("reject:"))
async def reject(callback: CallbackQuery):
    if not callback_limiter.check(callback.from_user.id):
        await callback.answer("⏱️ Слишком часто! Подождите немного.", show_alert=True)
        return
    
    post_id = int(callback.data.split(":")[1])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Нарушение правил", callback_data=f"reject_reason:rules:{post_id}")],
        [InlineKeyboardButton(text="📝 Неверный формат", callback_data=f"reject_reason:format:{post_id}")],
        [InlineKeyboardButton(text="🚫 Спам", callback_data=f"reject_reason:spam:{post_id}")],
        [InlineKeyboardButton(text="🎯 Не по теме", callback_data=f"reject_reason:offtopic:{post_id}")],
        [InlineKeyboardButton(text="💬 Другое", callback_data=f"reject_reason:other:{post_id}")],
        [InlineKeyboardButton(text="↩️ Отмена", callback_data=f"back_to_mod:{post_id}")]
    ])
    
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer("Выберите причину отклонения:")

@dp.callback_query(F.data.startswith("reject_reason:"))
async def reject_with_reason(callback: CallbackQuery):
    if not callback_limiter.check(callback.from_user.id):
        await callback.answer("⏱️ Слишком часто! Подождите немного.", show_alert=True)
        return
    
    _, reason, post_id = callback.data.split(":")
    post_id = int(post_id)
    
    reasons = {
        "rules": "Нарушение правил",
        "format": "Неверный формат объявления",
        "spam": "Спам",
        "offtopic": "Не по теме канала",
        "other": "Другая причина"
    }
    
    reason_text = reasons.get(reason, "Не указана")
    post = await get_post(post_id)
    if not post:
        await callback.answer("❌ Пост не найден")
        return
    
    await update_status(post_id, "rejected", callback.from_user.id)
    logger.info(f"Пост #{post_id} отклонен. Причина: {reason_text}")
    
    try:
        await bot.send_message(
            post[1],
            POST_REJECTED_USER.format(reason=reason_text),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить пользователя {post[1]}: {e}")
    
    status_text = f"\n\n❌ Отклонено: {reason_text}"
    try:
        if callback.message.text:
            await callback.message.edit_text(f"{callback.message.text}{status_text}", parse_mode="HTML")
        elif callback.message.caption:
            await callback.message.edit_caption(caption=f"{callback.message.caption}{status_text}", parse_mode="HTML")
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение: {e}")
    
    await callback.answer(f"Отклонено: {reason_text}")

@dp.callback_query(F.data.startswith("back_to_mod:"))
async def back_to_mod(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[1])
    await callback.message.edit_reply_markup(reply_markup=mod_kb(post_id))
    await callback.answer("↩️ Отмена")

# ---------------- НЕИЗВЕСТНЫЕ КОМАНДЫ ----------------
@dp.message(F.text.startswith("/"))
async def unknown_command(message: Message):
    await message.answer(UNKNOWN_COMMAND, parse_mode="HTML")

# ---------------- START BOT ----------------
async def main():
    await init_db()
    
    asyncio.create_task(clean_old_data_periodically())
    
    logger.info("Бот запущен")
    print("\n✅ Бот успешно запущен!")
    print(f"📝 Чат модерации: {MODERATION_CHAT_ID}")
    print(f"📢 Канал публикации: {MARKET_CHANNEL_ID}")
    print(f"👥 Группы для сбора: {list(COLLECTION_GROUPS.keys())}")
    print(f"👮 Модераторы: {MODERATOR_IDS}")
    print(f"\n📊 Ограничения:")
    print(f"  - {MAX_POSTS_PER_DAY} объявления в сутки")
    print(f"  - {POST_COOLDOWN_HOURS} часа между объявлениями")
    print(f"  - {MAX_MESSAGE_LENGTH} символов максимум")
    print("\n🤖 Бот готов к работе!\n")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())