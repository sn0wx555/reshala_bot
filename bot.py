import asyncio
import logging
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram import F

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
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
from logging.handlers import RotatingFileHandler

load_dotenv()

# Чувствительные настройки
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip().isdigit()]
CALLBACK_SECRET = os.getenv('CALLBACK_SECRET', 'secret-key')

# Настройка логирования с ротацией
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

# Защитные механизмы
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


# Фоновые задачи
async def cleanup_rl_cache_task():
    """Очистка кэша rate limit"""
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
    """Проверка здоровья бота"""
    failed = 0
    while True:
        try:
            await asyncio.wait_for(bot.get_me(), timeout=10)
            failed = 0
        except Exception as e:
            failed += 1
            logger.warning('Health check fail #%s: %s', failed, e)
            if failed >= 3:
                logger.error('Multiple health check failures, will restart')
                try:
                    await bot.session.close()
                except Exception:
                    pass
                os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(300)


async def check_expired_deals_periodically():
    """Проверка просроченных сделок"""
    while True:
        await asyncio.sleep(60)
        try:
            events = await GameLogic.check_expired_deals()
            if events:
                logger.info(f"Found {len(events)} expired deals")
        except Exception as e:
            logger.error(f"Error in expired deals check: {e}")


async def finish_wars_periodically():
    """Завершение войн по времени"""
    while True:
        await asyncio.sleep(60)
        try:
            await GameLogic.finish_wars_periodically()
        except Exception as e:
            logger.error(f"Error in finish wars: {e}")


async def clean_cooldowns_periodically():
    """Очистка старых кулдаунов"""
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            await db.clean_old_cooldowns(24)
            logger.info("Old cooldowns cleaned")
        except Exception as e:
            logger.error(f"Error cleaning cooldowns: {e}")


async def check_achievements_periodically():
    """Периодическая проверка достижений у активных игроков"""
    while True:
        await asyncio.sleep(3600)
        try:
            async with db.conn.execute("SELECT id FROM users LIMIT 50") as cursor:
                users = await cursor.fetchall()
            for u in users:
                await db.check_and_award_titles(u['id'])
        except Exception as e:
            logger.error(f"Error in achievements check: {e}")


async def random_events_periodically():
    """Случайные события раз в час"""
    while True:
        await asyncio.sleep(3600)
        try:
            event_roll = random.random()
            
            if event_roll < 0.1:  # 10% - Чёрный рынок
                await db.create_global_event('black_market', 'Аура со скидкой!', 0.5, 3)
                logger.info("Black market event started")
                
            elif event_roll < 0.2:  # 10% - Инфляция
                await db.create_global_event('inflation', 'Цены растут!', 1.1, 10)
                logger.info("Inflation event started")
                
            elif event_roll < 0.3:  # 10% - Дефляция
                await db.create_global_event('deflation', 'Цены падают!', 0.9, 10)
                logger.info("Deflation event started")
                
            elif event_roll < 0.4:  # 10% - Бонусный час
                await db.create_global_event('bonus_hour', 'Бонусный час!', 2.0, 1)
                logger.info("Bonus hour event started")
            
            # Налёт на казну
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
        await asyncio.sleep(60)
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
        await asyncio.sleep(3600)
        try:
            results = await db.settle_futures()
            for r in results:
                logger.info(f"Future settled: user {r['user_id']}, profit: {r['profit']}")
        except Exception as e:
            logger.error(f"Error in futures: {e}")


async def clan_boss_spawn_periodically():
    """Появление клан-босса раз в неделю"""
    while True:
        await asyncio.sleep(7 * 24 * 3600)
        try:
            boss_names = ['Дракон', 'Гигант', 'Демон', 'Титан', 'Владыка']
            boss_name = random.choice(boss_names)
            await db.spawn_clan_boss(boss_name, 10000)
            logger.info(f"Clan boss spawned: {boss_name}")
        except Exception as e:
            logger.error(f"Error spawning boss: {e}")


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

        # Отправляем события
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


@dp.message(Command("help"))
async def cmd_help(message: Message):
    help_text = GameLogic.get_help_text()
    await message.reply(help_text)


