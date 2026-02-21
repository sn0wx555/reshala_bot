import os
from dotenv import load_dotenv

load_dotenv()

# Bot
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_NAME = "game.db"

# Influence rewards
INFLUENCE_PER_MESSAGE = 1
INFLUENCE_PER_REPLY = 2
INFLUENCE_PER_MENTION = 3
INFLUENCE_PER_INVITE = 15
DAILY_BONUS = 5

# Cooldowns (seconds)
REPLY_COOLDOWN = 180
MENTION_COOLDOWN = 180
TRUST_COOLDOWN = 86400
WAR_ATTACK_COOLDOWN = 1800  # 30 минут

# Requirements
MIN_TRUST_FOR_DEAL = 10
MIN_TRUST_FOR_CLAN = 10
CLAN_CREATION_COST = 200
WAR_DECLARE_COST = 100  # из казны

# Aura
INITIAL_AURA_PRICE = 10.0
AURA_BUY_INCREASE = 0.05   # +5% за покупку
AURA_SELL_DECREASE = 0.03  # -3% за продажу
MIN_AURA_PRICE = 1.0

# Newbie invite
NEWBIE_MIN_MESSAGES = 5
NEWBIE_MAX_HOURS = 24

# Tops update interval (seconds)
TOP_UPDATE_INTERVAL = 600

# Levels
EXP_PER_LEVEL = 1000  # опыт для повышения уровня

# Клановые улучшения
UPGRADE_COSTS = {
    'treasury': [200, 500, 1000, 2000, 5000],
    'military': [300, 600, 1200, 2400, 6000],
    'diplomacy': [400, 800, 1600, 3200, 8000]
}

# Банк
DEPOSIT_INTEREST = 0.05  # 5% в день
LOAN_INTEREST = 0.1      # 10% за срок

# Рулетка
ROULETTE_MIN_BET = 10
ROULETTE_MULTIPLIER = 2

# Инвестиции
INVESTMENT_MIN = 50
INVESTMENT_DURATION = 1  # часов

# Лотерея
LOTTERY_TICKET_COST = 10
LOTTERY_DRAW_INTERVAL = 1  # часов

# Дуэли
DUEL_MIN_STAKE = 10
DUEL_DURATION = 5  # минут

# Разборки (showdowns)
SHOWDOWN_STAKE = 100
SHOWDOWN_DURATION = 3  # минут

# Фьючерсы
FUTURES_MIN_AMOUNT = 1  # минимум ауры
FUTURES_DURATION = 24  # часов
FUTURES_MARGIN = 0.1   # 10% маржа

# Страховка
INSURANCE_PREMIUM_RATE = 0.1  # 10% от покрытия

# Рэкет
RACKET_SUCCESS_CHANCE = 0.7
RACKET_PENALTY_RATE = 0.2

# Крышевание
PROTECTION_COST_PER_DAY = 50

# Свадьба кланов
CLAN_WEDDING_COST = 1000

# Клан-босс
CLAN_BOSS_SPAWN_INTERVAL = 7  # дней
CLAN_BOSS_HEALTH = 10000
CLAN_BOSS_REWARD_INFLUENCE = 500
CLAN_BOSS_REWARD_AURA = 10

# Черный рынок
BLACK_MARKET_DISCOUNT = 0.5
BLACK_MARKET_DURATION = 3  # минут

# Бонусный час
BONUS_HOUR_MULTIPLIER = 2

# День донора
DONOR_TRANSFER_COOLDOWN = 86400  # 24 часа

# Клан-ивенты
CLAN_EVENT_DURATION = 24  # часов

# Защита от спама
SPAM_COOLDOWN = 1.5  # минимальный интервал между сообщениями (секунды)
SPAM_PENALTY = 3  # штраф влияния за спам