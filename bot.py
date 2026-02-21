from starlette.routing import Route
import asyncio
import logging
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram import F
# Добавь это где-нибудь после остальных импортов
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
import uvicorn

from config import BOT_TOKEN, TOP_UPDATE_INTERVAL, LOTTERY_DRAW_INTERVAL
from database import db
from logic import GameLogic
import random
from dotenv import load_dotenv
import os
import sys
import signal
import time
import hmac
import hashlib
import html

load_dotenv()

# Чувствительные настройки
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip().isdigit()]
CALLBACK_SECRET = os.getenv('CALLBACK_SECRET', 'secret-key')

# Настройка логирования с ротацией
from logging.handlers import RotatingFileHandler
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
fh = RotatingFileHandler('bot.log', maxBytes=5_000_000, backupCount=3, encoding='utf-8')
fh.setFormatter(formatter)
sh = logging.StreamHandler()
sh.setFormatter(formatter)
logger.addHandler(fh)
logger.addHandler(sh)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# ----------------- Защитные механизмы (middleware, HMAC, rate-limit) -----------------
_rl_cache = {}  # {user_id: [timestamps]}
_last_command = {}  # {user_id: (text, ts)}

RL_MAX_MSGS = int(os.getenv('RL_MAX_MSGS', '5'))
RL_INTERVAL = int(os.getenv('RL_INTERVAL', '10'))
CMD_REPEAT_INTERVAL = int(os.getenv('CMD_REPEAT_INTERVAL', '5'))
MSG_MAX_LENGTH = int(os.getenv('MSG_MAX_LENGTH', '4096'))
IGNORE_OLD_SECONDS = int(os.getenv('IGNORE_OLD_SECONDS', '60'))


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def sign_callback(data: str) -> str:
    sig = hmac.new(CALLBACK_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}|{sig}"


def verify_callback(signed: str):
    try:
        data, sig = signed.rsplit('|', 1)
    except Exception:
        return False, ''
    expected = hmac.new(CALLBACK_SECRET.encode(), data.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected), data


class ProtectionMiddleware:
    """Middleware для анти-спама, проверки длины, старых сообщений и повторов команд."""
    async def __call__(self, handler, event: types.Message, data: dict):
        try:
            msg = event
            if not msg.from_user:
                return
            uid = msg.from_user.id

            # Длина сообщения
            if msg.text and len(msg.text) > MSG_MAX_LENGTH:
                logger.info('Too long message from %s', uid)
                try:
                    await msg.reply('Сообщение слишком длинное.')
                except Exception:
                    pass
                return

            # Старые сообщения
            if msg.date:
                now = datetime.now(timezone.utc)
                age = (now - msg.date).total_seconds()
                if age > IGNORE_OLD_SECONDS:
                    logger.info('Ignoring old message from %s (age=%s)', uid, age)
                    return

            # Rate limit
            now_ts = time.time()
            arr = _rl_cache.get(uid, [])
            arr = [t for t in arr if t > now_ts - RL_INTERVAL]
            arr.append(now_ts)
            _rl_cache[uid] = arr
            if len(arr) > RL_MAX_MSGS and not is_admin(uid):
                logger.info('Rate limit exceeded: %s', uid)
                try:
                    await msg.reply('Слишком много запросов, подождите.')
                except Exception:
                    pass
                return

            # Повтор команд
            if msg.text and msg.text.startswith('/') and not is_admin(uid):
                last = _last_command.get(uid)
                if last and last[0] == msg.text and (now_ts - last[1]) < CMD_REPEAT_INTERVAL:
                    logger.info('Repeated command ignored %s', uid)
                    return
                _last_command[uid] = (msg.text, now_ts)

            # Экранируем ввод пользователя
            if msg.text:
                msg = msg.model_copy(update={'text': html.escape(msg.text)})

            return await handler(msg, data)
        except Exception:
            logger.exception('ProtectionMiddleware failed')


# Фоновые задачи для очистки кэша и health-check
async def cleanup_rl_cache_task():
    while True:
        try:
            now_ts = time.time()
            cutoff = now_ts - max(RL_INTERVAL, CMD_REPEAT_INTERVAL) - 5
            for k in list(_rl_cache.keys()):
                _rl_cache[k] = [t for t in _rl_cache[k] if t >= cutoff]
                if not _rl_cache[k]:
                    del _rl_cache[k]
        except Exception:
            logger.exception('cleanup_rl_cache_task error')
        await asyncio.sleep(600)


async def health_check_task():
    failed = 0
    while True:
        try:
            await asyncio.wait_for(bot.get_me(), timeout=10)
            failed = 0
        except Exception as e:
            failed += 1
            logger.warning('Health check fail #%s: %s', failed, e)
            if failed >= 3:
                logger.error('Multiple health check failures, restarting via os.exec')
                try:
                    await bot.session.close()
                except Exception:
                    pass
                os.execl(sys.executable, sys.executable, *sys.argv)
        await asyncio.sleep(300)