# ---------- Отправка событий ----------
async def send_events(chat_id: int, events: list):
    """Отправляет соб��тия в чат"""
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

        elif ev['type'] == 'clan_created':
            text = f"🏰 Игрок {ev['leader_nick']} создал клан «{ev['clan_name']}» за 200 влияния!"

        elif ev['type'] == 'aura_bought':
            text = (f"💰 {ev['user_nick']} купил {ev['amount']} ауры за {ev['cost']:.0f} влияния.\n"
                    f"📈 Цена изменилась: {ev['old_price']:.2f} → {ev['new_price']:.2f}")

        elif ev['type'] == 'aura_sold':
            text = (f"💰 {ev['user_nick']} продал {ev['amount']} ауры за {ev['gain']:.0f} влияния.\n"
                    f"📉 Цена изменилась: {ev['old_price']:.2f} → {ev['new_price']:.2f}")

        elif ev['type'] == 'daily_bonus':
            text = f"�� Ежедневный бонус: +{ev['amount']} влияния!"

        elif ev['type'] == 'invite_success':
            text = "🎉 Игрок успешно пригласил новичка и получил +15 влияния!"

        elif ev['type'] == 'alliance_created':
            text = f"🤝 Клан «{ev['clan1_name']}» и клан «{ev['clan2_name']}» заключили альянс! Лидер {ev['leader_nick']} скрепил союз."

        elif ev['type'] == 'war_declared':
            text = (f"⚔️ ВОЙНА ОБЪЯВЛЕНА! ⚔️\n"
                    f"Клан «{ev['attacker_clan']}» атакует клан «{ev['defender_clan']}»!\n"
                    f"⏳ Война продлится 24 часа.\n"
                    f"💥 Атакуйте участников вражеского клана командой «атака @ник»")

        elif ev['type'] == 'war_attack':
            text = (f"💥 {ev['attacker_nick']} атакует {ev['defender_nick']} "
                    f"и наносит {ev['damage']} урона! (потрачено {ev['cost']} влияния)")

        elif ev['type'] == 'karma':
            sign = "+" if ev['value'] > 0 else ""
            text = f"⭐ {ev['from_nick']} поставил {sign}{ev['value']} карме {ev['to_nick']}. Теперь у него {ev['new_karma']}."

        elif ev['type'] == 'roulette_result':
            if ev['result'] == 'win':
                text = f"🎰 Рулетка: Выпало {ev['number']} ({ev['bet_type']})! 🎉 Вы выиграли {ev['win_amount']} влияния!"
            else:
                text = f"🎰 Рулетка: Выпало {ev['number']} ({'чёт' if ev['number'] % 2 == 0 else 'нечёт'})... Вы проиграли {ev['bet_amount']} влияния."

        elif ev['type'] == 'duel_started':
            text = (f"⚔️ ДУЭЛЬ! {ev['challenger_nick']} вызвал {ev['target_nick']} на дуэль!\n"
                    f"Ставка: {ev['bet_amount']} влияния\n"
                    f"Длительность: {ev['duration']} минут")

        elif ev['type'] == 'investment_started':
            text = (f"📈 {ev['user_nick']} инвестировал {ev['amount']} влияния!\n"
                    f"Через {ev['duration']} час(а) можно получить от {ev['potential_loss']} до {ev['potential_return']} влияния.")

        elif ev['type'] == 'lottery_ticket_bought':
            text = f"🎫 {ev['user_nick']} купил лотерейный билет за {ev['cost']} влияния! Раунд: {ev['round']}"

        elif ev['type'] == 'future_created':
            text = (f"📊 {ev['user_nick']} купил фьючерс на {ev['amount']} ауры!\n"
                    f"Страйк-цена: {ev['strike_price']:.2f}\n"
                    f"Длительность: {ev['duration']} ч.")

        elif ev['type'] == 'insurance_bought':
            text = f"🛡️ {ev['user_nick']} купил страховку на {ev['coverage']} влияния за {ev['premium']}!"

        elif ev['type'] == 'racket_success':
            text = f"💰 {ev['racketeer_nick']} успешно 'рэкетировал' {ev['target_nick']} и отжал {ev['amount']} влияния!"

        elif ev['type'] == 'racket_failed':
            text = f"💸 {ev['racketeer_nick']} попытался рэкетировать {ev['target_nick']}, но попал на штраф {ev['penalty']}!"

        elif ev['type'] == 'protection_started':
            text = f"🛡️ Клан '{ev['protector_clan']}' теперь крышует клан '{ev['protected_clan']}' за {ev['cost_per_day']} влияния/день!"

        elif ev['type'] == 'showdown_started':
            text = (f"🔫 РАЗБОРКА! {ev['challenger_nick']} вызвал {ev['challenged_nick']} на стрелку!\n"
                    f"Ставка: {ev['stake']} влияния\n"
                    f"Длительность: {ev['duration']} минут")

        elif ev['type'] == 'wedding_proposed':
            text = f"💒 {ev['clan1']} предлагает свадьбу клану {ev['clan2']}! Стоимость: {ev['cost']} влияния"

        elif ev['type'] == 'boss_attack':
            text = f"👊 Атака на босса '{ev['boss_name']}'! Урон: {ev['damage']} HP. Осталось: {ev['boss_health']}"

        elif ev['type'] == 'exchange_success':
            text = f"💱 {ev['from_type']} → {ev['to_type']}: {ev['from_amount']} → {ev['to_amount']:.1f}"

        elif ev['type'] == 'donor_success':
            text = f"🎁 {ev['from_nick']} подарил {ev['to_nick']} {ev['amount']} влияния (День донора)!"

        elif ev['type'] == 'birthday_set':
            text = f"🎂 {ev['user_nick']} установил день рождения: {ev['birthday']}!"

        elif ev['type'] == 'birthday_wish':
            text = f"🎉 {ev['wisher_nick']} поздравил {ev['birthday_user_nick']} с днём рождения! +1 доверие!"

        elif ev['type'] == 'random_bonus':
            text = f"🌟 Случайный бонус! +{ev['amount']} влияния!"

        elif ev['type'] == 'spam_penalty':
            text = (f"⚠️ Вы пишете слишком часто! Пожалуйста, подождите между сообщениями.\n"
                    f"С вас списано {ev['penalty']} влияния за нарушение!")

        elif ev['type'] == 'deposit_created':
            text = (f"🏦 {ev['user_nick']} открыл вклад на {ev['amount']} влияния "
                    f"на {ev['days']} дней под {ev['interest']}% годовых.")

        elif ev['type'] == 'loan_granted':
            text = (f"💰 {ev['user_nick']} взял кредит {ev['amount']} влияния "
                    f"под залог {ev['collateral']} ауры. Срок {ev['due_days']} дней, ставка {ev['interest']}%.")

        elif ev['type'] == 'espionage_started':
            text = (f"🕵️ Клан «{ev['from_clan']}» отправил шпиона в клан «{ev['to_clan']}» "
                    f"(стоимость {ev['cost']} влияния). Результат через 24 часа.")

        elif ev['type'] == 'tournament_joined':
            text = f"🏆 {ev['user_nick']} присоединился к турниру «{ev['tournament_name']}»! Удачи!"

        elif ev['type'] == 'newbie_invite':
            text = (f"👶 Игрок {ev['inviter_nick']} пригласил новичка {ev['newbie_nick']}!\n"
                    f"📊 Новичок должен написать от 5 сообщений в течение 24 часов. "
                    f"Сейчас у него {ev['newbie_messages']} сообщений.")

        elif ev['type'] == 'season_info':
            if 'season_name' in ev:
                text = (f"🏆 Сезон: {ev['season_name']}\n"
                        f"Осталось дней: {ev['days_left']}\n"
                        f"Тип сброса: {ev['reset_type']}")
            else:
                text = ev['text']

        elif ev['type'] == 'goals_info':
            text = ev['text']

        elif ev['type'] == 'titles_info':
            text = ev['text']

        elif ev['type'] == 'title_selected':
            text = f"✅ Титул '{ev['title']}' активирован!"

        elif ev['type'] == 'authority_info':
            text = f"🔥 Авторитет: {ev['points']} очков\nРанг: {ev['rank']}"

        elif ev['type'] == 'clan_event_info':
            text = ev['text']

        elif ev['type'] == 'secret_command':
            text = ev['response']
            if ev['rarity'] in ['rare', 'legendary']:
                text = f"✨ {text}"

        # Ошибки
        elif ev['type'] == 'deal_error':
            if ev['error'] == 'low_trust':
                text = f"❌ Для сделок нужно {ev['required']} доверия."
            elif ev['error'] == 'user_not_found':
                text = f"❌ Пользователь @{ev['username']} не найден."
            elif ev['error'] == 'not_enough_influence':
                text = "❌ У вас недостаточно влияния."
            elif ev['error'] == 'not_enough_aura':
                text = "❌ У вас недостаточно ауры."

        elif ev['type'] == 'clan_error':
            if ev['error'] == 'not_enough_influence':
                text = f"❌ Для создания клана нужно {ev['required']} влияния."
            elif ev['error'] == 'not_enough_trust':
                text = f"❌ Для создания клана нужно {ev['required']} доверия."
            elif ev['error'] == 'name_exists':
                text = f"❌ Клан «{ev['name']}» уже существует."
            elif ev['error'] == 'not_found':
                text = f"❌ Клан «{ev['name']}» не найден."

        elif ev['type'] == 'aura_error':
            if ev['error'] == 'not_enough_influence':
                text = f"❌ Нужно {ev['required']:.0f} влияния."
            elif ev['error'] == 'not_enough_aura':
                text = f"❌ У вас только {ev['required']:.2f} ауры."

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

        elif ev['type'] == 'karma_error':
            if ev['error'] == 'user_not_found':
                text = "❌ Игрок не найден."
            elif ev['error'] == 'self_karma':
                text = "❌ Нельзя изменять карму самому себе."
            elif ev['error'] == 'already_given_today':
                text = "❌ Вы уже ставили карму этому игроку сегодня."

        elif ev['type'] == 'roulette_error':
            if ev['error'] == 'min_bet':
                text = f"❌ Минимальная ставка: {ev['min']} влияния."

        elif ev['type'] == 'duel_error':
            if ev['error'] == 'user_not_found':
                text = "❌ Игрок не найден."
            elif ev['error'] == 'self_duel':
                text = "❌ Нельзя вызвать на дуэль самого себя."
            elif ev['error'] == 'not_enough_influence':
                text = "❌ Недостаточно влияния."

        elif ev['type'] == 'deposit_error':
            if ev['error'] == 'invalid_amount':
                text = "❌ Сумма должна быть положительной."
            elif ev['error'] == 'not_enough_influence':
                text = "❌ Недостаточно влияния."

        elif ev['type'] == 'loan_error':
            if ev['error'] == 'invalid_amount':
                text = "❌ Сумма должна быть положительной."
            elif ev['error'] == 'not_enough_collateral':
                text = "❌ Недостаточно ауры для залога."
            elif ev['error'] == 'already_has_loan':
                text = "❌ У вас уже есть активный кредит."

        elif ev['type'] == 'espionage_error':
            if ev['error'] == 'not_in_clan':
                text = "❌ Вы не состоите в клане."
            elif ev['error'] == 'not_leader_or_officer':
                text = "❌ Только лидер или офицер может отправлять шпиона."
            elif ev['error'] == 'target_clan_not_found':
                text = f"❌ Клан «{ev['name']}» не найден."

        elif ev['type'] == 'tournament_error':
            if ev['error'] == 'no_active_tournament':
                text = "❌ Сейчас нет активного турнира."

        elif ev['type'] == 'invite_error':
            if ev['error'] == 'user_not_found':
                text = f"❌ Игрок {ev['name']} не найден."
            elif ev['error'] == 'self_invite':
                text = "❌ Нельзя пригласить самого себя."
            elif ev['error'] == 'already_invited':
                text = "❌ Этот игрок уже был приглашён."
            elif ev['error'] == 'not_newbie':
                text = "❌ Этот игрок не новичок."

        elif ev['type'] == 'boss_error':
            text = "❌ Сейчас нет активного босса."

        elif ev['type'] == 'exchange_error':
            text = "❌ Ошибка обмена."

        elif ev['type'] == 'donor_error':
            text = "❌ Нельзя передать влияние (уже был трансфер за 24ч или недостаточно влияния)."

        elif ev['type'] == 'birthday_error':
            if ev['error'] == 'not_birthday':
                text = "❌ У этого игрока сегодня не день рождения."

        elif ev['type'] == 'title_error':
            text = "❌ У вас нет такого титула."

        elif ev['type'] == 'protection_error':
            text = "❌ Ошибка при создании защиты."

        elif ev['type'] == 'racket_error':
            text = "❌ Ошибка при рэкете."

        elif ev['type'] == 'showdown_error':
            text = "❌ Ошибка при вызове на разборку."

        elif ev['type'] == 'wedding_error':
            text = "❌ Ошибка свадьбы кланов."

        if text:
            await bot.send_message(chat_id, text)
            await asyncio.sleep(0.3)


