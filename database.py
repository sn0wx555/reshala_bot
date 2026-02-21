import aiosqlite
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from config import *

class Database:
    def __init__(self, db_path: str = None):
        """Инициализация базы данных с определением пути"""
        if db_path is None:
            self.db_path = '/tmp/bot_database.db' if os.getenv('RENDER') else 'bot_database.db'
        else:
            self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None
        print(f"🔥 Database path: {self.db_path}")

    async def connect(self):
        """Подключение к базе данных с созданием таблиц"""
        print(f"🔥 Connecting to database at {self.db_path}")
        try:
            self.conn = await aiosqlite.connect(self.db_path)
            self.conn.row_factory = aiosqlite.Row
            await self._create_tables()
            await self._init_achievements()
            await self._init_titles()
            await self._init_secret_commands()
            print("✅ Database connected successfully")
        except Exception as e:
            print(f"❌ Database connection error: {e}")
            raise

    async def close(self):
        """Закрытие соединения с БД"""
        if self.conn:
            await self.conn.close()
            print("✅ Database connection closed")

    async def _ensure_connection(self):
        """Проверка соединения и подключение при необходимости"""
        if self.conn is None:
            print("⚠️ Connection not established, connecting...")
            await self.connect()

    async def _create_tables(self):
        """Создание всех таблиц (весь твой код создания таблиц)"""
        async with self.conn.cursor() as cur:
            # Users
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT,
                    nickname TEXT NOT NULL,
                    influence INTEGER DEFAULT 0,
                    trust INTEGER DEFAULT 0,
                    aura REAL DEFAULT 0,
                    clan_id INTEGER,
                    messages_count INTEGER DEFAULT 0,
                    deals_count INTEGER DEFAULT 0,
                    war_wins INTEGER DEFAULT 0,
                    alliances_count INTEGER DEFAULT 0,
                    invites_count INTEGER DEFAULT 0,
                    level INTEGER DEFAULT 1,
                    experience INTEGER DEFAULT 0,
                    last_activity TIMESTAMP,
                    last_trust_given TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    invited_by INTEGER,
                    invite_time TIMESTAMP,
                    FOREIGN KEY (clan_id) REFERENCES clans(id) ON DELETE SET NULL
                )
            """)

            # Clans
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS clans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    leader_id INTEGER NOT NULL,
                    influence_treasury INTEGER DEFAULT 0,
                    aura_treasury REAL DEFAULT 0,
                    status TEXT DEFAULT 'neutral',
                    wars_won INTEGER DEFAULT 0,
                    wars_lost INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (leader_id) REFERENCES users(id)
                )
            """)

            # Clan members
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS clan_members (
                    clan_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (clan_id, user_id),
                    FOREIGN KEY (clan_id) REFERENCES clans(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Clan roles
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS clan_roles (
                    clan_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    PRIMARY KEY (clan_id, user_id),
                    FOREIGN KEY (clan_id) REFERENCES clans(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Clan upgrades
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS clan_upgrades (
                    clan_id INTEGER NOT NULL,
                    upgrade_type TEXT NOT NULL,
                    level INTEGER DEFAULT 0,
                    PRIMARY KEY (clan_id, upgrade_type),
                    FOREIGN KEY (clan_id) REFERENCES clans(id) ON DELETE CASCADE
                )
            """)

            # Alliances
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS alliances (
                    clan_id_1 INTEGER NOT NULL,
                    clan_id_2 INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (clan_id_1, clan_id_2),
                    FOREIGN KEY (clan_id_1) REFERENCES clans(id) ON DELETE CASCADE,
                    FOREIGN KEY (clan_id_2) REFERENCES clans(id) ON DELETE CASCADE
                )
            """)

            # Wars
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS wars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clan1_id INTEGER NOT NULL,
                    clan2_id INTEGER NOT NULL,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ends_at TIMESTAMP NOT NULL,
                    status TEXT DEFAULT 'active',
                    winner_id INTEGER,
                    clan1_damage INTEGER DEFAULT 0,
                    clan2_damage INTEGER DEFAULT 0,
                    FOREIGN KEY (clan1_id) REFERENCES clans(id) ON DELETE CASCADE,
                    FOREIGN KEY (clan2_id) REFERENCES clans(id) ON DELETE CASCADE,
                    FOREIGN KEY (winner_id) REFERENCES clans(id)
                )
            """)

            # War attacks
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS war_attacks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    war_id INTEGER NOT NULL,
                    attacker_id INTEGER NOT NULL,
                    defender_id INTEGER NOT NULL,
                    damage INTEGER NOT NULL,
                    attacked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (war_id) REFERENCES wars(id) ON DELETE CASCADE,
                    FOREIGN KEY (attacker_id) REFERENCES users(id),
                    FOREIGN KEY (defender_id) REFERENCES users(id)
                )
            """)

            # Deals
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER NOT NULL,
                    recipient_id INTEGER NOT NULL,
                    amount_type TEXT NOT NULL,
                    amount REAL NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    message_id INTEGER,
                    chat_id INTEGER,
                    FOREIGN KEY (sender_id) REFERENCES users(id),
                    FOREIGN KEY (recipient_id) REFERENCES users(id)
                )
            """)

            # Aura price history
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS aura_price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    price REAL NOT NULL,
                    total_influence INTEGER,
                    total_aura REAL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Message history
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS message_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    reply_to_user_id INTEGER,
                    mentioned_users TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Cooldowns
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS cooldowns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    last_action TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (from_user_id) REFERENCES users(id),
                    FOREIGN KEY (to_user_id) REFERENCES users(id)
                )
            """)

            # Newbie invites
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS newbie_invites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inviter_id INTEGER NOT NULL,
                    newbie_id INTEGER NOT NULL,
                    invite_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    messages_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    FOREIGN KEY (inviter_id) REFERENCES users(id),
                    FOREIGN KEY (newbie_id) REFERENCES users(id)
                )
            """)

            # Achievements
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS achievements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT NOT NULL,
                    condition_type TEXT NOT NULL,
                    condition_value INTEGER NOT NULL,
                    reward_influence INTEGER DEFAULT 0,
                    reward_aura REAL DEFAULT 0,
                    reward_bonus TEXT DEFAULT '{}'
                )
            """)

            # User achievements
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS user_achievements (
                    user_id INTEGER NOT NULL,
                    achievement_id INTEGER NOT NULL,
                    earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, achievement_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (achievement_id) REFERENCES achievements(id) ON DELETE CASCADE
                )
            """)

            # Karma
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS karma (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    value INTEGER NOT NULL,
                    given_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (from_user_id) REFERENCES users(id),
                    FOREIGN KEY (to_user_id) REFERENCES users(id)
                )
            """)

            # Deposits
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS deposits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount_influence INTEGER NOT NULL,
                    interest_rate REAL DEFAULT 0.05,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    matured_at TIMESTAMP,
                    status TEXT DEFAULT 'active',
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Loans
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS loans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount_influence INTEGER NOT NULL,
                    collateral_aura REAL DEFAULT 0,
                    due_at TIMESTAMP NOT NULL,
                    status TEXT DEFAULT 'active',
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Espionage
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS espionage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_clan_id INTEGER NOT NULL,
                    to_clan_id INTEGER NOT NULL,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ends_at TIMESTAMP,
                    success BOOLEAN,
                    info TEXT,
                    FOREIGN KEY (from_clan_id) REFERENCES clans(id) ON DELETE CASCADE,
                    FOREIGN KEY (to_clan_id) REFERENCES clans(id) ON DELETE CASCADE
                )
            """)

            # Global events
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS global_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    description TEXT,
                    multiplier REAL DEFAULT 1.0,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ends_at TIMESTAMP,
                    active BOOLEAN DEFAULT 1
                )
            """)

            # Tournaments
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS tournaments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    prize_pool_influence INTEGER DEFAULT 0,
                    prize_pool_aura REAL DEFAULT 0,
                    status TEXT DEFAULT 'upcoming'
                )
            """)

            # Tournament participants
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_participants (
                    tournament_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    score INTEGER DEFAULT 0,
                    rank INTEGER,
                    prize_influence INTEGER DEFAULT 0,
                    prize_aura REAL DEFAULT 0,
                    PRIMARY KEY (tournament_id, user_id),
                    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Seasons
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS seasons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    reset_type TEXT DEFAULT 'partial',
                    status TEXT DEFAULT 'upcoming'
                )
            """)

            # Season rewards
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS season_rewards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    season_id INTEGER NOT NULL,
                    rank INTEGER NOT NULL,
                    reward_influence INTEGER DEFAULT 0,
                    reward_aura REAL DEFAULT 0,
                    title TEXT,
                    FOREIGN KEY (season_id) REFERENCES seasons(id) ON DELETE CASCADE
                )
            """)

            # Personal goals
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS personal_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    goal_type TEXT NOT NULL,
                    target_value INTEGER NOT NULL,
                    current_value INTEGER DEFAULT 0,
                    reward_influence INTEGER DEFAULT 0,
                    reward_aura REAL DEFAULT 0,
                    completed BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Titles
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS titles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    condition_type TEXT,
                    condition_value INTEGER
                )
            """)

            # User titles
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS user_titles (
                    user_id INTEGER NOT NULL,
                    title_id INTEGER NOT NULL,
                    active BOOLEAN DEFAULT 0,
                    earned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, title_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (title_id) REFERENCES titles(id) ON DELETE CASCADE
                )
            """)

            # Command skins
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS command_skins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    original_command TEXT NOT NULL,
                    skin_emoji TEXT NOT NULL,
                    unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Spam protection
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS spam_protection (
                    user_id INTEGER PRIMARY KEY,
                    last_message_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    message_count INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Roulette games
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS roulette_games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    bet_type TEXT NOT NULL,
                    bet_amount INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    win_amount INTEGER DEFAULT 0,
                    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Duels
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS duels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player1_id INTEGER NOT NULL,
                    player2_id INTEGER NOT NULL,
                    bet_amount INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    duration_minutes INTEGER DEFAULT 5,
                    player1_messages INTEGER DEFAULT 0,
                    player2_messages INTEGER DEFAULT 0,
                    winner_id INTEGER,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ends_at TIMESTAMP,
                    FOREIGN KEY (player1_id) REFERENCES users(id),
                    FOREIGN KEY (player2_id) REFERENCES users(id)
                )
            """)

            # Investments
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS investments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    duration_hours INTEGER DEFAULT 1,
                    multiplier REAL DEFAULT 1.5,
                    status TEXT DEFAULT 'active',
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ends_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Lottery
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS lottery_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    round_number INTEGER NOT NULL,
                    bought_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            await cur.execute("""
                CREATE TABLE IF NOT EXISTS lottery_rounds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_number INTEGER UNIQUE NOT NULL,
                    jackpot INTEGER DEFAULT 0,
                    winner_id INTEGER,
                    drawn_at TIMESTAMP,
                    status TEXT DEFAULT 'active'
                )
            """)

            # Clan weddings
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS clan_weddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clan1_id INTEGER NOT NULL,
                    clan2_id INTEGER NOT NULL,
                    cost_influence INTEGER DEFAULT 1000,
                    status TEXT DEFAULT 'pending',
                    proposed_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (clan1_id) REFERENCES clans(id),
                    FOREIGN KEY (clan2_id) REFERENCES clans(id)
                )
            """)

            # Clan bosses
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS clan_bosses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    boss_name TEXT NOT NULL,
                    total_health INTEGER DEFAULT 10000,
                    current_health INTEGER DEFAULT 10000,
                    damage_per_hit INTEGER DEFAULT 10,
                    reward_influence INTEGER DEFAULT 500,
                    reward_aura REAL DEFAULT 10,
                    spawned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ends_at TIMESTAMP,
                    status TEXT DEFAULT 'active'
                )
            """)

            await cur.execute("""
                CREATE TABLE IF NOT EXISTS clan_boss_damage (
                    boss_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    damage INTEGER DEFAULT 0,
                    last_hit TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (boss_id, user_id),
                    FOREIGN KEY (boss_id) REFERENCES clan_bosses(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Clan events
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS clan_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP NOT NULL,
                    reward_influence INTEGER DEFAULT 0,
                    reward_aura REAL DEFAULT 0,
                    winner_clan_id INTEGER,
                    status TEXT DEFAULT 'active'
                )
            """)

            # Clan protections
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS clan_protections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    protected_clan_id INTEGER NOT NULL,
                    protector_clan_id INTEGER NOT NULL,
                    cost_per_day INTEGER DEFAULT 50,
                    status TEXT DEFAULT 'active',
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (protected_clan_id) REFERENCES clans(id),
                    FOREIGN KEY (protector_clan_id) REFERENCES clans(id)
                )
            """)

            # Rackets
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS rackets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    racketeer_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    success BOOLEAN,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP,
                    FOREIGN KEY (racketeer_id) REFERENCES users(id),
                    FOREIGN KEY (target_id) REFERENCES users(id)
                )
            """)

            # Authority points
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS authority_points (
                    user_id INTEGER PRIMARY KEY,
                    points INTEGER DEFAULT 0,
                    rank TEXT DEFAULT 'none',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Showdowns
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS showdowns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    challenger_id INTEGER NOT NULL,
                    challenged_id INTEGER NOT NULL,
                    stake_amount INTEGER DEFAULT 100,
                    status TEXT DEFAULT 'pending',
                    challenger_score INTEGER DEFAULT 0,
                    challenged_score INTEGER DEFAULT 0,
                    duration_minutes INTEGER DEFAULT 3,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ends_at TIMESTAMP,
                    winner_id INTEGER,
                    FOREIGN KEY (challenger_id) REFERENCES users(id),
                    FOREIGN KEY (challenged_id) REFERENCES users(id)
                )
            """)

            # Futures
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS futures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    strike_price REAL NOT NULL,
                    current_price_at_open REAL NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    status TEXT DEFAULT 'active',
                    profit_loss REAL DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Insurance policies
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS insurance_policies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    coverage_amount INTEGER NOT NULL,
                    premium INTEGER NOT NULL,
                    active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Clan exchange rates
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS clan_exchange_rates (
                    clan_id INTEGER PRIMARY KEY,
                    influence_to_aura_rate REAL DEFAULT 0.1,
                    aura_to_influence_rate REAL DEFAULT 10,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (clan_id) REFERENCES clans(id)
                )
            """)

            # Secret commands
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS secret_commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger_word TEXT UNIQUE NOT NULL,
                    response TEXT NOT NULL,
                    rarity TEXT DEFAULT 'common'
                )
            """)

            # Random bonuses
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS random_bonuses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    bonus_type TEXT NOT NULL,
                    bonus_amount INTEGER NOT NULL,
                    awarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Player birthdays
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS player_birthdays (
                    user_id INTEGER PRIMARY KEY,
                    birthday DATE NOT NULL,
                    last_celebrated YEAR,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Birthday wishes
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS birthday_wishes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    birthday_user_id INTEGER NOT NULL,
                    wisher_id INTEGER NOT NULL,
                    wish_text TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (birthday_user_id) REFERENCES users(id),
                    FOREIGN KEY (wisher_id) REFERENCES users(id)
                )
            """)

            # Donor transfers
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS donor_transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    transferred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (from_user_id) REFERENCES users(id),
                    FOREIGN KEY (to_user_id) REFERENCES users(id)
                )
            """)

            # Reminders
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    reminder_type TEXT NOT NULL,
                    message TEXT,
                    scheduled_at TIMESTAMP NOT NULL,
                    sent BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Ghost notifications
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS ghost_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    shown_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Indexes
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_users_influence ON users(influence DESC)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_users_trust ON users(trust DESC)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_users_aura ON users(aura DESC)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_deals_expires ON deals(expires_at)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_message_history_user ON message_history(user_id, timestamp)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_cooldowns_lookup ON cooldowns(from_user_id, to_user_id, action_type)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_newbie_invites_status ON newbie_invites(status)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_karma_to ON karma(to_user_id)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_deposits_user ON deposits(user_id)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_loans_user ON loans(user_id)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_tournaments_status ON tournaments(status)")

            await self.conn.commit()
        print("✅ All tables created successfully")

    # ==================== Инициализация данных ====================

    async def _init_achievements(self):
        """Заполнить таблицу достижений, если пуста"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT COUNT(*) FROM achievements") as cursor:
            count = (await cursor.fetchone())[0]
        if count > 0:
            return
        achievements = [
            ('first_blood', 'Первая кровь', 'influence', 100, 10, 0, '{}'),
            ('deal_master', 'Мастер сделок', 'deals', 10, 50, 1, '{"war_damage_bonus":0.05}'),
            ('conqueror', 'Завоеватель', 'war_wins', 5, 200, 5, '{"war_damage_bonus":0.1}'),
            ('diplomat', 'Дипломат', 'alliances', 3, 100, 2, '{}'),
            ('inviter', 'Привлекательный', 'invites', 5, 150, 3, '{}'),
            ('rich', 'Богач', 'aura', 100, 500, 10, '{}'),
            ('trusted', 'Доверенное лицо', 'trust', 50, 300, 0, '{}')
        ]
        for a in achievements:
            await self.conn.execute(
                """INSERT INTO achievements 
                   (name, description, condition_type, condition_value, reward_influence, reward_aura, reward_bonus)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                a
            )
        await self.conn.commit()

    async def _init_titles(self):
        """Инициализация титулов"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT COUNT(*) FROM titles") as cursor:
            count = (await cursor.fetchone())[0]
        if count > 0:
            return
        titles = [
            ('Спекулянт', 'Набрать 1000+ ауры', 'aura', 1000),
            ('Мафиози', 'Выиграть 10 войн', 'war_wins', 10),
            ('Ликвидатор', 'Нанести 1000 урона в войнах', 'war_damage', 1000),
            ('Дипломат', 'Заключить 5 альянсов', 'alliances', 5),
            ('Миллионер', 'Накопить 10000 влияния', 'influence', 10000),
            ('Доверенный', 'Получить 100 доверия', 'trust', 100),
            ('Призыватель', 'Пригласить 10 новичков', 'invites', 10),
            ('Банкир', 'Создать 10 вкладов', 'deposits', 10),
            ('Торговец', 'Совершить 50 сделок', 'deals', 50),
            ('Авторитет', 'Получить 100 очков авторитета', 'authority', 100),
        ]
        for t in titles:
            await self.conn.execute(
                "INSERT INTO titles (name, description, condition_type, condition_value) VALUES (?, ?, ?, ?)", t
            )
        await self.conn.commit()

    async def _init_secret_commands(self):
        """Инициализация скрытых команд"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT COUNT(*) FROM secret_commands") as cursor:
            count = (await cursor.fetchone())[0]
        if count > 0:
            return
        
        commands = [
            ('баланс', '💰 Твой баланс: {influence} влияния и {aura} ауры', 'common'),
            ('кинуть', '🪙 Подбросили монетку... Выпал {coin}!', 'common'),
            ('наехать', '🚗 Ты выехал на встречку! Штраф {fine} влияния', 'rare'),
            ('шмот', '👕 У тебя {outfits} крутых шмоток', 'uncommon'),
            ('кукл', '🎭 Кукловод {name} наблюдает за тобой...', 'legendary'),
        ]
        for c in commands:
            await self.conn.execute(
                "INSERT INTO secret_commands (trigger_word, response, rarity) VALUES (?, ?, ?)", c
            )
        await self.conn.commit()

    # ==================== ПОЛЬЗОВАТЕЛИ ====================

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получить пользователя по ID"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_or_create_user(self, user_id: int, username: str, nickname: str) -> Dict[str, Any]:
        """Получить или создать пользователя"""
        await self._ensure_connection()
        user = await self.get_user(user_id)
        if user:
            return user
        async with self.conn.execute(
            "INSERT INTO users (id, username, nickname) VALUES (?, ?, ?) RETURNING *",
            (user_id, username, nickname)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def update_user(self, user_id: int, **kwargs):
        """Обновить данные пользователя"""
        if not kwargs:
            return
        await self._ensure_connection()
        fields = ', '.join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [user_id]
        await self.conn.execute(f"UPDATE users SET {fields} WHERE id = ?", values)
        await self.conn.commit()

    async def add_influence(self, user_id: int, amount: int):
        """Добавить влияние пользователю"""
        await self._ensure_connection()
        await self.conn.execute(
            "UPDATE users SET influence = influence + ? WHERE id = ?",
            (amount, user_id)
        )
        await self.conn.commit()

    async def add_experience(self, user_id: int, exp: int):
        """Добавить опыт и обновить уровень"""
        await self._ensure_connection()
        user = await self.get_user(user_id)
        if not user:
            return
        new_exp = user['experience'] + exp
        new_level = user['level']
        while new_exp >= new_level * EXP_PER_LEVEL:
            new_exp -= new_level * EXP_PER_LEVEL
            new_level += 1
        await self.conn.execute(
            "UPDATE users SET experience = ?, level = ? WHERE id = ?",
            (new_exp, new_level, user_id)
        )
        await self.conn.commit()

    async def get_all_users(self) -> List[Dict[str, Any]]:
        """Получить всех пользователей"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM users ORDER BY influence DESC") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ==================== КЛАНЫ ====================

    async def get_clan(self, clan_id: int) -> Optional[Dict[str, Any]]:
        """Получить клан по ID"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM clans WHERE id = ?", (clan_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_clan_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Получить клан по названию"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM clans WHERE name = ?", (name,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def create_clan(self, name: str, leader_id: int) -> Dict[str, Any]:
        """Создать новый клан"""
        await self._ensure_connection()
        async with self.conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO clans (name, leader_id) VALUES (?, ?) RETURNING *",
                (name, leader_id)
            )
            clan_row = await cur.fetchone()
            clan_id = clan_row['id']
            await cur.execute(
                "INSERT INTO clan_members (clan_id, user_id) VALUES (?, ?)",
                (clan_id, leader_id)
            )
            await cur.execute(
                "UPDATE users SET clan_id = ? WHERE id = ?",
                (clan_id, leader_id)
            )
            await self.conn.commit()
            return dict(clan_row)

    async def update_clan(self, clan_id: int, **kwargs):
        """Обновить данные клана"""
        if not kwargs:
            return
        await self._ensure_connection()
        fields = ', '.join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [clan_id]
        await self.conn.execute(f"UPDATE clans SET {fields} WHERE id = ?", values)
        await self.conn.commit()

    async def get_clan_members(self, clan_id: int) -> List[Dict[str, Any]]:
        """Получить всех участников клана"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT u.* FROM users u JOIN clan_members cm ON u.id = cm.user_id WHERE cm.clan_id = ?",
            (clan_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def add_clan_member(self, clan_id: int, user_id: int):
        """Добавить пользователя в клан"""
        await self._ensure_connection()
        await self.conn.execute(
            "INSERT OR IGNORE INTO clan_members (clan_id, user_id) VALUES (?, ?)",
            (clan_id, user_id)
        )
        await self.conn.execute("UPDATE users SET clan_id = ? WHERE id = ?", (clan_id, user_id))
        await self.conn.commit()

    async def remove_clan_member(self, user_id: int):
        """Удалить пользователя из клана"""
        await self._ensure_connection()
        await self.conn.execute("DELETE FROM clan_members WHERE user_id = ?", (user_id,))
        await self.conn.execute("UPDATE users SET clan_id = NULL WHERE id = ?", (user_id,))
        await self.conn.commit()

    async def get_all_clans(self) -> List[Dict[str, Any]]:
        """Получить все кланы"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT c.*, COUNT(cm.user_id) as member_count FROM clans c LEFT JOIN clan_members cm ON c.id = cm.clan_id GROUP BY c.id"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ==================== РОЛИ В КЛАНЕ ====================

    async def set_clan_role(self, clan_id: int, user_id: int, role: str):
        """Установить роль в клане"""
        await self._ensure_connection()
        await self.conn.execute(
            "INSERT OR REPLACE INTO clan_roles (clan_id, user_id, role) VALUES (?, ?, ?)",
            (clan_id, user_id, role)
        )
        await self.conn.commit()

    async def remove_clan_role(self, clan_id: int, user_id: int):
        """Удалить роль в клане"""
        await self._ensure_connection()
        await self.conn.execute("DELETE FROM clan_roles WHERE clan_id = ? AND user_id = ?", (clan_id, user_id))
        await self.conn.commit()

    async def get_clan_roles(self, clan_id: int) -> Dict[str, List[int]]:
        """Получить все роли в клане"""
        await self._ensure_connection()
        roles = {'officer': [], 'treasurer': []}
        async with self.conn.execute("SELECT user_id, role FROM clan_roles WHERE clan_id = ?", (clan_id,)) as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                roles[r['role']].append(r['user_id'])
        return roles

    async def is_clan_leader(self, user_id: int, clan_id: int) -> bool:
        """Проверить, является ли пользователь лидером клана"""
        await self._ensure_connection()
        clan = await self.get_clan(clan_id)
        return clan and clan['leader_id'] == user_id

    async def has_clan_role(self, user_id: int, clan_id: int, role: str) -> bool:
        """Проверить наличие роли у пользователя в клане"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT 1 FROM clan_roles WHERE clan_id = ? AND user_id = ? AND role = ?",
            (clan_id, user_id, role)
        ) as cursor:
            return await cursor.fetchone() is not None

    # ==================== УЛУЧШЕНИЯ КЛАНА ====================

    async def get_clan_upgrade_level(self, clan_id: int, upgrade_type: str) -> int:
        """Получить уровень улучшения клана"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT level FROM clan_upgrades WHERE clan_id = ? AND upgrade_type = ?",
            (clan_id, upgrade_type)
        ) as cursor:
            row = await cursor.fetchone()
            return row['level'] if row else 0

    async def upgrade_clan(self, clan_id: int, upgrade_type: str) -> bool:
        """Улучшить клан"""
        await self._ensure_connection()
        current = await self.get_clan_upgrade_level(clan_id, upgrade_type)
        if current >= 5:
            return False
        cost = UPGRADE_COSTS[upgrade_type][current]
        clan = await self.get_clan(clan_id)
        if clan['influence_treasury'] < cost:
            return False
        await self.conn.execute(
            "UPDATE clans SET influence_treasury = influence_treasury - ? WHERE id = ?",
            (cost, clan_id)
        )
        await self.conn.execute(
            "INSERT OR REPLACE INTO clan_upgrades (clan_id, upgrade_type, level) VALUES (?, ?, ?)",
            (clan_id, upgrade_type, current + 1)
        )
        await self.conn.commit()
        return True

    # ==================== АЛЬЯНСЫ ====================

    async def create_alliance(self, clan_id_1: int, clan_id_2: int):
        """Создать альянс между кланами"""
        await self._ensure_connection()
        await self.conn.execute(
            "INSERT INTO alliances (clan_id_1, clan_id_2) VALUES (?, ?)",
            (clan_id_1, clan_id_2)
        )
        await self.conn.commit()

    async def get_clan_alliances(self, clan_id: int) -> List[int]:
        """Получить ID всех союзников клана"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT clan_id_2 FROM alliances WHERE clan_id_1 = ? UNION SELECT clan_id_1 FROM alliances WHERE clan_id_2 = ?",
            (clan_id, clan_id)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    # ==================== ВОЙНЫ ====================

    async def declare_war(self, clan1_id: int, clan2_id: int) -> Dict[str, Any]:
        """Объявить войну между кланами"""
        await self._ensure_connection()
        ends_at = (datetime.now() + timedelta(hours=24)).isoformat()
        async with self.conn.execute(
            "INSERT INTO wars (clan1_id, clan2_id, ends_at) VALUES (?, ?, ?) RETURNING *",
            (clan1_id, clan2_id, ends_at)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def get_active_war_between(self, clan1_id: int, clan2_id: int) -> Optional[Dict[str, Any]]:
        """Получить активную войну между кланами"""
        await self._ensure_connection()
        async with self.conn.execute(
            """SELECT * FROM wars 
               WHERE ((clan1_id = ? AND clan2_id = ?) OR (clan1_id = ? AND clan2_id = ?))
               AND status = 'active' AND ends_at > datetime('now')""",
            (clan1_id, clan2_id, clan2_id, clan1_id)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_clan_active_wars(self, clan_id: int) -> List[Dict[str, Any]]:
        """Получить все активные войны клана"""
        await self._ensure_connection()
        async with self.conn.execute(
            """SELECT * FROM wars 
               WHERE (clan1_id = ? OR clan2_id = ?)
               AND status = 'active' AND ends_at > datetime('now')""",
            (clan_id, clan_id)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def record_war_attack(self, war_id: int, attacker_id: int, defender_id: int, damage: int):
        """Записать атаку в войне"""
        await self._ensure_connection()
        await self.conn.execute(
            "INSERT INTO war_attacks (war_id, attacker_id, defender_id, damage) VALUES (?, ?, ?, ?)",
            (war_id, attacker_id, defender_id, damage)
        )
        async with self.conn.execute("SELECT clan_id FROM users WHERE id = ?", (attacker_id,)) as cursor:
            attacker_clan = (await cursor.fetchone())[0]
        async with self.conn.execute("SELECT clan_id FROM users WHERE id = ?", (defender_id,)) as cursor:
            defender_clan = (await cursor.fetchone())[0]
        if attacker_clan:
            await self.conn.execute(
                "UPDATE wars SET clan1_damage = clan1_damage + ? WHERE id = ? AND clan1_id = ?",
                (damage, war_id, attacker_clan)
            )
            await self.conn.execute(
                "UPDATE wars SET clan2_damage = clan2_damage + ? WHERE id = ? AND clan2_id = ?",
                (damage, war_id, attacker_clan)
            )
        await self.conn.commit()

    async def finish_war(self, war_id: int) -> Optional[Dict]:
        """Завершить войну и распределить трофеи"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM wars WHERE id = ?", (war_id,)) as cursor:
            war = await cursor.fetchone()
        if not war:
            return None
        war = dict(war)
        if war['clan1_damage'] > war['clan2_damage']:
            winner_id = war['clan1_id']
            loser_id = war['clan2_id']
        elif war['clan2_damage'] > war['clan1_damage']:
            winner_id = war['clan2_id']
            loser_id = war['clan1_id']
        else:
            await self.conn.execute("UPDATE wars SET status = 'finished' WHERE id = ?", (war_id,))
            await self.conn.commit()
            return None

        await self.conn.execute("UPDATE clans SET wars_won = wars_won + 1 WHERE id = ?", (winner_id,))
        await self.conn.execute("UPDATE clans SET wars_lost = wars_lost + 1 WHERE id = ?", (loser_id,))

        loser_clan = await self.get_clan(loser_id)
        winner_clan = await self.get_clan(winner_id)
        tribute_influence = int(loser_clan['influence_treasury'] * 0.1)
        tribute_aura = loser_clan['aura_treasury'] * 0.1

        await self.conn.execute(
            "UPDATE clans SET influence_treasury = influence_treasury - ? WHERE id = ?",
            (tribute_influence, loser_id)
        )
        await self.conn.execute(
            "UPDATE clans SET aura_treasury = aura_treasury - ? WHERE id = ?",
            (tribute_aura, loser_id)
        )
        await self.conn.execute(
            "UPDATE clans SET influence_treasury = influence_treasury + ? WHERE id = ?",
            (tribute_influence, winner_id)
        )
        await self.conn.execute(
            "UPDATE clans SET aura_treasury = aura_treasury + ? WHERE id = ?",
            (tribute_aura, winner_id)
        )

        await self.conn.execute(
            "UPDATE wars SET status = 'finished', winner_id = ? WHERE id = ?",
            (winner_id, war_id)
        )
        await self.conn.commit()

        return {
            'winner_id': winner_id,
            'loser_id': loser_id,
            'tribute_influence': tribute_influence,
            'tribute_aura': tribute_aura
        }

    # ==================== СДЕЛКИ ====================

    async def create_deal(self, sender_id: int, recipient_id: int, amount_type: str, amount: float,
                          message_id: int, chat_id: int) -> Dict[str, Any]:
        """Создать предложение сделки"""
        await self._ensure_connection()
        expires_at = (datetime.now() + timedelta(minutes=3)).isoformat()
        async with self.conn.execute(
            """INSERT INTO deals (sender_id, recipient_id, amount_type, amount, expires_at, message_id, chat_id)
               VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING *""",
            (sender_id, recipient_id, amount_type, amount, expires_at, message_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def get_deal(self, deal_id: int) -> Optional[Dict[str, Any]]:
        """Получить сделку по ID"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_deal_by_message(self, message_id: int, chat_id: int) -> Optional[Dict[str, Any]]:
        """Получить сделку по ID сообщения"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM deals WHERE message_id = ? AND chat_id = ?",
            (message_id, chat_id)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_deal_status(self, deal_id: int, status: str):
        """Обновить статус сделки"""
        await self._ensure_connection()
        await self.conn.execute(
            "UPDATE deals SET status = ? WHERE id = ?",
            (status, deal_id)
        )
        await self.conn.commit()

    async def get_pending_deals(self) -> List[Dict[str, Any]]:
        """Получить все ожидающие сделки"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM deals WHERE status = 'pending' AND expires_at > datetime('now')"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_expired_deals(self) -> List[Dict[str, Any]]:
        """Получить все просроченные сделки"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM deals WHERE status = 'pending' AND expires_at <= datetime('now')"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ==================== ЦЕНА АУРЫ ====================

    async def get_current_aura_price(self) -> float:
        """Получить текущую цену ауры"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT price FROM aura_price_history ORDER BY id DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else INITIAL_AURA_PRICE

    async def set_aura_price(self, price: float):
        """Установить новую цену ауры"""
        await self._ensure_connection()
        total_influence = await self.get_total_system_influence()
        total_aura = await self.get_total_aura_in_circulation()
        await self.conn.execute(
            "INSERT INTO aura_price_history (price, total_influence, total_aura) VALUES (?, ?, ?)",
            (price, total_influence, total_aura)
        )
        await self.conn.commit()

    async def get_total_system_influence(self) -> float:
        """Получить общее количество влияния в системе"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT COALESCE(SUM(influence), 0) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0]

    async def get_total_aura_in_circulation(self) -> float:
        """Получить общее количество ауры в системе"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT COALESCE(SUM(aura), 0) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0]

    # ==================== СЕЗОНЫ ====================

    async def create_season(self, name: str, start_time: datetime, end_time: datetime, reset_type: str = 'partial') -> Dict:
        """Создать новый сезон"""
        await self._ensure_connection()
        async with self.conn.execute(
            "INSERT INTO seasons (name, start_time, end_time, reset_type, status) VALUES (?, ?, ?, ?, 'upcoming') RETURNING *",
            (name, start_time.isoformat(), end_time.isoformat(), reset_type)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def get_active_season(self) -> Optional[Dict]:
        """Получить активный сезон"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM seasons WHERE status = 'active' AND start_time <= datetime('now') AND end_time >= datetime('now')"
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_season_leaderboard(self, season_id: int, limit: int = 10) -> List[Dict]:
        """Получить топ сезона"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM users ORDER BY influence DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ==================== ЛИЧНЫЕ ЦЕЛИ ====================

    async def create_personal_goal(self, user_id: int, goal_type: str, target_value: int, reward_influence: int, reward_aura: float = 0) -> Dict:
        """Создать личную цель"""
        await self._ensure_connection()
        async with self.conn.execute(
            "INSERT INTO personal_goals (user_id, goal_type, target_value, reward_influence, reward_aura) VALUES (?, ?, ?, ?, ?) RETURNING *",
            (user_id, goal_type, target_value, reward_influence, reward_aura)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def update_personal_goal(self, goal_id: int, current_value: int) -> bool:
        """Обновить прогресс цели"""
        await self._ensure_connection()
        await self.conn.execute(
            "UPDATE personal_goals SET current_value = ? WHERE id = ?", (current_value, goal_id)
        )
        await self.conn.commit()
        return True

    async def complete_personal_goal(self, goal_id: int) -> Optional[Dict]:
        """Завершить личную цель"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM personal_goals WHERE id = ?", (goal_id,)) as cursor:
            goal = await cursor.fetchone()
        if not goal:
            return None
        goal = dict(goal)
        if goal['completed']:
            return None
        
        await self.conn.execute("UPDATE personal_goals SET completed = 1 WHERE id = ?", (goal_id,))
        if goal['reward_influence']:
            await self.add_influence(goal['user_id'], goal['reward_influence'])
        if goal['reward_aura']:
            await self.conn.execute("UPDATE users SET aura = aura + ? WHERE id = ?", (goal['reward_aura'], goal['user_id']))
        await self.conn.commit()
        return goal

    async def get_user_goals(self, user_id: int) -> List[Dict]:
        """Получить активные цели пользователя"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM personal_goals WHERE user_id = ? AND completed = 0", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ==================== ТИТУЛЫ ====================

    async def check_and_award_titles(self, user_id: int) -> List[Dict]:
        """Проверить и выдать титулы"""
        await self._ensure_connection()
        user = await self.get_user(user_id)
        if not user:
            return []
        
        awarded = []
        async with self.conn.execute("SELECT * FROM titles") as cursor:
            titles = await cursor.fetchall()
        
        for title in titles:
            async with self.conn.execute(
                "SELECT 1 FROM user_titles WHERE user_id = ? AND title_id = ?", (user_id, title['id'])
            ) as cursor:
                if await cursor.fetchone():
                    continue
            
            cond_type = title['condition_type']
            cond_val = title['condition_value']
            achieved = False
            
            if cond_type == 'aura' and user.get('aura', 0) >= cond_val:
                achieved = True
            elif cond_type == 'influence' and user.get('influence', 0) >= cond_val:
                achieved = True
            elif cond_type == 'trust' and user.get('trust', 0) >= cond_val:
                achieved = True
            elif cond_type == 'war_wins' and user.get('war_wins', 0) >= cond_val:
                achieved = True
            elif cond_type == 'alliances' and user.get('alliances_count', 0) >= cond_val:
                achieved = True
            elif cond_type == 'invites' and user.get('invites_count', 0) >= cond_val:
                achieved = True
            elif cond_type == 'deals' and user.get('deals_count', 0) >= cond_val:
                achieved = True
            
            if achieved:
                await self.conn.execute(
                    "INSERT INTO user_titles (user_id, title_id, active) VALUES (?, ?, 1)",
                    (user_id, title['id'])
                )
                awarded.append(dict(title))
        
        await self.conn.commit()
        return awarded

    async def get_user_titles(self, user_id: int) -> List[Dict]:
        """Получить все титулы пользователя"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT t.*, ut.active FROM user_titles ut JOIN titles t ON ut.title_id = t.id WHERE ut.user_id = ?",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def set_active_title(self, user_id: int, title_id: int):
        """Установить активный титул"""
        await self._ensure_connection()
        await self.conn.execute("UPDATE user_titles SET active = 0 WHERE user_id = ?", (user_id,))
        await self.conn.execute(
            "UPDATE user_titles SET active = 1 WHERE user_id = ? AND title_id = ?", (user_id, title_id)
        )
        await self.conn.commit()

    # ==================== СКИНЫ КОМАНД ====================

    async def add_command_skin(self, user_id: int, original_command: str, skin_emoji: str):
        """Добавить скин команды"""
        await self._ensure_connection()
        await self.conn.execute(
            "INSERT OR REPLACE INTO command_skins (user_id, original_command, skin_emoji) VALUES (?, ?, ?)",
            (user_id, original_command, skin_emoji)
        )
        await self.conn.commit()

    async def get_user_skins(self, user_id: int) -> List[Dict]:
        """Получить все скины пользователя"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM command_skins WHERE user_id = ?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ==================== РУЛЕТКА ====================

    async def play_roulette(self, user_id: int, bet_type: str, bet_amount: int) -> Dict:
        """Играть в рулетку: чёт/нечёт"""
        await self._ensure_connection()
        import random
        user = await self.get_user(user_id)
        if user['influence'] < bet_amount:
            return {'success': False, 'error': 'not_enough_influence'}
        
        number = random.randint(0, 36)
        is_even = number % 2 == 0
        
        win = False
        if bet_type == 'чёт' and is_even and number != 0:
            win = True
        elif bet_type == 'нечёт' and not is_even:
            win = True
        
        result = 'win' if win else 'lose'
        win_amount = bet_amount if win else 0
        
        await self.conn.execute(
            "UPDATE users SET influence = influence - ? WHERE id = ?", (bet_amount, user_id)
        )
        if win:
            await self.conn.execute(
                "UPDATE users SET influence = influence + ? WHERE id = ?", (win_amount, user_id)
            )
        
        await self.conn.execute(
            "INSERT INTO roulette_games (user_id, bet_type, bet_amount, result, win_amount) VALUES (?, ?, ?, ?, ?)",
            (user_id, bet_type, bet_amount, result, win_amount)
        )
        await self.conn.commit()
        
        return {
            'success': True,
            'number': number,
            'result': result,
            'win_amount': win_amount
        }

    # ==================== ДУЭЛИ ====================

    async def create_duel(self, player1_id: int, player2_id: int, bet_amount: int) -> Dict:
        """Создать дуэль"""
        await self._ensure_connection()
        ends_at = (datetime.now() + timedelta(minutes=5)).isoformat()
        async with self.conn.execute(
            "INSERT INTO duels (player1_id, player2_id, bet_amount, status, ends_at) VALUES (?, ?, ?, 'active', ?) RETURNING *",
            (player1_id, player2_id, bet_amount, ends_at)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def update_duel_score(self, duel_id: int, user_id: int):
        """Обновить счёт дуэли"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM duels WHERE id = ?", (duel_id,)) as cursor:
            duel = dict(await cursor.fetchone())
        
        if duel['player1_id'] == user_id:
            await self.conn.execute(
                "UPDATE duels SET player1_messages = player1_messages + 1 WHERE id = ?", (duel_id,)
            )
        else:
            await self.conn.execute(
                "UPDATE duels SET player2_messages = player2_messages + 1 WHERE id = ?", (duel_id,)
            )
        await self.conn.commit()

    async def finish_duel(self, duel_id: int) -> Optional[Dict]:
        """Завершить дуэль"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM duels WHERE id = ?", (duel_id,)) as cursor:
            duel = dict(await cursor.fetchone())
        
        if duel['player1_messages'] > duel['player2_messages']:
            winner_id = duel['player1_id']
            loser_id = duel['player2_id']
        elif duel['player2_messages'] > duel['player1_messages']:
            winner_id = duel['player2_id']
            loser_id = duel['player1_id']
        else:
            winner_id = None
            loser_id = None
        
        if winner_id and loser_id:
            await self.conn.execute(
                "UPDATE users SET influence = influence + ? WHERE id = ?", (duel['bet_amount'], winner_id)
            )
        
        await self.conn.execute(
            "UPDATE duels SET status = 'finished', winner_id = ? WHERE id = ?", (winner_id, duel_id)
        )
        await self.conn.commit()
        
        return {
            'winner_id': winner_id,
            'loser_id': loser_id,
            'player1_messages': duel['player1_messages'],
            'player2_messages': duel['player2_messages']
        }

    async def get_active_duels(self, user_id: int) -> List[Dict]:
        """Получить активные дуэли пользователя"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM duels WHERE status = 'active' AND (player1_id = ? OR player2_id = ?) AND ends_at > datetime('now')",
            (user_id, user_id)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ==================== ИНВЕСТИЦИИ ====================

    async def create_investment(self, user_id: int, amount: int, duration_hours: int = 1) -> Optional[Dict]:
        """Создать инвестицию"""
        await self._ensure_connection()
        import random
        multiplier = random.uniform(0.5, 2.0)
        ends_at = (datetime.now() + timedelta(hours=duration_hours)).isoformat()
        
        user = await self.get_user(user_id)
        if user['influence'] < amount:
            return None
        
        await self.conn.execute(
            "UPDATE users SET influence = influence - ? WHERE id = ?", (amount, user_id)
        )
        
        async with self.conn.execute(
            "INSERT INTO investments (user_id, amount, duration_hours, multiplier, ends_at) VALUES (?, ?, ?, ?, ?) RETURNING *",
            (user_id, amount, duration_hours, multiplier, ends_at)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def check_investments(self) -> List[Dict]:
        """Проверить завершённые инвестиции"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM investments WHERE status = 'active' AND ends_at <= datetime('now')"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def settle_investment(self, investment_id: int) -> Dict:
        """Рассчитать прибыль по инвестиции"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM investments WHERE id = ?", (investment_id,)) as cursor:
            inv = dict(await cursor.fetchone())
        
        profit = int(inv['amount'] * inv['multiplier']) - inv['amount']
        new_amount = int(inv['amount'] * inv['multiplier'])
        
        await self.conn.execute(
            "UPDATE users SET influence = influence + ? WHERE id = ?", (new_amount, inv['user_id'])
        )
        await self.conn.execute(
            "UPDATE investments SET status = 'completed', profit_loss = ? WHERE id = ?", (profit, investment_id)
        )
        await self.conn.commit()
        
        return {'profit': profit, 'new_amount': new_amount}

    # ==================== ЛОТЕРЕЯ ====================

    async def buy_lottery_ticket(self, user_id: int, round_number: int) -> bool:
        """Купить лотерейный билет за 10 влияния"""
        await self._ensure_connection()
        user = await self.get_user(user_id)
        if user['influence'] < 10:
            return False
        
        await self.conn.execute(
            "UPDATE users SET influence = influence - 10 WHERE id = ?", (user_id,)
        )
        await self.conn.execute(
            "UPDATE lottery_rounds SET jackpot = jackpot + 10 WHERE round_number = ?", (round_number,)
        )
        await self.conn.execute(
            "INSERT INTO lottery_tickets (user_id, round_number) VALUES (?, ?)", (user_id, round_number)
        )
        await self.conn.commit()
        return True

    async def get_lottery_round(self, round_number: int) -> Optional[Dict]:
        """Получить информацию о раунде лотереи"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM lottery_rounds WHERE round_number = ?", (round_number,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def create_lottery_round(self, round_number: int) -> Dict:
        """Создать новый раунд лотереи"""
        await self._ensure_connection()
        async with self.conn.execute(
            "INSERT INTO lottery_rounds (round_number) VALUES (?) RETURNING *", (round_number,)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def draw_lottery(self, round_number: int) -> Optional[int]:
        """Розыгрыш лотереи"""
        await self._ensure_connection()
        import random
        
        async with self.conn.execute(
            "SELECT user_id FROM lottery_tickets WHERE round_number = ?", (round_number,)
        ) as cursor:
            tickets = await cursor.fetchall()
        
        if not tickets:
            return None
        
        winner = random.choice(tickets)
        winner_id = winner['user_id']
        
        async with self.conn.execute(
            "SELECT jackpot FROM lottery_rounds WHERE round_number = ?", (round_number,)
        ) as cursor:
            jackpot = (await cursor.fetchone())['jackpot']
        
        await self.conn.execute(
            "UPDATE users SET influence = influence + ? WHERE id = ?", (jackpot, winner_id)
        )
        await self.conn.execute(
            "UPDATE lottery_rounds SET winner_id = ?, status = 'drawn', drawn_at = datetime('now') WHERE round_number = ?",
            (winner_id, round_number)
        )
        await self.conn.commit()
        
        return winner_id

    # ==================== СВАДЬБЫ КЛАНОВ ====================

    async def propose_clan_wedding(self, clan1_id: int, clan2_id: int, proposer_id: int, cost: int = 1000) -> Dict:
        """Предложить свадьбу кланов"""
        await self._ensure_connection()
        async with self.conn.execute(
            "INSERT INTO clan_weddings (clan1_id, clan2_id, cost_influence, proposer_id) VALUES (?, ?, ?, ?) RETURNING *",
            (clan1_id, clan2_id, cost, proposer_id)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def accept_clan_wedding(self, wedding_id: int) -> Optional[Dict]:
        """Принять предложение свадьбы"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM clan_weddings WHERE id = ?", (wedding_id,)) as cursor:
            wedding = dict(await cursor.fetchone())
        
        if not wedding or wedding['status'] != 'pending':
            return None
        
        clan1 = await self.get_clan(wedding['clan1_id'])
        clan2 = await self.get_clan(wedding['clan2_id'])
        
        members2 = await self.get_clan_members(wedding['clan2_id'])
        for m in members2:
            await self.add_clan_member(wedding['clan1_id'], m['id'])
        
        total_treasury = clan1['influence_treasury'] + clan2['influence_treasury']
        await self.update_clan(wedding['clan1_id'], influence_treasury=total_treasury)
        
        await self.conn.execute("DELETE FROM clans WHERE id = ?", (wedding['clan2_id'],))
        await self.conn.execute(
            "UPDATE clan_weddings SET status = 'completed' WHERE id = ?", (wedding_id,)
        )
        await self.conn.commit()
        
        return {'clan1_name': clan1['name'], 'clan2_name': clan2['name']}

    # ==================== КЛАНОВЫЕ БОССЫ ====================

    async def spawn_clan_boss(self, boss_name: str, total_health: int = 10000) -> Dict:
        """Создать кланового босса"""
        await self._ensure_connection()
        ends_at = (datetime.now() + timedelta(days=1)).isoformat()
        async with self.conn.execute(
            "INSERT INTO clan_bosses (boss_name, total_health, current_health, ends_at) VALUES (?, ?, ?, ?) RETURNING *",
            (boss_name, total_health, total_health, ends_at)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def hit_clan_boss(self, boss_id: int, user_id: int, damage: int) -> bool:
        """Атаковать кланового босса"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM clan_bosses WHERE id = ?", (boss_id,)) as cursor:
            boss = dict(await cursor.fetchone())
        
        if not boss or boss['status'] != 'active':
            return False
        
        new_health = boss['current_health'] - damage
        
        await self.conn.execute(
            """INSERT INTO clan_boss_damage (boss_id, user_id, damage) VALUES (?, ?, ?)
               ON CONFLICT(boss_id, user_id) DO UPDATE SET damage = damage + ?""",
            (boss_id, user_id, damage, damage)
        )
        
        if new_health <= 0:
            await self.conn.execute(
                "UPDATE clan_bosses SET current_health = 0, status = 'defeated' WHERE id = ?", (boss_id,)
            )
            async with self.conn.execute(
                "SELECT user_id, damage FROM clan_boss_damage WHERE boss_id = ? ORDER BY damage DESC LIMIT 10", (boss_id,)
            ) as cursor:
                contributors = await cursor.fetchall()
            
            for i, c in enumerate(contributors):
                reward = int(boss['reward_influence'] / (i + 1))
                await self.add_influence(c['user_id'], reward)
        else:
            await self.conn.execute(
                "UPDATE clan_bosses SET current_health = ? WHERE id = ?", (new_health, boss_id)
            )
        
        await self.conn.commit()
        return True

    async def get_active_boss(self) -> Optional[Dict]:
        """Получить активного босса"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM clan_bosses WHERE status = 'active' AND ends_at > datetime('now') LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ==================== КЛАНОВЫЕ ИВЕНТЫ ====================

    async def start_clan_event(self, event_type: str, duration_hours: int = 24) -> Dict:
        """Начать клановое событие"""
        await self._ensure_connection()
        start = datetime.now()
        end = start + timedelta(hours=duration_hours)
        async with self.conn.execute(
            "INSERT INTO clan_events (event_type, start_time, end_time) VALUES (?, ?, ?) RETURNING *",
            (event_type, start.isoformat(), end.isoformat())
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def get_active_clan_event(self) -> Optional[Dict]:
        """Получить активное клановое событие"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM clan_events WHERE status = 'active' AND end_time > datetime('now') LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ==================== КРЫШЕВАНИЕ ====================

    async def create_protection(self, protected_clan_id: int, protector_clan_id: int, cost_per_day: int = 50) -> Dict:
        """Создать крышу (защиту) клана"""
        await self._ensure_connection()
        async with self.conn.execute(
            "INSERT INTO clan_protections (protected_clan_id, protector_clan_id, cost_per_day) VALUES (?, ?, ?) RETURNING *",
            (protected_clan_id, protector_clan_id, cost_per_day)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def get_clan_protection(self, clan_id: int) -> Optional[Dict]:
        """Получить информацию о защите клана"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM clan_protections WHERE protected_clan_id = ? AND status = 'active'", (clan_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ==================== РЭКЕТ ====================

    async def create_racket(self, racketeer_id: int, target_id: int, amount: int) -> Dict:
        """Создать попытку рэкета"""
        await self._ensure_connection()
        import random
        success = random.random() > 0.3
        
        async with self.conn.execute(
            "INSERT INTO rackets (racketeer_id, target_id, amount, success) VALUES (?, ?, ?, ?) RETURNING *",
            (racketeer_id, target_id, amount, success)
        ) as cursor:
            row = await cursor.fetchone()
            
        if success:
            target = await self.get_user(target_id)
            if target and target['influence'] >= amount:
                await self.conn.execute(
                    "UPDATE users SET influence = influence - ? WHERE id = ?", (amount, target_id)
                )
                await self.conn.execute(
                    "UPDATE users SET influence = influence + ? WHERE id = ?", (amount, racketeer_id)
                )
        else:
            penalty = int(amount * 0.2)
            racketeer = await self.get_user(racketeer_id)
            if racketeer and racketeer['influence'] >= penalty:
                await self.conn.execute(
                    "UPDATE users SET influence = influence - ? WHERE id = ?", (penalty, racketeer_id)
                )
        
        await self.conn.execute(
            "UPDATE rackets SET status = 'resolved', resolved_at = datetime('now') WHERE id = ?", (row['id'],)
        )
        await self.conn.commit()
        return dict(row)

    # ==================== АВТОРИТЕТ ====================

    async def update_authority(self, user_id: int, points: int):
        """Обновить очки авторитета"""
        await self._ensure_connection()
        await self.conn.execute(
            """INSERT INTO authority_points (user_id, points) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET points = points + ?, updated_at = datetime('now')""",
            (user_id, points, points)
        )
        
        async with self.conn.execute("SELECT points FROM authority_points WHERE user_id = ?", (user_id,)) as cursor:
            total = (await cursor.fetchone())['points']
        
        rank = 'none'
        if total >= 1000:
            rank = 'legenda'
        elif total >= 500:
            rank = 'boss'
        elif total >= 200:
            rank = 'capo'
        elif total >= 50:
            rank = 'soldier'
        
        await self.conn.execute("UPDATE authority_points SET rank = ? WHERE user_id = ?", (rank, user_id))
        await self.conn.commit()

    async def get_authority(self, user_id: int) -> Dict:
        """Получить информацию об авторитете"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM authority_points WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else {'user_id': user_id, 'points': 0, 'rank': 'none'}

    # ==================== РАЗБОРКИ ====================

    async def create_showdown(self, challenger_id: int, challenged_id: int, stake: int = 100) -> Dict:
        """Создать разборку"""
        await self._ensure_connection()
        ends_at = (datetime.now() + timedelta(minutes=3)).isoformat()
        async with self.conn.execute(
            "INSERT INTO showdowns (challenger_id, challenged_id, stake_amount, ends_at) VALUES (?, ?, ?, ?) RETURNING *",
            (challenger_id, challenged_id, stake, ends_at)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def update_showdown_score(self, showdown_id: int, user_id: int):
        """Обновить счёт разборки"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM showdowns WHERE id = ?", (showdown_id,)) as cursor:
            sd = dict(await cursor.fetchone())
        
        if sd['challenger_id'] == user_id:
            await self.conn.execute(
                "UPDATE showdowns SET challenger_score = challenger_score + 1 WHERE id = ?", (showdown_id,)
            )
        else:
            await self.conn.execute(
                "UPDATE showdowns SET challenged_score = challenged_score + 1 WHERE id = ?", (showdown_id,)
            )
        await self.conn.commit()

    async def finish_showdown(self, showdown_id: int) -> Optional[Dict]:
        """Завершить разборку"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM showdowns WHERE id = ?", (showdown_id,)) as cursor:
            sd = dict(await cursor.fetchone())
        
        if sd['challenger_score'] > sd['challenged_score']:
            winner_id = sd['challenger_id']
        elif sd['challenged_score'] > sd['challenger_score']:
            winner_id = sd['challenged_id']
        else:
            winner_id = None
        
        if winner_id:
            await self.conn.execute(
                "UPDATE users SET influence = influence + ? WHERE id = ?", (sd['stake_amount'], winner_id)
            )
            await self.update_authority(winner_id, 10)
        
        await self.conn.execute(
            "UPDATE showdowns SET status = 'finished', winner_id = ? WHERE id = ?", (winner_id, showdown_id)
        )
        await self.conn.commit()
        
        return {'winner_id': winner_id}

    async def get_active_showdowns(self, user_id: int) -> List[Dict]:
        """Получить активные разборки пользователя"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM showdowns WHERE status = 'pending' AND (challenger_id = ? OR challenged_id = ?) AND ends_at > datetime('now')",
            (user_id, user_id)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ==================== ФЬЮЧЕРСЫ ====================

    async def create_future(self, user_id: int, amount: float, duration_hours: int = 24) -> Optional[Dict]:
        """Купить фьючерс на ауру"""
        await self._ensure_connection()
        current_price = await self.get_current_aura_price()
        cost = int(amount * current_price * 0.1)
        
        user = await self.get_user(user_id)
        if user['influence'] < cost:
            return None
        
        await self.conn.execute(
            "UPDATE users SET influence = influence - ? WHERE id = ?", (cost, user_id)
        )
        
        ends_at = (datetime.now() + timedelta(hours=duration_hours)).isoformat()
        async with self.conn.execute(
            "INSERT INTO futures (user_id, amount, strike_price, current_price_at_open, expires_at) VALUES (?, ?, ?, ?, ?) RETURNING *",
            (user_id, amount, current_price, current_price, ends_at)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def settle_futures(self) -> List[Dict]:
        """Рассчитать фьючерсы"""
        await self._ensure_connection()
        current_price = await self.get_current_aura_price()
        
        async with self.conn.execute(
            "SELECT * FROM futures WHERE status = 'active' AND expires_at <= datetime('now')"
        ) as cursor:
            futures = await cursor.fetchall()
        
        results = []
        for f in futures:
            f = dict(f)
            if current_price > f['strike_price']:
                profit = int((current_price - f['strike_price']) * f['amount'])
                await self.conn.execute(
                    "UPDATE users SET influence = influence + ? WHERE id = ?", (profit, f['user_id'])
                )
                await self.conn.execute(
                    "UPDATE futures SET profit_loss = ?, status = 'settled' WHERE id = ?", (profit, f['id'])
                )
                results.append({'user_id': f['user_id'], 'profit': profit})
            else:
                await self.conn.execute(
                    "UPDATE futures SET profit_loss = 0, status = 'settled' WHERE id = ?", (f['id'],)
                )
        
        await self.conn.commit()
        return results

    # ==================== СТРАХОВКИ ====================

    async def buy_insurance(self, user_id: int, coverage: int, premium: int) -> Optional[Dict]:
        """Купить страховку от потери влияния"""
        await self._ensure_connection()
        user = await self.get_user(user_id)
        if user['influence'] < premium:
            return None
        
        await self.conn.execute(
            "UPDATE users SET influence = influence - ? WHERE id = ?", (premium, user_id)
        )
        
        expires_at = (datetime.now() + timedelta(days=30)).isoformat()
        async with self.conn.execute(
            "INSERT INTO insurance_policies (user_id, coverage_amount, premium, expires_at) VALUES (?, ?, ?, ?) RETURNING *",
            (user_id, coverage, premium, expires_at)
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    async def get_active_insurance(self, user_id: int) -> Optional[Dict]:
        """Получить активную страховку пользователя"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM insurance_policies WHERE user_id = ? AND active = 1 AND expires_at > datetime('now')",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def claim_insurance(self, user_id: int, loss_amount: int) -> int:
        """Получить компенсацию по страховке"""
        await self._ensure_connection()
        insurance = await self.get_active_insurance(user_id)
        if not insurance:
            return 0
        
        compensation = min(insurance['coverage_amount'], loss_amount)
        await self.conn.execute(
            "UPDATE users SET influence = influence + ? WHERE id = ?", (compensation, user_id)
        )
        await self.conn.execute(
            "UPDATE insurance_policies SET active = 0 WHERE id = ?", (insurance['id'],)
        )
        await self.conn.commit()
        
        return compensation

    # ==================== ОБМЕН В КЛАНЕ ====================

    async def get_clan_exchange_rate(self, clan_id: int) -> Dict:
        """Получить курс обмена в клане"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM clan_exchange_rates WHERE clan_id = ?", (clan_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
        
        await self.conn.execute(
            "INSERT INTO clan_exchange_rates (clan_id) VALUES (?)", (clan_id,)
        )
        await self.conn.commit()
        return {'clan_id': clan_id, 'influence_to_aura_rate': 0.1, 'aura_to_influence_rate': 10}

    async def clan_exchange(self, user_id: int, clan_id: int, from_type: str, amount: float) -> Optional[Dict]:
        """Обмен внутри клана без комиссии"""
        await self._ensure_connection()
        rate = await self.get_clan_exchange_rate(clan_id)
        
        user = await self.get_user(user_id)
        if not user or user['clan_id'] != clan_id:
            return None
        
        if from_type == 'influence':
            if user['influence'] < amount:
                return None
            aura_amount = amount * rate['influence_to_aura_rate']
            await self.conn.execute(
                "UPDATE users SET influence = influence - ?, aura = aura + ? WHERE id = ?",
                (amount, aura_amount, user_id)
            )
            return {'from_type': 'influence', 'to_type': 'aura', 'from_amount': amount, 'to_amount': aura_amount}
        else:
            if user['aura'] < amount:
                return None
            influence_amount = amount * rate['aura_to_influence_rate']
            await self.conn.execute(
                "UPDATE users SET aura = aura - ?, influence = influence + ? WHERE id = ?",
                (amount, influence_amount, user_id)
            )
            return {'from_type': 'aura', 'to_type': 'influence', 'from_amount': amount, 'to_amount': influence_amount}

    # ==================== СЕКРЕТНЫЕ КОМАНДЫ ====================

    async def get_secret_response(self, trigger: str) -> Optional[Dict]:
        """Получить ответ на скрытую команду"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM secret_commands WHERE trigger_word = ?", (trigger,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ==================== СЛУЧАЙНЫЕ БОНУСЫ ====================

    async def give_random_bonus(self, user_id: int) -> bool:
        """Случайный бонус за сообщение (редкий)"""
        await self._ensure_connection()
        import random
        if random.random() > 0.01:
            return False
        
        bonus = random.randint(5, 20)
        await self.add_influence(user_id, bonus)
        
        await self.conn.execute(
            "INSERT INTO random_bonuses (user_id, bonus_type, bonus_amount) VALUES (?, 'message', ?)",
            (user_id, bonus)
        )
        await self.conn.commit()
        return True

    # ==================== ДНИ РОЖДЕНИЯ ====================

    async def set_birthday(self, user_id: int, birthday: str):
        """Установить день рождения"""
        await self._ensure_connection()
        await self.conn.execute(
            "INSERT OR REPLACE INTO player_birthdays (user_id, birthday) VALUES (?, ?)",
            (user_id, birthday)
        )
        await self.conn.commit()

    async def get_birthday_users(self) -> List[Dict]:
        """Получить игроков с днём рождения сегодня"""
        await self._ensure_connection()
        today = datetime.now().strftime('%m-%d')
        async with self.conn.execute(
            "SELECT pb.*, u.nickname FROM player_birthdays pb JOIN users u ON pb.user_id = u.id WHERE strftime('%m-%d', pb.birthday) = ?",
            (today,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def birthday_wish(self, birthday_user_id: int, wisher_id: int, wish_text: str = "Поздравляю!"):
        """Пожелать с днём рождения"""
        await self._ensure_connection()
        await self.conn.execute(
            "INSERT INTO birthday_wishes (birthday_user_id, wisher_id, wish_text) VALUES (?, ?, ?)",
            (birthday_user_id, wisher_id, wish_text)
        )
        await self.conn.execute("UPDATE users SET trust = trust + 1 WHERE id = ?", (birthday_user_id,))
        await self.conn.commit()

    # ==================== ДЕНЬ ДОНОРА ====================

    async def donor_transfer(self, from_user_id: int, to_user_id: int, amount: int) -> bool:
        """Передать влияние без сделки (раз в день)"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT 1 FROM donor_transfers WHERE from_user_id = ? AND transferred_at > datetime('now', '-1 day')",
            (from_user_id,)
        ) as cursor:
            if await cursor.fetchone():
                return False
        
        user = await self.get_user(from_user_id)
        if user['influence'] < amount:
            return False
        
        await self.conn.execute(
            "UPDATE users SET influence = influence - ? WHERE id = ?", (amount, from_user_id)
        )
        await self.conn.execute(
            "UPDATE users SET influence = influence + ? WHERE id = ?", (amount, to_user_id)
        )
        await self.conn.execute(
            "INSERT INTO donor_transfers (from_user_id, to_user_id, amount) VALUES (?, ?, ?)",
            (from_user_id, to_user_id, amount)
        )
        await self.conn.commit()
        return True

    # ==================== НАЛЁТ НА КАЗНУ ====================

    async def treasury_raid(self) -> Optional[Dict]:
        """Налёт на казну случайного клана"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM clans WHERE influence_treasury > 0") as cursor:
            clans = await cursor.fetchall()
        
        if not clans:
            return None
        
        import random
        target_clan = random.choice(clans)
        loss = int(target_clan['influence_treasury'] * 0.1)
        
        await self.conn.execute(
            "UPDATE clans SET influence_treasury = influence_treasury - ? WHERE id = ?",
            (loss, target_clan['id'])
        )
        await self.conn.commit()
        
        return {'clan_name': target_clan['name'], 'loss': loss}

    # ==================== НАПОМИНАНИЯ ====================

    async def create_reminder(self, user_id: int, reminder_type: str, message: str, scheduled_at: datetime):
        """Создать напоминание"""
        await self._ensure_connection()
        await self.conn.execute(
            "INSERT INTO reminders (user_id, reminder_type, message, scheduled_at) VALUES (?, ?, ?, ?)",
            (user_id, reminder_type, message, scheduled_at.isoformat())
        )
        await self.conn.commit()

    async def get_pending_reminders(self) -> List[Dict]:
        """Получить ожидающие напоминания"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM reminders WHERE sent = 0 AND scheduled_at <= datetime('now')"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def mark_reminder_sent(self, reminder_id: int):
        """Отметить напоминание как отправленное"""
        await self._ensure_connection()
        await self.conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
        await self.conn.commit()

    # ==================== ПРИЗРАКИ ====================

    async def check_ghosts(self, days_inactive: int = 7) -> List[Dict]:
        """Найти неактивных игроков"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM users WHERE last_activity < datetime('now', '-' || ? || ' days')",
            (days_inactive,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ==================== РАСЧЁТ ЦЕНЫ АУРЫ ====================

    async def calculate_aura_price(self) -> float:
        """Рассчитать справедливую цену ауры"""
        await self._ensure_connection()
        total_influence = await self.get_total_system_influence()
        total_aura = await self.get_total_aura_in_circulation()
        if total_aura == 0:
            return INITIAL_AURA_PRICE
        return total_influence / total_aura

    # ==================== КУЛДАУНЫ ====================

    async def check_cooldown(self, from_user: int, to_user: int, action: str, seconds: int) -> bool:
        """Проверить, не прошло ли время кулдауна"""
        await self._ensure_connection()
        async with self.conn.execute(
            """SELECT last_action FROM cooldowns 
               WHERE from_user_id = ? AND to_user_id = ? AND action_type = ?
               AND last_action > datetime('now', ?)""",
            (from_user, to_user, action, f'-{seconds} seconds')
        ) as cursor:
            row = await cursor.fetchone()
            return row is None

    async def set_cooldown(self, from_user: int, to_user: int, action: str):
        """Установить кулдаун"""
        await self._ensure_connection()
        await self.conn.execute(
            """INSERT INTO cooldowns (from_user_id, to_user_id, action_type, last_action)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET last_action = CURRENT_TIMESTAMP""",
            (from_user, to_user, action)
        )
        await self.conn.commit()

    # ==================== ЗАЩИТА ОТ СПАМА ====================

    async def check_spam(self, user_id: int, cooldown_seconds: float) -> Tuple[bool, Optional[datetime]]:
        """Проверяет, является ли сообщение спамом"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT last_message_time FROM spam_protection WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return False, None
            
            last_time = datetime.fromisoformat(row[0])
            now = datetime.now()
            time_diff = (now - last_time).total_seconds()
            
            if time_diff < cooldown_seconds:
                return True, last_time
            return False, last_time

    async def update_spam_record(self, user_id: int):
        """Обновляет время последнего сообщения пользователя"""
        await self._ensure_connection()
        await self.conn.execute(
            """INSERT INTO spam_protection (user_id, last_message_time, message_count)
               VALUES (?, CURRENT_TIMESTAMP, 1)
               ON CONFLICT(user_id) DO UPDATE SET 
                   last_message_time = CURRENT_TIMESTAMP,
                   message_count = message_count + 1""",
            (user_id,)
        )
        await self.conn.commit()

    # ==================== ПРИГЛАШЕНИЯ НОВИЧКОВ ====================

    async def create_invite(self, inviter_id: int, newbie_id: int):
        """Создать приглашение новичка"""
        await self._ensure_connection()
        await self.conn.execute(
            "INSERT INTO newbie_invites (inviter_id, newbie_id) VALUES (?, ?)",
            (inviter_id, newbie_id)
        )
        await self.conn.commit()

    async def increment_newbie_messages(self, newbie_id: int):
        """Увеличить счётчик сообщений новичка"""
        await self._ensure_connection()
        await self.conn.execute(
            "UPDATE newbie_invites SET messages_count = messages_count + 1 WHERE newbie_id = ? AND status = 'pending'",
            (newbie_id,)
        )
        await self.conn.commit()

    async def check_invite_success(self, newbie_id: int) -> Optional[int]:
        """Проверить, выполнены ли условия для успешного приглашения"""
        await self._ensure_connection()
        async with self.conn.execute(
            """SELECT inviter_id, messages_count, 
                      julianday('now') - julianday(invite_time) as days
               FROM newbie_invites
               WHERE newbie_id = ? AND status = 'pending'""",
            (newbie_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            inviter_id, msgs, days = row
            if msgs >= NEWBIE_MIN_MESSAGES or days >= NEWBIE_MAX_HOURS / 24.0:
                await self.conn.execute(
                    "UPDATE newbie_invites SET status = 'success' WHERE newbie_id = ?",
                    (newbie_id,)
                )
                await self.conn.commit()
                return inviter_id
            return None

    async def get_invite_info(self, newbie_id: int) -> Optional[Dict]:
        """Получить информацию о приглашении новичка"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM newbie_invites WHERE newbie_id = ? AND status = 'pending'",
            (newbie_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ==================== ИСТОРИЯ СООБЩЕНИЙ ====================

    async def log_message(self, user_id: int, chat_id: int, reply_to_user_id: Optional[int] = None,
                          mentioned_users: Optional[List[int]] = None):
        """Записать сообщение в историю"""
        await self._ensure_connection()
        mentioned = ','.join(map(str, mentioned_users)) if mentioned_users else None
        await self.conn.execute(
            """INSERT INTO message_history (user_id, chat_id, reply_to_user_id, mentioned_users)
               VALUES (?, ?, ?, ?)""",
            (user_id, chat_id, reply_to_user_id, mentioned)
        )
        await self.conn.commit()

    async def get_user_messages_last_minutes(self, user_id: int, minutes: int) -> int:
        """Получить количество сообщений пользователя за последние N минут"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT COUNT(*) FROM message_history WHERE user_id = ? AND timestamp > datetime('now', ?)",
            (user_id, f'-{minutes} minutes')
        ) as cursor:
            row = await cursor.fetchone()
            return row[0]

    # ==================== ДОСТИЖЕНИЯ ====================

    async def check_achievements(self, user_id: int) -> List[Dict]:
        """Проверить и выдать новые достижения"""
        await self._ensure_connection()
        user = await self.get_user(user_id)
        if not user:
            return []
        earned = []
        async with self.conn.execute(
            "SELECT * FROM achievements WHERE id NOT IN (SELECT achievement_id FROM user_achievements WHERE user_id = ?)",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                ach = dict(row)
                cond = ach['condition_type']
                val = ach['condition_value']
                achieved = False
                if cond == 'influence' and user['influence'] >= val:
                    achieved = True
                elif cond == 'deals' and user['deals_count'] >= val:
                    achieved = True
                elif cond == 'war_wins' and user['war_wins'] >= val:
                    achieved = True
                elif cond == 'alliances' and user['alliances_count'] >= val:
                    achieved = True
                elif cond == 'invites' and user['invites_count'] >= val:
                    achieved = True
                elif cond == 'aura' and user['aura'] >= val:
                    achieved = True
                elif cond == 'trust' and user['trust'] >= val:
                    achieved = True
                if achieved:
                    await self.conn.execute(
                        "INSERT INTO user_achievements (user_id, achievement_id) VALUES (?, ?)",
                        (user_id, ach['id'])
                    )
                    if ach['reward_influence']:
                        await self.add_influence(user_id, ach['reward_influence'])
                    if ach['reward_aura']:
                        await self.conn.execute("UPDATE users SET aura = aura + ? WHERE id = ?", (ach['reward_aura'], user_id))
                    earned.append(ach)
        await self.conn.commit()
        return earned

    # ==================== КАРМА ====================

    async def give_karma(self, from_user: int, to_user: int, value: int) -> bool:
        """Поставить карму (+1 или -1)"""
        await self._ensure_connection()
        if value not in (1, -1):
            return False
        async with self.conn.execute(
            "SELECT 1 FROM karma WHERE from_user_id = ? AND to_user_id = ? AND given_at > datetime('now', '-1 day')",
            (from_user, to_user)
        ) as cursor:
            if await cursor.fetchone():
                return False
        await self.conn.execute(
            "INSERT INTO karma (from_user_id, to_user_id, value) VALUES (?, ?, ?)",
            (from_user, to_user, value)
        )
        await self.conn.commit()
        return True

    async def get_karma(self, user_id: int) -> int:
        """Получить сумму кармы пользователя"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT COALESCE(SUM(value), 0) FROM karma WHERE to_user_id = ?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0]

    # ==================== ВКЛАДЫ ====================

    async def create_deposit(self, user_id: int, amount: int, days: int) -> bool:
        """Создать вклад"""
        await self._ensure_connection()
        if amount <= 0:
            return False
        user = await self.get_user(user_id)
        if user['influence'] < amount:
            return False
        matured_at = (datetime.now() + timedelta(days=days)).isoformat()
        await self.conn.execute(
            "UPDATE users SET influence = influence - ? WHERE id = ?",
            (amount, user_id)
        )
        await self.conn.execute(
            "INSERT INTO deposits (user_id, amount_influence, interest_rate, matured_at) VALUES (?, ?, ?, ?)",
            (user_id, amount, DEPOSIT_INTEREST, matured_at)
        )
        await self.conn.commit()
        return True

    async def withdraw_deposit(self, deposit_id: int) -> Optional[int]:
        """Закрыть вклад и получить средства"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM deposits WHERE id = ? AND status = 'active'", (deposit_id,)) as cursor:
            dep = await cursor.fetchone()
        if not dep:
            return None
        dep = dict(dep)
        now = datetime.now()
        mature = datetime.fromisoformat(dep['matured_at'])
        if now < mature:
            amount = dep['amount_influence']
        else:
            amount = int(dep['amount_influence'] * (1 + dep['interest_rate']))
        await self.conn.execute(
            "UPDATE deposits SET status = 'withdrawn' WHERE id = ?",
            (deposit_id,)
        )
        await self.conn.execute(
            "UPDATE users SET influence = influence + ? WHERE id = ?",
            (amount, dep['user_id'])
        )
        await self.conn.commit()
        return amount

    # ==================== КРЕДИТЫ ====================

    async def create_loan(self, user_id: int, amount: int, collateral_aura: float, days: int) -> bool:
        """Взять кредит"""
        await self._ensure_connection()
        user = await self.get_user(user_id)
        if user['aura'] < collateral_aura:
            return False
        due_at = (datetime.now() + timedelta(days=days)).isoformat()
        await self.conn.execute(
            "UPDATE users SET aura = aura - ? WHERE id = ?",
            (collateral_aura, user_id)
        )
        await self.conn.execute(
            "INSERT INTO loans (user_id, amount_influence, collateral_aura, due_at) VALUES (?, ?, ?, ?)",
            (user_id, amount, collateral_aura, due_at)
        )
        await self.add_influence(user_id, amount)
        await self.conn.commit()
        return True

    async def repay_loan(self, loan_id: int) -> bool:
        """Погасить кредит"""
        await self._ensure_connection()
        async with self.conn.execute("SELECT * FROM loans WHERE id = ? AND status = 'active'", (loan_id,)) as cursor:
            loan = await cursor.fetchone()
        if not loan:
            return False
        loan = dict(loan)
        user = await self.get_user(loan['user_id'])
        total = int(loan['amount_influence'] * (1 + LOAN_INTEREST))
        if user['influence'] < total:
            return False
        await self.conn.execute(
            "UPDATE users SET influence = influence - ? WHERE id = ?",
            (total, loan['user_id'])
        )
        await self.conn.execute(
            "UPDATE users SET aura = aura + ? WHERE id = ?",
            (loan['collateral_aura'], loan['user_id'])
        )
        await self.conn.execute(
            "UPDATE loans SET status = 'repaid' WHERE id = ?",
            (loan_id,)
        )
        await self.conn.commit()
        return True

    # ==================== ШПИОНАЖ ====================

    async def start_espionage(self, from_clan: int, to_clan: int, cost: int) -> bool:
        """Начать шпионаж"""
        await self._ensure_connection()
        clan = await self.get_clan(from_clan)
        if clan['influence_treasury'] < cost:
            return False
        await self.conn.execute(
            "UPDATE clans SET influence_treasury = influence_treasury - ? WHERE id = ?",
            (cost, from_clan)
        )
        ends_at = (datetime.now() + timedelta(hours=24)).isoformat()
        await self.conn.execute(
            "INSERT INTO espionage (from_clan_id, to_clan_id, ends_at) VALUES (?, ?, ?)",
            (from_clan, to_clan, ends_at)
        )
        await self.conn.commit()
        return True

    # ==================== ГЛОБАЛЬНЫЕ СОБЫТИЯ ====================

    async def get_active_event(self) -> Optional[Dict]:
        """Получить активное глобальное событие"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM global_events WHERE active = 1 AND ends_at > datetime('now') ORDER BY started_at DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def create_global_event(self, event_type: str, description: str, multiplier: float, ends_at: datetime) -> Dict:
        """Создать глобальное событие"""
        await self._ensure_connection()
        await self.conn.execute("UPDATE global_events SET active = 0 WHERE active = 1")
        
        async with self.conn.execute(
            "INSERT INTO global_events (event_type, description, multiplier, ends_at) VALUES (?, ?, ?, ?) RETURNING *",
            (event_type, description, multiplier, ends_at.isoformat())
        ) as cursor:
            row = await cursor.fetchone()
            await self.conn.commit()
            return dict(row)

    # ==================== ТУРНИРЫ ====================

    async def start_tournament(self, name: str, duration_hours: int, prize_inf: int, prize_aura: float):
        """Начать турнир"""
        await self._ensure_connection()
        start = datetime.now().isoformat()
        end = (datetime.now() + timedelta(hours=duration_hours)).isoformat()
        await self.conn.execute(
            "INSERT INTO tournaments (name, start_time, end_time, prize_pool_influence, prize_pool_aura, status) VALUES (?, ?, ?, ?, ?, 'active')",
            (name, start, end, prize_inf, prize_aura)
        )
        await self.conn.commit()

    async def join_tournament(self, user_id: int, tournament_id: int):
        """Присоединиться к турниру"""
        await self._ensure_connection()
        await self.conn.execute(
            "INSERT OR IGNORE INTO tournament_participants (tournament_id, user_id) VALUES (?, ?)",
            (tournament_id, user_id)
        )
        await self.conn.commit()

    async def update_tournament_score(self, user_id: int, points: int):
        """Обновить счёт в турнире"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT id FROM tournaments WHERE status = 'active' AND start_time <= datetime('now') AND end_time >= datetime('now')"
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            await self.conn.execute(
                "UPDATE tournament_participants SET score = score + ? WHERE tournament_id = ? AND user_id = ?",
                (points, row[0], user_id)
            )
            await self.conn.commit()

    async def finish_tournament(self, tournament_id: int):
        """Завершить турнир и распределить призы"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT user_id, score FROM tournament_participants WHERE tournament_id = ? ORDER BY score DESC LIMIT 3",
            (tournament_id,)
        ) as cursor:
            winners = await cursor.fetchall()
        if not winners:
            return
        tour = await self.conn.execute("SELECT prize_pool_influence, prize_pool_aura FROM tournaments WHERE id = ?", (tournament_id,))
        tour_row = await tour.fetchone()
        prize_inf = tour_row['prize_pool_influence']
        prize_aura = tour_row['prize_pool_aura']
        shares = [0.5, 0.3, 0.2]
        for i, row in enumerate(winners):
            user_id = row['user_id']
            share = shares[i] if i < len(shares) else 0
            inf_prize = int(prize_inf * share)
            aura_prize = prize_aura * share
            await self.conn.execute(
                "UPDATE tournament_participants SET rank = ?, prize_influence = ?, prize_aura = ? WHERE tournament_id = ? AND user_id = ?",
                (i+1, inf_prize, aura_prize, tournament_id, user_id)
            )
            await self.add_influence(user_id, inf_prize)
            if aura_prize > 0:
                await self.conn.execute("UPDATE users SET aura = aura + ? WHERE id = ?", (aura_prize, user_id))
        await self.conn.execute("UPDATE tournaments SET status = 'finished' WHERE id = ?", (tournament_id,))
        await self.conn.commit()

    # ==================== СИЛА КЛАНОВ ====================

    async def get_clan_power(self, clan_id: int) -> float:
        """Рассчитать силу клана"""
        await self._ensure_connection()
        clan = await self.get_clan(clan_id)
        if not clan:
            return 0
        members = await self.get_clan_members(clan_id)
        price = await self.get_current_aura_price()
        members_influence = sum(m['influence'] for m in members)
        members_aura = sum(m['aura'] for m in members)
        power = (
            clan['influence_treasury'] +
            clan['aura_treasury'] * price +
            members_influence * 0.5 +
            members_aura * price * 0.3 +
            clan['wars_won'] * 100 -
            clan['wars_lost'] * 50
        )
        return power

    async def get_clans_by_power(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Получить рейтинг кланов по силе"""
        await self._ensure_connection()
        all_clans = await self.get_all_clans()
        clans_with_power = []
        for clan in all_clans:
            power = await self.get_clan_power(clan['id'])
            clan['power'] = power
            clans_with_power.append(clan)
        clans_with_power.sort(key=lambda x: x['power'], reverse=True)
        return clans_with_power[:limit]

    # ==================== ТОПЫ ====================

    async def get_top_influence(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Топ по влиянию"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT id, nickname, influence, aura, trust FROM users ORDER BY influence DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_top_trust(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Топ по доверию"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT id, nickname, trust FROM users ORDER BY trust DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_top_aura(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Топ по ауре"""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT id, nickname, aura FROM users ORDER BY aura DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_top_clans(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Топ кланов по богатству"""
        await self._ensure_connection()
        price = await self.get_current_aura_price()
        async with self.conn.execute(
            """SELECT c.*, COUNT(cm.user_id) as member_count,
                      (c.influence_treasury + c.aura_treasury * ?) as total_wealth
               FROM clans c
               LEFT JOIN clan_members cm ON c.id = cm.clan_id
               GROUP BY c.id
               ORDER BY total_wealth DESC
               LIMIT ?""",
            (price, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ==================== ОЧИСТКА ====================

    async def clean_expired_deals(self) -> int:
        """Очистить просроченные сделки"""
        await self._ensure_connection()
        async with self.conn.execute(
            "UPDATE deals SET status = 'cancelled' WHERE status = 'pending' AND expires_at < datetime('now') RETURNING id"
        ) as cursor:
            rows = await cursor.fetchall()
            await self.conn.commit()
            return len(rows)

    async def clean_old_cooldowns(self, hours: int = 24):
        """Очистить старые кулдауны"""
        await self._ensure_connection()
        await self.conn.execute(
            "DELETE FROM cooldowns WHERE last_action < datetime('now', ?)",
            (f'-{hours} hours',)
        )
        await self.conn.commit()


# Global instance
db = Database()