# ----- Дополнительные меры от поломки (3 штуки) -----
async def safely_run(coro, timeout: int = 30):
    """Запуск корутины с таймаутом и логированием исключений."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning('Operation timed out')
    except Exception:
        logger.exception('Error in safely_run')
    return None


async def memory_watchdog(max_mb: int = 200):
    """Проверяет память процесса и перезапускает при превышении (опционально).
    Нужен psutil для работы; если его нет, функция просто завернёт в sleep.
    """
    try:
        import psutil
    except Exception:
        # psutil не установлен — просто ждём
        while True:
            await asyncio.sleep(600)
    proc = psutil.Process()
    while True:
        mem = proc.memory_info().rss / 1024 / 1024
        if mem > max_mb:
            logger.error('Memory limit exceeded: %s MB > %s MB, restarting', mem, max_mb)
            try:
                await bot.session.close()
            except Exception:
                pass
            os.execl(sys.executable, sys.executable, *sys.argv)
        await asyncio.sleep(60)


def handler_timeout(seconds: int = 20):
    """Декоратор для оборачивания пользовательских хэндлеров с таймаутом.
    (Не применяется автоматически к существующим хэндлерам, но доступен для новых.)
    """
    def deco(func):
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                logger.warning('Handler %s timed out', func.__name__)
            except Exception:
                logger.exception('Handler %s error', func.__name__)
        return wrapper
    return deco


@dp.callback_query()
async def callback_protection(query: types.CallbackQuery):
    ok, data = verify_callback(query.data or '')
    if not ok:
        logger.warning('Callback signature invalid from %s', query.from_user.id if query.from_user else '?')
        try:
            await query.answer('Неверная подпись.', show_alert=True)
        except Exception:
            pass
        return
    # Если нужно, можно передавать data дальше; по умолчанию отвечаем коротко
    try:
        await query.answer(f'OK: {data}')
    except Exception:
        pass

# Хранилище активных сделок: message_id -> deal_id
active_deals = {}


# ---------- Обработчик всех текстовых сообщений ----------
@dp.message(F.text)
async def handle_message(message: Message):
    try:
        # Игнорируем сообщения от ботов
        if message.from_user.is_bot:
            return

        # Обработка личных сообщений (приветствие)
        if message.chat.type == "private":
            await message.reply(
                "👋 Привет! Я бот для социальной игры «Решала Клуб».\n"
                "Чтобы играть, добавь меня в групповой чат и сделай администратором.\n"
                "А пока можешь посмотреть свою статистику, написав «Профиль».\n\n"
                "Полный список возможностей: «Помощь»"
            )
            return

        # Групповой чат
        user_id = message.from_user.id
        username = message.from_user.username or f"user_{user_id}"
        nickname = message.from_user.full_name

        # Кому отвечаем (если есть)
        reply_to_id = None
        if message.reply_to_message and message.reply_to_message.from_user:
            reply_to_id = message.reply_to_message.from_user.id

        # Упомянутые пользователи
        mentioned_ids = []
        if message.entities:
            for ent in message.entities:
                if ent.type == "mention":
                    mention = message.text[ent.offset+1:ent.offset+ent.length]
                    async with db.conn.execute(
                        "SELECT id FROM users WHERE username = ?", (mention,)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            mentioned_ids.append(row[0])
                elif ent.type == "text_mention" and ent.user:
                    mentioned_ids.append(ent.user.id)

        # Обрабатываем сообщение в логике
        result = await GameLogic.process_message(
            user_id=user_id,
            username=username,
            nickname=nickname,
            chat_id=message.chat.id,
            message_text=message.text or "",
            reply_to_user_id=reply_to_id,
            mentioned_users=mentioned_ids,
            message_id=message.message_id
        )

        # Отправляем события (например, доверие, сделка, аура, войны и т.д.)
        await send_events(message.chat.id, result['events'])

        # Проверяем, является ли это подтверждением сделки (ответ на сообщение с предложением)
        if message.reply_to_message and message.reply_to_message.message_id in active_deals:
            deal_id = active_deals[message.reply_to_message.message_id]
            confirm_result = await GameLogic.confirm_deal(deal_id, user_id)
            if confirm_result:
                await send_events(message.chat.id, [confirm_result])
                if confirm_result['type'] == 'deal_success':
                    del active_deals[message.reply_to_message.message_id]

        # Обрабатываем текстовые запросы (профиль, топ, помощь и др.)
        if result.get('profile_request'):
            profile_text = await GameLogic.get_profile_text(user_id)
            await message.reply(profile_text)

        if result.get('top_request'):
            top_text = await GameLogic.get_top_text()
            await message.reply(top_text)

        if result.get('help_request'):
            help_text = GameLogic.get_help_text()
            await message.reply(help_text)

        if result.get('clan_power_request'):
            power_text = await GameLogic.get_clan_power_rating_text(10)
            await message.reply(power_text)

        if result.get('war_status_request'):
            war_text = await GameLogic.get_war_status_text(result['war_status_request'])
            await message.reply(war_text)

        if result.get('clan_profile_request'):
            clan_profile = await GameLogic.get_clan_profile_text(result['clan_profile_request'])
            await message.reply(clan_profile)

        if result.get('achievements_request'):
            ach_text = await GameLogic.get_achievements_text(user_id)
            await message.reply(ach_text)

        if result.get('level_request'):
            level_text = await GameLogic.get_level_text(user_id)
            await message.reply(level_text)

        if result.get('events_request'):
            events_text = await GameLogic.get_events_text()
            await message.reply(events_text)

        if result.get('black_market_request'):
            bm_text = await GameLogic.get_black_market_text()
            await message.reply(bm_text)

        # Если было предложение сделки, сохраняем его message_id -> deal_id
        for ev in result['events']:
            if ev.get('type') == 'deal_proposal':
                active_deals[message.message_id] = ev['deal_id']
                break

    except Exception as e:
        logger.error(f"Error in handle_message: {e}", exc_info=True)


# ---------- Команды (для совместимости, но игра через текст) ----------
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.reply(
        "👋 Привет! Я бот «Решала Клуб».\n"
        "Используй обычные слова: Профиль, Топ, Помощь, Доверяю, @username 50 влияние, Купить аура 10, Война НазваниеКлана и т.д."
    )


# ---------- Отправка событий ----------
async def send_events(chat_id: int, events: list):
    for ev in events:
        if not ev:
            continue

        text = None

        # Основные события
        if ev['type'] == 'trust':
            text = f"🤝 {ev['truster_nick']} доверяет {ev['trusted_nick']} → +1 доверие!"

        elif ev['type'] == 'deal_proposal':
            type_ru = "влияния" if ev['amount_type'] == 'influence' else "ауры"
            text = (f"📝 {ev['sender_nick']} предлагает {ev['amount']} {type_ru} "
                    f"игроку {ev['recipient_nick']}. У него 3 минуты ответить «согласен».")

        elif ev['type'] == 'deal_success':
            type_ru = "влияния" if ev['amount_type'] == 'influence' else "ауры"
            text = f"✅ Сделка состоялась! {ev['sender_nick']} → {ev['recipient_nick']}: {ev['amount']} {type_ru}"

        elif ev['type'] == 'deal_cancelled':
            text = f"⌛ Сделка между {ev['sender_nick']} и {ev['recipient_nick']} отменена (время вышло)."

        elif ev['type'] == 'deal_violation':
            text = f"⚠️ {ev['violator_nick']} нарушил сделку и теряет 1 доверие!"

        elif ev['type'] == 'deal_expired':
            text = "⌛ Время сделки истекло."

        elif ev['type'] == 'deal_error':
            if ev['error'] == 'low_trust':
                text = f"❌ Для сделок нужно {ev['required']} доверия."
            elif ev['error'] == 'user_not_found':
                text = f"❌ Пользователь @{ev['username']} не найден."
            elif ev['error'] == 'not_enough_influence':
                text = "❌ У вас недостаточно влияния."
            elif ev['error'] == 'not_enough_aura':
                text = "❌ У вас недостаточно ауры."

        elif ev['type'] == 'clan_created':
            text = f"🏰 Игрок {ev['leader_nick']} создал клан «{ev['clan_name']}» за 200 влияния!"

        elif ev['type'] == 'clan_error':
            if ev['error'] == 'not_enough_influence':
                text = f"❌ Для создания клана нужно {ev['required']} влияния."
            elif ev['error'] == 'not_enough_trust':
                text = f"❌ Для создания клана нужно {ev['required']} доверия."
            elif ev['error'] == 'name_exists':
                text = f"❌ Клан «{ev['name']}» уже существует."
            elif ev['error'] == 'not_found':
                text = f"❌ Клан «{ev['name']}» не найден."

        elif ev['type'] == 'aura_bought':
            text = (f"💰 {ev['user_nick']} купил {ev['amount']} ауры за {ev['cost']:.0f} влияния.\n"
                    f"📈 Цена изменилась: {ev['old_price']:.2f} → {ev['new_price']:.2f}")

        elif ev['type'] == 'aura_sold':
            text = (f"💰 {ev['user_nick']} продал {ev['amount']} ауры за {ev['gain']:.0f} влияния.\n"
                    f"📉 Цена изменилась: {ev['old_price']:.2f} → {ev['new_price']:.2f}")

        elif ev['type'] == 'aura_error':
            if ev['error'] == 'not_enough_influence':
                text = f"❌ Нужно {ev['required']:.0f} влияния."
            elif ev['error'] == 'not_enough_aura':
                text = f"❌ У вас только {ev['required']:.2f} ауры."

        elif ev['type'] == 'daily_bonus':
            text = f"🎁 Ежедневный бонус: +{ev['amount']} влияния!"

        elif ev['type'] == 'invite_success':
            text = "🎉 Игрок успешно пригласил новичка и получил +15 влияния!"

        # Альянсы
        elif ev['type'] == 'alliance_created':
            text = f"🤝 Клан «{ev['clan1_name']}» и клан «{ev['clan2_name']}» заключили альянс! Лидер {ev['leader_nick']} скрепил союз."

        elif ev['type'] == 'alliance_error':
            if ev['error'] == 'not_in_clan':
                text = "❌ Вы не состоите в клане."
            elif ev['error'] == 'not_leader':
                text = "❌ Только лидер клана может создавать альянсы."
            elif ev['error'] == 'target_clan_not_found':
                text = f"❌ Клан «{ev['name']}» не найден."
            elif ev['error'] == 'self_alliance':
                text = "❌ Нельзя создать альянс с самим собой."
            elif ev['error'] == 'already_allied':
                text = f"❌ У вас уже есть альянс с кланом «{ev['name']}»."

        # Войны
        elif ev['type'] == 'war_declared':
            text = (f"⚔️ ВОЙНА ОБЪЯВЛЕНА! ⚔️\n"
                    f"Клан «{ev['attacker_clan']}» атакует клан «{ev['defender_clan']}»!\n"
                    f"⏳ Война продлится 24 часа.\n"
                    f"💥 Атакуйте участников вражеского клана командой «атака @ник»")

        elif ev['type'] == 'war_attack':
            text = (f"💥 {ev['attacker_nick']} атакует {ev['defender_nick']} "
                    f"и наносит {ev['damage']} урона! (потрачено {ev['cost']} влияния)")

        elif ev['type'] == 'war_error':
            if ev['error'] == 'not_in_clan':
                text = "❌ Вы не состоите в клане."
            elif ev['error'] == 'not_leader_or_officer':
                text = "❌ Только лидер или офицер клана может объявлять войну."
            elif ev['error'] == 'target_clan_not_found':
                text = f"❌ Клан «{ev['name']}» не найден."
            elif ev['error'] == 'self_war':
                text = "❌ Нельзя объявить войну своему клану."
            elif ev['error'] == 'already_at_war':
                text = f"❌ Ваш клан уже воюет с кланом «{ev['name']}»."
            elif ev['error'] == 'cannot_war_alliance':
                text = f"❌ Нельзя объявить войну клану, с которым у вас альянс."
            elif ev['error'] == 'not_enough_treasury':
                text = f"❌ В казне клана недостаточно влияния. Нужно {ev['required']}."
            elif ev['error'] == 'no_active_wars':
                text = f"❌ У клана «{ev['name']}» нет активных войн."

        elif ev['type'] == 'war_attack_error':
            if ev['error'] == 'not_in_clan':
                text = "❌ Вы не состоите в клане."
            elif ev['error'] == 'user_not_found':
                text = f"❌ Игрок {ev['name']} не найден."
            elif ev['error'] == 'target_not_in_clan':
                text = "❌ Цель не состоит в клане."
            elif ev['error'] == 'self_attack':
                text = "❌ Нельзя атаковать самого себя."
            elif ev['error'] == 'not_at_war':
                text = "❌ Ваш клан не воюет с кланом этого игрока."
            elif ev['error'] == 'cooldown':
                text = "❌ Атаковать одного игрока можно раз в 30 минут."

        # Карма
        elif ev['type'] == 'karma':
            sign = "+" if ev['value'] > 0 else ""
            text = f"⭐ {ev['from_nick']} поставил {sign}{ev['value']} карме {ev['to_nick']}. Теперь у него {ev['new_karma']}."

        elif ev['type'] == 'karma_error':
            if ev['error'] == 'user_not_found':
                text = "❌ Игрок не найден."
            elif ev['error'] == 'self_karma':
                text = "❌ Нельзя изменять карму самому себе."
            else:
                text = "❌ Ошибка кармы."

        # ==================== НОВЫЕ СОБЫТИЯ ====================

        # Рулетка
        elif ev['type'] == 'roulette_result':
            if ev['result'] == 'win':
                text = f"🎰 Рулетка: Выпало {ev['number']} ({ev['bet_type']})! 🎉 Вы выиграли {ev['win_amount']} влияния!"
            else:
                text = f"🎰 Рулетка: Выпало {ev['number']} ({'чёт' if ev['number'] % 2 == 0 else 'нечёт'})... Вы проиграли {ev['bet_amount']} влияния."

        elif ev['type'] == 'roulette_error':
            if ev['error'] == 'min_bet':
                text = f"❌ Минимальная ставка: {ev['min']} влияния."
            else:
                text = "❌ Ошибка рулетки."

        # Дуэль
        elif ev['type'] == 'duel_started':
            text = (f"⚔️ ДУЭЛЬ! {ev['challenger_nick']} вызвал {ev['target_nick']} на дуэль!\n"
                    f"Ставка: {ev['bet_amount']} влияния\n"
                    f"Длительность: {ev['duration']} минут")

        elif ev['type'] == 'duel_error':
            if ev['error'] == 'user_not_found':
                text = "❌ Игрок не найден."
            elif ev['error'] == 'self_duel':
                text = "❌ Нельзя вызвать на дуэль самого себя."
            elif ev['error'] == 'not_enough_influence':
                text = "❌ Недостаточно влияния."

        # Инвестиции
        elif ev['type'] == 'investment_started':
            text = (f"📈 {ev['user_nick']} инвестировал {ev['amount']} влияния!\n"
                    f"Через {ev['duration']} час(а) можно получить от {ev['potential_loss']} до {ev['potential_return']} влияния.")

        elif ev['type'] == 'investment_error':
            text = f"❌ Минимальная инвестиция: {ev.get('min', 50)} влияния."

        # Лотерея
        elif ev['type'] == 'lottery_ticket_bought':
            text = f"🎫 {ev['user_nick']} купил лотерейный билет за {ev['cost']} влияния! Раунд: {ev['round']}"

        elif ev['type'] == 'lottery_error':
            text = "❌ Недостаточно влияния для покупки билета."

        # Фьючерсы
        elif ev['type'] == 'future_created':
            text = (f"📊 {ev['user_nick']} купил фьючерс на {ev['amount']} ауры!\n"
                    f"Страйк-цена: {ev['strike_price']:.2f}\n"
                    f"Длительность: {ev['duration']} ч.")

        elif ev['type'] == 'future_error':
            if ev['error'] == 'not_enough_influence':
                text = f"❌ Недостаточно влияния. Нужно: {ev.get('required', 0)}"
            else:
                text = "❌ Ошибка фьючерса."

        # Страховка
        elif ev['type'] == 'insurance_bought':
            text = f"🛡️ {ev['user_nick']} купил страховку на {ev['coverage']} влияния за {ev['premium']}!"

        elif ev['type'] == 'insurance_error':
            text = "❌ Недостаточно влияния для страховки."

        # Рэкет
        elif ev['type'] == 'racket_success':
            text = f"💰 {ev['racketeer_nick']} успешно 'рэкетировал' {ev['target_nick']} и отжал {ev['amount']} влияния!"

        elif ev['type'] == 'racket_failed':
            text = f"💸 {ev['racketeer_nick']} попытался рэкетировать {ev['target_nick']}, но попал на штраф {ev['penalty']}!"

        elif ev['type'] == 'racket_error':
            if ev['error'] == 'user_not_found':
                text = "❌ Игрок не найден."
            elif ev['error'] == 'self_racket':
                text = "❌ Нельзя рэкетировать себя."

        # Крышевание
        elif ev['type'] == 'protection_started':
            text = f"🛡️ Клан '{ev['protector_clan']}' теперь крышует клан '{ev['protected_clan']}' за {ev['cost_per_day']} влияния/день!"

        elif ev['type'] == 'protection_error':
            if ev['error'] == 'not_in_clan':
                text = "❌ Вы не состоите в клане."

        # Разборки
        elif ev['type'] == 'showdown_started':
            text = (f"🔫 РАЗБОРКА! {ev['challenger_nick']} вызвал {ev['challenged_nick']} на стрелку!\n"
                    f"Ставка: {ev['stake']} влияния\n"
                    f"Длительность: {ev['duration']} минут")

        elif ev['type'] == 'showdown_error':
            text = "❌ Ошибка при вызове на разборку."

        # Свадьба кланов
        elif ev['type'] == 'wedding_proposed':
            text = f"💒 {ev['clan1']} предлагает свадьбу клану {ev['clan2']}! Стоимость: {ev['cost']} влияния"

        elif ev['type'] == 'wedding_error':
            text = "❌ Ошибка свадьбы кланов."

        # Клан-босс
        elif ev['type'] == 'boss_attack':
            text = f"👊 {ev['user_nick']} атаковал босса '{ev['boss_name']}' и нанёс {ev['damage']} урона! Осталось HP: {ev['boss_health']}"

        elif ev['type'] == 'boss_error':
            text = "❌ Сейчас нет активного босса."

        # Обмен внутри клана
        elif ev['type'] == 'exchange_success':
            text = f"💱 {ev['from_type']} → {ev['to_type']}: {ev['from_amount']} → {ev['to_amount']:.1f}"

        elif ev['type'] == 'exchange_error':
            text = "❌ Ошибка обмена."

        # День донора
        elif ev['type'] == 'donor_success':
            text = f"🎁 {ev['from_nick']} подарил {ev['to_nick']} {ev['amount']} влияния (День донора)!"

        elif ev['type'] == 'donor_error':
            text = "❌ Нельзя передать влияние (уже был трансфер за 24ч или недостаточно влияния)."

        # День рождения
        elif ev['type'] == 'birthday_set':
            text = f"🎂 {ev['user_nick']} установил день рождения: {ev['birthday']}!"

        elif ev['type'] == 'birthday_wish':
            text = f"🎉 {ev['wisher_nick']} поздравил {ev['birthday_user_nick']} с днём рождения! +1 доверие!"

        elif ev['type'] == 'birthday_error':
            if ev['error'] == 'not_birthday':
                text = "❌ У этого игрока сегодня не день рождения."

        # Сезон
        elif ev['type'] == 'season_info':
            if 'season_name' in ev:
                text = (f"🏆 Сезон: {ev['season_name']}\n"
                        f"Осталось дней: {ev['days_left']}\n"
                        f"Тип сброса: {ev['reset_type']}")
            else:
                text = ev['text']

        # Личные цели
        elif ev['type'] == 'goals_info':
            text = ev['text']

        # Титулы
        elif ev['type'] == 'titles_info':
            text = ev['text']

        elif ev['type'] == 'title_selected':
            text = f"✅ Титул '{ev['title']}' активирован!"

        elif ev['type'] == 'title_error':
            text = "❌ У вас нет такого титула."

        # Авторитет
        elif ev['type'] == 'authority_info':
            text = f"🔥 Авторитет: {ev['points']} очков\nРанг: {ev['rank']}"

        # Клан-ивенты
        elif ev['type'] == 'clan_event_info':
            text = ev['text']

        # Секретные команды
        elif ev['type'] == 'secret_command':
            text = ev['response']
            if ev['rarity'] in ['rare', 'legendary']:
                text = f"✨ {text}"

        # Случайный бонус
        elif ev['type'] == 'random_bonus':
            text = f"🌟 Случайный бонус! +{ev['amount']} влияния!"
        
        # Штраф за спам
        elif ev['type'] == 'spam_penalty':
            text = (f"⚠️ Вы пишете слишком часто! Пожалуйста, подождите {ev['time_diff']} сек. между сообщениями.\n"
                    f"С вас списано {ev['penalty']} влияния за нарушение!")
            if ev['error'] == 'invalid_value':
                text = "❌ Карма может быть только +1 или -1."
            elif ev['error'] == 'user_not_found':
                text = f"❌ Игрок {ev['name']} не найден."
            elif ev['error'] == 'self_karma':
                text = "❌ Нельзя ставить карму самому себе."
            elif ev['error'] == 'already_given_today':
                text = "❌ Вы уже ставили карму этому игроку сегодня."

        # Банк
        elif ev['type'] == 'deposit_created':
            text = (f"🏦 {ev['user_nick']} открыл вклад на {ev['amount']} влияния "
                    f"на {ev['days']} дней под {ev['interest']}% годовых.")

        elif ev['type'] == 'deposit_error':
            if ev['error'] == 'invalid_amount':
                text = "❌ Сумма должна быть положительной."
            elif ev['error'] == 'not_enough_influence':
                text = "❌ Недостаточно влияния."
            elif ev['error'] == 'creation_failed':
                text = "❌ Не удалось создать вклад."

        elif ev['type'] == 'loan_granted':
            text = (f"💰 {ev['user_nick']} взял кредит {ev['amount']} влияния "
                    f"под залог {ev['collateral']} ауры. Срок {ev['due_days']} дней, ставка {ev['interest']}%.")

        elif ev['type'] == 'loan_error':
            if ev['error'] == 'invalid_amount':
                text = "❌ Сумма должна быть положительной."
            elif ev['error'] == 'not_enough_collateral':
                text = "❌ Недостаточно ауры для залога."
            elif ev['error'] == 'already_has_loan':
                text = "❌ У вас уже есть активный кредит."
            elif ev['error'] == 'creation_failed':
                text = "❌ Не удалось оформить кредит."

        # Шпионаж
        elif ev['type'] == 'espionage_started':
            text = (f"🕵️ Клан «{ev['from_clan']}» отправил шпиона в клан «{ev['to_clan']}» "
                    f"(стоимость {ev['cost']} влияния). Результат через 24 часа.")

        elif ev['type'] == 'espionage_error':
            if ev['error'] == 'not_in_clan':
                text = "❌ Вы не состоите в клане."
            elif ev['error'] == 'not_leader_or_officer':
                text = "❌ Только лидер или офицер может отправлять шпиона."
            elif ev['error'] == 'target_clan_not_found':
                text = f"❌ Клан «{ev['name']}» не найден."
            elif ev['error'] == 'self_espionage':
                text = "❌ Нельзя шпионить за своим кланом."
            elif ev['error'] == 'cannot_spy_alliance':
                text = "❌ Нельзя шпионить за кланом-союзником."
            elif ev['error'] == 'not_enough_treasury':
                text = f"❌ В казне недостаточно влияния. Нужно {ev['required']}."
            elif ev['error'] == 'creation_failed':
                text = "❌ Не удалось начать шпионаж."

        # Турниры
        elif ev['type'] == 'tournament_joined':
            text = f"🏆 {ev['user_nick']} присоединился к турниру «{ev['tournament_name']}»! Удачи!"

        elif ev['type'] == 'tournament_error':
            if ev['error'] == 'no_active_tournament':
                text = "❌ Сейчас нет активного турнира."

        # Приглашение новичка
        elif ev['type'] == 'newbie_invite':
            text = (f"👶 Игрок {ev['inviter_nick']} пригласил новичка {ev['newbie_nick']}!\n"
                    f"📊 Новичок должен написать от 5 сообщений в течение 24 часов, чтобы приглашение засчиталось. "
                    f"Сейчас у него {ev['newbie_messages']} сообщений.")

        elif ev['type'] == 'invite_error':
            if ev['error'] == 'user_not_found':
                text = f"❌ Игрок {ev['name']} не найден."
            elif ev['error'] == 'self_invite':
                text = "❌ Нельзя пригласить самого себя."
            elif ev['error'] == 'already_invited':
                text = "❌ Этот игрок уже был приглашён."
            elif ev['error'] == 'not_newbie':
                text = "❌ Этот игрок не новичок (у него уже >50 влияния)."

        if text:
            await bot.send_message(chat_id, text)
            await asyncio.sleep(0.3)  # небольшая задержка


# ---------- Фоновые задачи ----------
async def check_expired_deals_periodically():
    while True:
        await asyncio.sleep(60)  # раз в минуту
        try:
            events = await GameLogic.check_expired_deals()
            for ev in events:
                # Здесь можно отправить уведомления в соответствующие чаты, но для этого нужно сохранять chat_id в deals
                # Пока просто логируем
                if events:
                    logger.info(f"Found {len(events)} expired deals")
        except Exception as e:
            logger.error(f"Error in expired deals check: {e}")

async def finish_wars_periodically():
    while True:
        await asyncio.sleep(60)
        try:
            await GameLogic.finish_wars_periodically()
        except Exception as e:
            logger.error(f"Error in finish wars: {e}")

async def clean_cooldowns_periodically():
    while True:
        await asyncio.sleep(24 * 3600)
        await db.clean_old_cooldowns(24)
        logger.info("Old cooldowns cleaned")

async def check_achievements_periodically():
    """Периодически проверяем достижения у активных игроков"""
    while True:
        await asyncio.sleep(3600)  # раз в час
        try:
            # Проверяем титулы
            async with db.conn.execute("SELECT id FROM users") as cursor:
                users = await cursor.fetchall()
            for u in users[:50]:  # Проверяем первых 50
                await db.check_and_award_titles(u['id'])
        except Exception as e:
            logger.error(f"Error in achievements check: {e}")


# ==================== НОВЫЕ ФОНОВЫЕ ЗАДАЧИ ====================

async def random_events_periodically():
    """Случайные события раз в час"""
    while True:
        await asyncio.sleep(3600)  # раз в час
        try:
            # Случайное событие
            event_roll = random.random()
            
            if event_roll < 0.1:  # 10% - Чёрный рынок
                from datetime import timedelta
                ends = datetime.now() + timedelta(minutes=3)
                await db.create_global_event('black_market', 'Аура со скидкой!', 0.5, ends)
                logger.info("Black market event started")
                
            elif event_roll < 0.2:  # 10% - Инфляция
                from datetime import timedelta
                ends = datetime.now() + timedelta(minutes=10)
                await db.create_global_event('inflation', 'Цены растут!', 1.1, ends)
                logger.info("Inflation event started")
                
            elif event_roll < 0.3:  # 10% - Дефляция
                from datetime import timedelta
                ends = datetime.now() + timedelta(minutes=10)
                await db.create_global_event('deflation', 'Цены падают!', 0.9, ends)
                logger.info("Deflation event started")
                
            elif event_roll < 0.4:  # 10% - Бонусный час
                from datetime import timedelta
                ends = datetime.now() + timedelta(hours=1)
                await db.create_global_event('bonus_hour', 'Бонусный час!', 2.0, ends)
                logger.info("Bonus hour event started")
            
            # Налёт на казну (редко)
            if random.random() < 0.05:
                raid_result = await db.treasury_raid()
                if raid_result:
                    logger.info(f"Treasury raid: {raid_result['clan_name']} lost {raid_result['loss']}")
                    
        except Exception as e:
            logger.error(f"Error in random events: {e}")


async def lottery_draw_periodically():
    """Розыгрыш лотереи"""
    while True:
        await asyncio.sleep(LOTTERY_DRAW_INTERVAL * 3600)
        try:
            current_hour = datetime.now().hour
            round_number = datetime.now().year * 10000 + datetime.now().month * 100 + current_hour
            winner = await db.draw_lottery(round_number)
            if winner:
                logger.info(f"Lottery winner: {winner}")
        except Exception as e:
            logger.error(f"Error in lottery draw: {e}")


async def investments_settle_periodically():
    """Расчёт инвестиций"""
    while True:
        await asyncio.sleep(60)  # каждую минуту
        try:
            completed = await db.check_investments()
            for inv in completed:
                result = await db.settle_investment(inv['id'])
                logger.info(f"Investment settled: user {inv['user_id']}, profit: {result['profit']}")
        except Exception as e:
            logger.error(f"Error in investments: {e}")


async def futures_settle_periodically():
    """Расчёт фьючерсов"""
    while True:
        await asyncio.sleep(3600)  # каждый час
        try:
            results = await db.settle_futures()
            for r in results:
                logger.info(f"Future settled: user {r['user_id']}, profit: {r['profit']}")
        except Exception as e:
            logger.error(f"Error in futures: {e}")


async def clan_boss_spawn_periodically():
    """Появление клан-босса раз в неделю"""
    while True:
        await asyncio.sleep(7 * 24 * 3600)  # раз в неделю
        try:
            boss_names = ['Дракон', 'Гигант', 'Демон', 'Титан', 'Владыка']
            boss_name = random.choice(boss_names)
            await db.spawn_clan_boss(boss_name, 10000)
            logger.info(f"Clan boss spawned: {boss_name}")
        except Exception as e:
            logger.error(f"Error spawning boss: {e}")


# ---------- Startup / Shutdown ----------
async def on_startup():
    logger.info("Starting up...")
    await db.connect()
    
    # Инициализация новых таблиц
    await db.init_titles()
    await db.init_secret_commands()
    
    # Запускаем фоновые задачи
    asyncio.create_task(check_expired_deals_periodically())
    asyncio.create_task(finish_wars_periodically())
    asyncio.create_task(clean_cooldowns_periodically())
    asyncio.create_task(check_achievements_periodically())
    asyncio.create_task(random_events_periodically())
    asyncio.create_task(lottery_draw_periodically())
    asyncio.create_task(investments_settle_periodically())
    asyncio.create_task(futures_settle_periodically())
    asyncio.create_task(clan_boss_spawn_periodically())
    # Защитные фоновые задачи
    asyncio.create_task(cleanup_rl_cache_task())
    asyncio.create_task(health_check_task())
    
    logger.info("Bot started successfully!")


async def on_shutdown():
    logger.info("Shutting down...")
    await db.close()
    logger.info("Database closed")


async def main():
    # 👇 РЕГИСТРИРУЕМ STARTUP И SHUTDOWN (это было пропущено!)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # 👇 ЯВНО ПОДКЛЮЧАЕМ MIDDLEWARE (для надёжности)
    dp.message.middleware(ProtectionMiddleware())

    # Получаем порт от Render
    PORT = int(os.getenv("PORT", 8000))
    RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

    # Ставим вебхук (вместо polling'а)
    webhook_url = f"{RENDER_URL}/webhook"
    await bot.set_webhook(webhook_url, allowed_updates=dp.resolve_used_update_types())
    print(f"Webhook установлен на {webhook_url}")

    # Создаём веб-сервер для Render
    async def webhook_endpoint(request):
        update = types.Update(**(await request.json()))
        await dp.feed_update(bot, update)
        return Response()

    async def health_endpoint(request):
        return PlainTextResponse("OK")

    starlette_app = Starlette(routes=[
        Route("/webhook", webhook_endpoint, methods=["POST"]),
        Route("/health", health_endpoint, methods=["GET"]),
    ])

    # Запускаем сервер
    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