# Запуск сервера
async def on_startup():
    """Инициализация при запуске"""
    logger.info("Starting up...")
    await db.connect()
    
    # Инициализация новых таблиц
    await db._init_titles()
    await db._init_secret_commands()
    
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
    asyncio.create_task(cleanup_rl_cache_task())
    asyncio.create_task(health_check_task())
    
    logger.info("Bot started successfully!")


async def on_shutdown():
    """Завершение при выключении"""
    logger.info("Shutting down...")
    await db.close()
    logger.info("Database closed")


async def main():
    """Главная функция"""
    await db.connect()
    logger.info("Database connected")
    
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    dp.message.middleware(ProtectionMiddleware())

    PORT = int(os.getenv("PORT", 8000))
    RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

    # Ставим вебхук
    webhook_url = f"{RENDER_URL}/webhook"
    await bot.set_webhook(webhook_url, allowed_updates=dp.resolve_used_update_types())
    print(f"Webhook установлен на {webhook_url}")

    # Создаём веб-сервер
    async def webhook_endpoint(request):
        try:
            update = types.Update(**(await request.json()))
            await dp.feed_update(bot, update)
            return Response(status_code=200)
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return Response(status_code=500)

    async def health_endpoint(request):
        return PlainTextResponse("OK")

    starlette_app = Starlette(routes=[
        Route("/webhook", webhook_endpoint, methods=["POST"]),
        Route("/health", health_endpoint, methods=["GET"]),
    ])

    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
