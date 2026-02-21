import re
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from database import db
import random
from config import *

# Импорт констант для защиты от спама
from config import SPAM_COOLDOWN, SPAM_PENALTY


class GameLogic:
    @staticmethod
    async def process_message(
        user_id: int,
        username: str,
        nickname: str,
        chat_id: int,
        message_text: str,
        reply_to_user_id: Optional[int] = None,
        mentioned_users: Optional[List[int]] = None,
        message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Обрабатывает сообщение:
        - начисляет влияние
        - проверяет инвайты
        - ежедневный бонус
        - проверяет специальные триггеры (доверяю, сделка, создание клана, аура, профиль, топ,
          альянсы, войны, достижения, карма, банк, шпионаж, турниры, уровень)
        - проверяет защиту от спама
        """
        result = {
            'influence_gained': 0,
            'events': [],
            'profile_request': False,
            'top_request': False,
            'help_request': False,
            'clan_power_request': False,
            'war_status_request': None,
            'clan_profile_request': None,
            'spam_penalty': False,  # Новый флаг для определения спама
        }

        # Получаем или создаём пользователя
        user = await db.get_or_create_user(user_id, username, nickname)

        # Проверка на спам
        is_spam, last_time = await db.check_spam(user_id, SPAM_COOLDOWN)
        if is_spam:
            # Штраф за спам - 3 влияния
            penalty = SPAM_PENALTY
            current_influence = user.get('influence', 0)
            if current_influence >= penalty:
                await db.add_influence(user_id, -penalty)
                result['spam_penalty'] = True
                result['events'].append({
                    'type': 'spam_penalty',
                    'penalty': penalty,
                    'time_diff': SPAM_COOLDOWN
                })
                # Всё равно обновляем время последнего сообщения
                await db.update_spam_record(user_id)
                # Возвращаем результат без начисления влияния за сообщение
                return result
        
        # Обновляем время последнего сообщения
        await db.update_spam_record(user_id)

        # Проверяем активное глобальное событие (для модификаторов)
        active_event = await db.get_active_event()
        event_multiplier = active_event['multiplier'] if active_event else 1.0

        # Проверяем успешность инвайта (новичок выполнил условия)
        if user.get('invited_by'):
            inviter_id = await db.check_invite_success(user_id)
            if inviter_id:
                await db.add_influence(inviter_id, INFLUENCE_PER_INVITE)
                # Увеличиваем счётчик приглашений у пригласившего
                inviter = await db.get_user(inviter_id)
                await db.update_user(inviter_id, invites_count=inviter['invites_count'] + 1)
                result['events'].append({
                    'type': 'invite_success',
                    'inviter_id': inviter_id,
                    'newbie_id': user_id,
                })

        # --- Начисление влияния за активность ---
        gain = INFLUENCE_PER_MESSAGE  # базовое сообщение

        # Ответ на сообщение
        if reply_to_user_id and reply_to_user_id != user_id:
            recipient = await db.get_user(reply_to_user_id)
            if recipient and recipient['influence'] > 0:
                if await db.check_cooldown(user_id, reply_to_user_id, 'reply', REPLY_COOLDOWN):
                    gain += INFLUENCE_PER_REPLY
                    await db.set_cooldown(user_id, reply_to_user_id, 'reply')

        # Упоминания
        if mentioned_users:
            for mid in mentioned_users:
                if mid != user_id:
                    if await db.check_cooldown(user_id, mid, 'mention', MENTION_COOLDOWN):
                        gain += INFLUENCE_PER_MENTION
                        await db.set_cooldown(user_id, mid, 'mention')

        # Применяем множитель события
        gain = int(gain * event_multiplier)

        if gain > 0:
            await db.add_influence(user_id, gain)
            result['influence_gained'] = gain

        # --- Ежедневный бонус (первое сообщение за 24 часа) ---
        last_activity = user.get('last_activity')
        now = datetime.now()
        give_bonus = False

        if last_activity is None:
            give_bonus = True
        else:
            try:
                last_time = datetime.fromisoformat(last_activity)
                if now - last_time > timedelta(hours=24):
                    give_bonus = True
            except:
                give_bonus = True

        if give_bonus:
            bonus = DAILY_BONUS
            await db.add_influence(user_id, bonus)
            result['influence_gained'] += bonus
            result['events'].append({
                'type': 'daily_bonus',
                'amount': bonus
            })

        # Обновляем время последней активности
        await db.update_user(
            user_id,
            last_activity=now.isoformat(),
            messages_count=user['messages_count'] + 1
        )

        # Добавляем опыт за сообщение
        exp_gain = max(1, gain // 2)
        await db.add_experience(user_id, exp_gain)

        # Логируем сообщение
        await db.log_message(user_id, chat_id, reply_to_user_id, mentioned_users)

        # --- Обработка специальных текстовых триггеров (без /) ---
        text_lower = message_text.lower().strip()

        # Профиль
        if text_lower in ('профиль', 'profile', 'мой профиль'):
            result['profile_request'] = True

        # Топ
        if text_lower in ('топ', 'top', 'рейтинг'):
            result['top_request'] = True

        # Помощь
        if text_lower in ('помощь', 'help', 'правила', 'как играть'):
            result['help_request'] = True

        # Доверие
        if text_lower == 'доверяю' and reply_to_user_id:
            trust_event = await GameLogic._handle_trust(reply_to_user_id, user_id)
            if trust_event:
                result['events'].append(trust_event)

        # Сделка: @username количество влияние/аура
        deal_match = re.search(r'@(\w+)\s+(\d+(?:\.\d+)?)\s+(влияние|ауру|ауры|influence|aura)', text_lower)
        if deal_match and message_id:
            deal_event = await GameLogic._handle_deal_proposal(
                user_id, nickname, deal_match, chat_id, message_id
            )
            if deal_event:
                result['events'].append(deal_event)

        # Создание клана
        clan_match = re.search(r'создать клан [""](.+?)[""]|создать клан (.+)', text_lower)
        if clan_match:
            clan_name = clan_match.group(1) or clan_match.group(2)
            clan_event = await GameLogic._handle_clan_creation(user_id, nickname, clan_name)
            if clan_event:
                result['events'].append(clan_event)

        # Покупка/продажа ауры
        buy_match = re.search(r'купить аура\s+(\d+(?:\.\d+)?)', text_lower)
        if buy_match:
            amount = float(buy_match.group(1))
            aura_event = await GameLogic._handle_aura_buy(user_id, nickname, amount)
            if aura_event:
                result['events'].append(aura_event)

        sell_match = re.search(r'продать аура\s+(\d+(?:\.\d+)?)', text_lower)
        if sell_match:
            amount = float(sell_match.group(1))
            aura_event = await GameLogic._handle_aura_sell(user_id, nickname, amount)
            if aura_event:
                result['events'].append(aura_event)

        # Альянсы
        alliance_match = re.search(r'(?:создать )?альянс(?:\s+с)?\s+(.+)', text_lower)
        if alliance_match:
            target_clan = alliance_match.group(1).strip()
            alliance_event = await GameLogic._handle_alliance_creation(user_id, nickname, target_clan, message_text)
            if alliance_event:
                result['events'].append(alliance_event)

        # Профиль клана
        clan_profile_match = re.search(r'(?:профиль клана|клан)\s+(.+)', text_lower)
        if clan_profile_match:
            clan_name = clan_profile_match.group(1).strip()
            clan = await db.get_clan_by_name(clan_name)
            if clan:
                result['clan_profile_request'] = clan['id']
            else:
                result['events'].append({'type': 'clan_error', 'error': 'not_found', 'name': clan_name})

        # Рейтинг силы кланов
        if text_lower in ('рейтинг кланов', 'сила кланов', 'топ кланов по силе'):
            result['clan_power_request'] = True

        # Объявление войны
        war_declare_match = re.search(r'(?:объявить войну|война)\s+(.+)', text_lower)
        if war_declare_match:
            target_clan = war_declare_match.group(1).strip()
            war_event = await GameLogic._handle_war_declaration(user_id, nickname, target_clan)
            if war_event:
                result['events'].append(war_event)

        # Атака в войне
        war_attack_match = re.search(r'(?:атаковать|атака)\s+@?(\w+)', text_lower)
        if war_attack_match:
            target = war_attack_match.group(1).strip()
            attack_event = await GameLogic._handle_war_attack(user_id, nickname, target, message_text)
            if attack_event:
                result['events'].append(attack_event)

        # Статус войны
        war_status_match = re.search(r'(?:статус войны|война статус)\s+(.+)', text_lower)
        if war_status_match:
            clan_name = war_status_match.group(1).strip()
            clan = await db.get_clan_by_name(clan_name)
            if clan:
                active_wars = await db.get_clan_active_wars(clan['id'])
                if active_wars:
                    result['war_status_request'] = active_wars[0]['id']
                else:
                    result['events'].append({'type': 'war_error', 'error': 'no_active_wars', 'name': clan_name})

        # Достижения
        if text_lower in ('достижения', 'achievements', 'мои достижения'):
            result['achievements_request'] = True

        # Карма
        karma_match = re.search(r'(?:поставить|дать)\s+([+-]?\d+)\s+@?(\w+)', text_lower)
        if karma_match:
            value = int(karma_match.group(1))
            target_nick = karma_match.group(2)
            # Защита - карма может быть только +1 или -1
            if value in (-1, 1):
                karma_event = await GameLogic._handle_karma(user_id, nickname, target_nick, value)
                if karma_event:
                    result['events'].append(karma_event)

        # Банк: вклад
        deposit_match = re.search(r'вклад\s+(\d+)(?:\s+на\s+(\d+)\s+дн)?', text_lower)
        if deposit_match:
            amount = int(deposit_match.group(1))
            days = int(deposit_match.group(2)) if deposit_match.group(2) else 1
            # Защита от отрицательных сумм
            if amount > 0 and days > 0:
                deposit_event = await GameLogic._handle_deposit(user_id, nickname, amount, days)
                if deposit_event:
                    result['events'].append(deposit_event)

        # Кредит
        loan_match = re.search(r'кредит\s+(\d+)(?:\s+под залог\s+([\d.]+)\s+ауры)?', text_lower)
        if loan_match:
            amount = int(loan_match.group(1))
            collateral = float(loan_match.group(2)) if loan_match.group(2) else 0
            # Защита от отрицательных сумм
            if amount > 0:
                loan_event = await GameLogic._handle_loan(user_id, nickname, amount, collateral)
                if loan_event:
                    result['events'].append(loan_event)

        # Шпионаж
        spy_match = re.search(r'шпионаж\s+(.+)', text_lower)
        if spy_match:
            target_clan = spy_match.group(1).strip()
            spy_event = await GameLogic._handle_espionage(user_id, nickname, target_clan)
            if spy_event:
                result['events'].append(spy_event)

        # Турнир
        if text_lower == 'турнир' or text_lower == 'участвовать в турнире':
            tournament_event = await GameLogic._handle_tournament_join(user_id, nickname)
            if tournament_event:
                result['events'].append(tournament_event)

        # Уровень
        if text_lower in ('уровень', 'level', 'мой уровень'):
            result['level_request'] = True

        # События (проверить активное)
        if text_lower in ('события', 'ивент', 'событие'):
            result['events_request'] = True

        # Черный рынок
        if text_lower == 'черный рынок' or text_lower == 'рынок':
            result['black_market_request'] = True

        # Приглашение нового игрока
        invite_match = re.search(r'пригласить\s+@?(\w+)', text_lower)
        if invite_match:
            target_nick = invite_match.group(1).strip()
            invite_event = await GameLogic._handle_invite(user_id, nickname, target_nick, chat_id)
            if invite_event:
                result['events'].append(invite_event)

        # ==================== НОВЫЕ ФУНКЦИИ ====================
        
        # Рулетка
        roulette_match = re.search(r'рулетка\s+(чёт|нечёт)\s+(\d+)', text_lower)
        if roulette_match:
            bet_type = roulette_match.group(1)
            bet_amount = int(roulette_match.group(2))
            # Защита от отрицательных и нулевых ставок
            if bet_amount > 0:
                roulette_event = await GameLogic._handle_roulette(user_id, nickname, bet_type, bet_amount)
                if roulette_event:
                    result['events'].append(roulette_event)

        # Дуэль
        duel_match = re.search(r'дуэль\s+@?(\w+)\s+(\d+)', text_lower)
        if duel_match:
            target_nick = duel_match.group(1)
            bet_amount = int(duel_match.group(2))
            # Защита от отрицательных и нулевых ставок
            if bet_amount > 0:
                duel_event = await GameLogic._handle_duel(user_id, nickname, target_nick, bet_amount)
                if duel_event:
                    result['events'].append(duel_event)

        # Инвестиции
        invest_match = re.search(r'инвестировать\s+(\d+)', text_lower)
        if invest_match:
            amount = int(invest_match.group(1))
            # Защита от отрицательных сумм
            if amount > 0:
                invest_event = await GameLogic._handle_investment(user_id, nickname, amount)
                if invest_event:
                    result['events'].append(invest_event)

        # Лотерея
        if text_lower == 'лотерея' or text_lower == 'купить билет':
            lottery_event = await GameLogic._handle_lottery(user_id, nickname)
            if lottery_event:
                result['events'].append(lottery_event)

        # Фьючерсы
        future_match = re.search(r'фьючерс\s+([\d.]+)\s*ауры?', text_lower)
        if future_match:
            amount = float(future_match.group(1))
            # Защита от отрицательных сумм
            if amount > 0:
                future_event = await GameLogic._handle_future(user_id, nickname, amount)
                if future_event:
                    result['events'].append(future_event)

        # Страховка
        insurance_match = re.search(r'страховка\s+(\d+)', text_lower)
        if insurance_match:
            coverage = int(insurance_match.group(1))
            # Защита от отрицательных сумм
            if coverage > 0:
                insurance_event = await GameLogic._handle_insurance(user_id, nickname, coverage)
                if insurance_event:
                    result['events'].append(insurance_event)

        # Рэкет
        racket_match = re.search(r'рэкет\s+@?(\w+)\s+(\d+)', text_lower)
        if racket_match:
            target_nick = racket_match.group(1)
            amount = int(racket_match.group(2))
            # Защита от отрицательных сумм
            if amount > 0:
                racket_event = await GameLogic._handle_racket(user_id, nickname, target_nick, amount)
                if racket_event:
                    result['events'].append(racket_event)

        # Крышевание
        protect_match = re.search(r'крыша\s+(.+)', text_lower)
        if protect_match:
            target_clan = protect_match.group(1).strip()
            protect_event = await GameLogic._handle_protection(user_id, nickname, target_clan)
            if protect_event:
                result['events'].append(protect_event)

        # Разборки (showdown)
        showdown_match = re.search(r'разборка\s+@?(\w+)', text_lower)
        if showdown_match:
            target_nick = showdown_match.group(1)
            showdown_event = await GameLogic._handle_showdown(user_id, nickname, target_nick)
            if showdown_event:
                result['events'].append(showdown_event)

        # Свадьба кланов
        wedding_match = re.search(r'свадьба\s+(.+)', text_lower)
        if wedding_match:
            target_clan = wedding_match.group(1).strip()
            wedding_event = await GameLogic._handle_clan_wedding(user_id, nickname, target_clan)
            if wedding_event:
                result['events'].append(wedding_event)

        # Атака клан-босса
        if text_lower in ('босс', 'атаковать босс', 'ударить босс'):
            boss_event = await GameLogic._handle_clan_boss_attack(user_id, nickname)
            if boss_event:
                result['events'].append(boss_event)

        # Обмен внутри клана
        exchange_match = re.search(r'обмен\s+(инфluence|аура)\s+([\d.]+)', text_lower)
        if exchange_match:
            from_type = exchange_match.group(1)
            amount = float(exchange_match.group(2))
            # Защита от отрицательных сумм
            if amount > 0:
                exchange_event = await GameLogic._handle_clan_exchange(user_id, nickname, from_type, amount)
                if exchange_event:
                    result['events'].append(exchange_event)

        # День донора - передача влияния
        donor_match = re.search(r'подарить\s+@?(\w+)\s+(\d+)\s+влияния', text_lower)
        if donor_match:
            target_nick = donor_match.group(1)
            amount = int(donor_match.group(2))
            # Защита от отрицательных сумм
            if amount > 0:
                donor_event = await GameLogic._handle_donor_transfer(user_id, nickname, target_nick, amount)
                if donor_event:
                    result['events'].append(donor_event)

        # Установить день рождения
        birthday_match = re.search(r'день рождения\s+(\d{2})-(\d{2})', text_lower)
        if birthday_match:
            month = birthday_match.group(1)
            day = birthday_match.group(2)
            birthday_event = await GameLogic._handle_set_birthday(user_id, nickname, f"2024-{month}-{day}")
            if birthday_event:
                result['events'].append(birthday_event)

        # Поздравить с днём рождения
        congrats_match = re.search(r'поздравляю\s+@?(\w+)', text_lower)
        if congrats_match:
            target_nick = congrats_match.group(1)
            congrats_event = await GameLogic._handle_birthday_wish(user_id, nickname, target_nick)
            if congrats_event:
                result['events'].append(congrats_event)

        # Сезоны
        if text_lower in ('сезон', 'текущий сезон'):
            season_event = await GameLogic._handle_season_info()
            if season_event:
                result['events'].append(season_event)

        # Личные цели
        if text_lower in ('цели', 'мои цели', 'battle pass'):
            goals_event = await GameLogic._handle_goals_info(user_id)
            if goals_event:
                result['events'].append(goals_event)

        # Титулы
        if text_lower in ('титулы', 'мои титулы'):
            titles_event = await GameLogic._handle_titles_info(user_id)
            if titles_event:
                result['events'].append(titles_event)

        # Выбрать титул
        title_select_match = re.search(r'выбрать титул\s+"(.+)"', text_lower)
        if title_select_match:
            title_name = title_select_match.group(1)
            title_event = await GameLogic._handle_title_select(user_id, title_name)
            if title_event:
                result['events'].append(title_event)

        # Авторитет
        if text_lower in ('авторитет', 'мой авторитет'):
            auth_event = await GameLogic._handle_authority_info(user_id)
            if auth_event:
                result['events'].append(auth_event)

        # Клан-ивенты
        if text_lower in ('клановый ивент', ' Clan Event'):
            clan_event = await GameLogic._handle_clan_event_info()
            if clan_event:
                result['events'].append(clan_event)

        # Секретные команды
        secret_trigger = text_lower.strip()
        if secret_trigger in ('баланс', 'кинуть', 'наехать', 'шмот', 'кукл'):
            secret_event = await GameLogic._handle_secret_command(user_id, secret_trigger)
            if secret_event:
                result['events'].append(secret_event)

        # Проверка случайного бонуса
        random_bonus = await db.give_random_bonus(user_id)
        if random_bonus:
            result['events'].append({'type': 'random_bonus', 'amount': random_bonus})

        return result

    @staticmethod
    async def _handle_trust(trusted_id: int, truster_id: int) -> Optional[Dict[str, Any]]:
        """Обработка доверия"""
        if trusted_id == truster_id:
            return None
        if not await db.check_cooldown(truster_id, trusted_id, 'trust', TRUST_COOLDOWN):
            return None

        await db.conn.execute("UPDATE users SET trust = trust + 1 WHERE id = ?", (trusted_id,))
        await db.conn.commit()
        await db.set_cooldown(truster_id, trusted_id, 'trust')
        await db.update_user(truster_id, last_trust_given=datetime.now().isoformat())

        trusted = await db.get_user(trusted_id)
        truster = await db.get_user(truster_id)
        return {
            'type': 'trust',
            'truster_nick': truster['nickname'],
            'trusted_nick': trusted['nickname'],
        }

    @staticmethod
    async def _handle_deal_proposal(sender_id: int, sender_nick: str, match, chat_id: int, message_id: int) -> Optional[Dict]:
        """Обработка предложения сделки"""
        target_username = match.group(1)
        amount = float(match.group(2))
        type_word = match.group(3)
        amount_type = 'influence' if type_word in ('влияние', 'influence') else 'aura'

        sender = await db.get_user(sender_id)
        if sender['trust'] < MIN_TRUST_FOR_DEAL:
            return {'type': 'deal_error', 'error': 'low_trust', 'required': MIN_TRUST_FOR_DEAL}

        # Найдём получателя по username
        async with db.conn.execute("SELECT * FROM users WHERE username = ?", (target_username,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return {'type': 'deal_error', 'error': 'user_not_found', 'username': target_username}
            recipient = dict(row)

        # Проверим достаточно ли ресурсов
        if amount_type == 'influence' and sender['influence'] < amount:
            return {'type': 'deal_error', 'error': 'not_enough_influence'}
        if amount_type == 'aura' and sender['aura'] < amount:
            return {'type': 'deal_error', 'error': 'not_enough_aura'}

        deal = await db.create_deal(sender_id, recipient['id'], amount_type, amount, message_id, chat_id)

        return {
            'type': 'deal_proposal',
            'deal_id': deal['id'],
            'sender_nick': sender_nick,
            'recipient_nick': recipient['nickname'],
            'amount': amount,
            'amount_type': amount_type,
            'expires_at': deal['expires_at']
        }

    @staticmethod
    async def confirm_deal(deal_id: int, confirmer_id: int) -> Optional[Dict]:
        """Подтверждение сделки (вызывается из bot.py)"""
        deal = await db.get_deal(deal_id)
        if not deal or deal['status'] != 'pending' or deal['recipient_id'] != confirmer_id:
            return None

        if datetime.now() > datetime.fromisoformat(deal['expires_at']):
            await db.update_deal_status(deal_id, 'cancelled')
            return {'type': 'deal_expired'}

        sender = await db.get_user(deal['sender_id'])
        recipient = await db.get_user(deal['recipient_id'])

        if deal['amount_type'] == 'influence':
            if sender['influence'] < deal['amount']:
                await db.update_deal_status(deal_id, 'violation')
                await db.conn.execute("UPDATE users SET trust = trust - 1 WHERE id = ?", (deal['sender_id'],))
                await db.conn.commit()
                return {'type': 'deal_violation', 'violator_nick': sender['nickname']}
            # Перевод
            await db.conn.execute("UPDATE users SET influence = influence - ? WHERE id = ?", (deal['amount'], deal['sender_id']))
            await db.conn.execute("UPDATE users SET influence = influence + ? WHERE id = ?", (deal['amount'], deal['recipient_id']))
        else:  # aura
            if sender['aura'] < deal['amount']:
                await db.update_deal_status(deal_id, 'violation')
                await db.conn.execute("UPDATE users SET trust = trust - 1 WHERE id = ?", (deal['sender_id'],))
                await db.conn.commit()
                return {'type': 'deal_violation', 'violator_nick': sender['nickname']}
            await db.conn.execute("UPDATE users SET aura = aura - ? WHERE id = ?", (deal['amount'], deal['sender_id']))
            await db.conn.execute("UPDATE users SET aura = aura + ? WHERE id = ?", (deal['amount'], deal['recipient_id']))

        # Увеличиваем счётчик сделок у обоих
        await db.update_user(deal['sender_id'], deals_count=sender['deals_count'] + 1)
        await db.update_user(deal['recipient_id'], deals_count=recipient['deals_count'] + 1)

        await db.conn.commit()
        await db.update_deal_status(deal_id, 'success')

        # Проверяем достижения после сделки
        await db.check_achievements(deal['sender_id'])
        await db.check_achievements(deal['recipient_id'])

        return {
            'type': 'deal_success',
            'sender_nick': sender['nickname'],
            'recipient_nick': recipient['nickname'],
            'amount': deal['amount'],
            'amount_type': deal['amount_type']
        }

    @staticmethod
    async def _handle_clan_creation(user_id: int, nick: str, clan_name: str) -> Optional[Dict]:
        """Создание клана"""
        user = await db.get_user(user_id)
        if user['influence'] < CLAN_CREATION_COST:
            return {'type': 'clan_error', 'error': 'not_enough_influence', 'required': CLAN_CREATION_COST}
        if user['trust'] < MIN_TRUST_FOR_CLAN:
            return {'type': 'clan_error', 'error': 'not_enough_trust', 'required': MIN_TRUST_FOR_CLAN}

        existing = await db.get_clan_by_name(clan_name)
        if existing:
            return {'type': 'clan_error', 'error': 'name_exists', 'name': clan_name}

        # Вычитаем влияние
        await db.conn.execute("UPDATE users SET influence = influence - ? WHERE id = ?", (CLAN_CREATION_COST, user_id))
        clan = await db.create_clan(clan_name, user_id)
        # Клан получает в казну затраченное влияние
        await db.update_clan(clan['id'], influence_treasury=CLAN_CREATION_COST)
        await db.conn.commit()

        return {'type': 'clan_created', 'clan_name': clan_name, 'leader_nick': nick}

    @staticmethod
    async def _handle_aura_buy(user_id: int, nick: str, amount: float) -> Optional[Dict]:
        """Покупка ауры: рыночная цена (без дюпа)"""
        if amount <= 0 or amount > 10000:
            return {'type': 'aura_error', 'error': 'invalid_amount'}
            
        user = await db.get_user(user_id)
        
        # Получаем текущие показатели системы
        total_influence = await db.get_total_system_influence()
        total_aura = await db.get_total_aura_in_circulation()
        
        # Рыночная цена: (влияние всей системы) / (аура в обороте)
        # После покупки цена вырастет, так как аура в обороте увеличится
        current_price = await db.get_current_aura_price()
        
        # Рассчитываем стоимость по средней цене между текущей и будущей
        # Будущая цена = новое общее влияние / новое общее количество ауры
        new_total_influence = total_influence
        new_total_aura = total_aura + amount
        
        if new_total_aura > 0:
            future_price = new_total_influence / new_total_aura
        else:
            future_price = current_price * 1.1
            
        # Средняя цена для защиты от дюпа
        avg_price = (current_price + future_price) / 2
        cost = int(amount * avg_price)
        
        if user['influence'] < cost:
            return {'type': 'aura_error', 'error': 'not_enough_influence', 'required': cost}

        # Выполняем перевод
        await db.conn.execute(
            "UPDATE users SET influence = influence - ?, aura = aura + ? WHERE id = ?",
            (cost, amount, user_id)
        )
        
        # Устанавливаем новую рыночную цену
        new_price = future_price
        if new_price < MIN_AURA_PRICE:
            new_price = MIN_AURA_PRICE
            
        await db.set_aura_price(new_price)
        await db.conn.commit()

        return {
            'type': 'aura_bought',
            'user_nick': nick,
            'amount': amount,
            'cost': cost,
            'old_price': current_price,
            'new_price': new_price
        }

    @staticmethod
    async def _handle_aura_sell(user_id: int, nick: str, amount: float) -> Optional[Dict]:
        """Продажа ауры: рыночная цена (без дюпа)"""
        if amount <= 0 or amount > 10000:
            return {'type': 'aura_error', 'error': 'invalid_amount'}
            
        user = await db.get_user(user_id)
        
        if user['aura'] < amount:
            return {'type': 'aura_error', 'error': 'not_enough_aura', 'required': amount}
        
        # Получаем текущие показатели системы
        total_influence = await db.get_total_system_influence()
        total_aura = await db.get_total_aura_in_circulation()
        
        current_price = await db.get_current_aura_price()
        
        # Рассчитываем будущую цену после продажи
        new_total_aura = total_aura - amount
        
        if new_total_aura > 0:
            future_price = total_influence / new_total_aura
        else:
            future_price = current_price * 0.9
            
        # Средняя цена для защиты от дюпа
        avg_price = (current_price + future_price) / 2
        gain = int(amount * avg_price)
        
        await db.conn.execute(
            "UPDATE users SET aura = aura - ?, influence = influence + ? WHERE id = ?",
            (amount, gain, user_id)
        )
        
        # Устанавливаем новую рыночную цену
        new_price = future_price
        if new_price < MIN_AURA_PRICE:
            new_price = MIN_AURA_PRICE
            
        await db.set_aura_price(new_price)
        await db.conn.commit()

        return {
            'type': 'aura_sold',
            'user_nick': nick,
            'amount': amount,
            'gain': gain,
            'old_price': current_price,
            'new_price': new_price
        }

    @staticmethod
    async def _handle_alliance_creation(user_id: int, nick: str, target_clan_name: str, message_text: str) -> Optional[Dict]:
        """Создание альянса"""
        user = await db.get_user(user_id)
        if not user['clan_id']:
            return {'type': 'alliance_error', 'error': 'not_in_clan'}

        user_clan = await db.get_clan(user['clan_id'])
        if not user_clan:
            return {'type': 'alliance_error', 'error': 'clan_not_found'}

        if user_clan['leader_id'] != user_id:
            return {'type': 'alliance_error', 'error': 'not_leader'}

        target_clan = await db.get_clan_by_name(target_clan_name)
        if not target_clan:
            return {'type': 'alliance_error', 'error': 'target_clan_not_found', 'name': target_clan_name}

        if target_clan['id'] == user_clan['id']:
            return {'type': 'alliance_error', 'error': 'self_alliance'}

        existing_alliances = await db.get_clan_alliances(user_clan['id'])
        if target_clan['id'] in existing_alliances:
            return {'type': 'alliance_error', 'error': 'already_allied', 'name': target_clan['name']}

        await db.create_alliance(user_clan['id'], target_clan['id'])
        # Увеличиваем счётчик альянсов у лидера (для достижений)
        await db.update_user(user_id, alliances_count=user['alliances_count'] + 1)

        return {
            'type': 'alliance_created',
            'clan1_name': user_clan['name'],
            'clan2_name': target_clan['name'],
            'leader_nick': nick
        }

    @staticmethod
    async def _handle_war_declaration(user_id: int, nick: str, target_clan_name: str) -> Optional[Dict]:
        """Объявление войны"""
        user = await db.get_user(user_id)
        if not user['clan_id']:
            return {'type': 'war_error', 'error': 'not_in_clan'}

        attacker_clan = await db.get_clan(user['clan_id'])
        if not attacker_clan:
            return {'type': 'war_error', 'error': 'clan_not_found'}

        # Проверяем, является ли пользователь лидером или офицером
        is_leader = attacker_clan['leader_id'] == user_id
        is_officer = await db.has_clan_role(user_id, attacker_clan['id'], 'officer')
        if not (is_leader or is_officer):
            return {'type': 'war_error', 'error': 'not_leader_or_officer'}

        defender_clan = await db.get_clan_by_name(target_clan_name)
        if not defender_clan:
            return {'type': 'war_error', 'error': 'target_clan_not_found', 'name': target_clan_name}

        if defender_clan['id'] == attacker_clan['id']:
            return {'type': 'war_error', 'error': 'self_war'}

        existing_war = await db.get_active_war_between(attacker_clan['id'], defender_clan['id'])
        if existing_war:
            return {'type': 'war_error', 'error': 'already_at_war', 'name': defender_clan['name']}

        # Проверка альянса
        alliances = await db.get_clan_alliances(attacker_clan['id'])
        if defender_clan['id'] in alliances:
            return {'type': 'war_error', 'error': 'cannot_war_alliance', 'name': defender_clan['name']}

        if attacker_clan['influence_treasury'] < WAR_DECLARE_COST:
            return {'type': 'war_error', 'error': 'not_enough_treasury', 'required': WAR_DECLARE_COST}

        # Списываем стоимость
        await db.update_clan(attacker_clan['id'], influence_treasury=attacker_clan['influence_treasury'] - WAR_DECLARE_COST)
        war = await db.declare_war(attacker_clan['id'], defender_clan['id'])

        return {
            'type': 'war_declared',
            'attacker_clan': attacker_clan['name'],
            'defender_clan': defender_clan['name'],
            'war_id': war['id'],
            'ends_at': war['ends_at']
        }

    @staticmethod
    async def _handle_war_attack(user_id: int, nick: str, target_nick: str, message_text: str) -> Optional[Dict]:
        """Атака в войне"""
        user = await db.get_user(user_id)
        if not user['clan_id']:
            return {'type': 'war_attack_error', 'error': 'not_in_clan'}

        # Находим цель по нику или username
        async with db.conn.execute(
            "SELECT * FROM users WHERE username = ? OR nickname = ?",
            (target_nick, target_nick)
        ) as cursor:
            target_row = await cursor.fetchone()
            if not target_row:
                return {'type': 'war_attack_error', 'error': 'user_not_found', 'name': target_nick}
            target = dict(target_row)

        if not target['clan_id']:
            return {'type': 'war_attack_error', 'error': 'target_not_in_clan'}

        if target['id'] == user_id:
            return {'type': 'war_attack_error', 'error': 'self_attack'}

        war = await db.get_active_war_between(user['clan_id'], target['clan_id'])
        if not war:
            return {'type': 'war_attack_error', 'error': 'not_at_war'}

        if not await db.check_cooldown(user_id, target['id'], 'war_attack', WAR_ATTACK_COOLDOWN):
            return {'type': 'war_attack_error', 'error': 'cooldown'}

        # Рассчитываем урон: база + бонус от военного улучшения клана
        military_level = await db.get_clan_upgrade_level(user['clan_id'], 'military')
        damage_bonus = 1.0 + military_level * 0.1  # +10% за уровень
        damage = int((user['influence'] * 0.1 + user['trust'] * 2) * damage_bonus)
        if damage < 1:
            damage = 1

        await db.record_war_attack(war['id'], user_id, target['id'], damage)
        await db.set_cooldown(user_id, target['id'], 'war_attack')

        # Атакующий тратит влияние
        cost = max(1, int(user['influence'] * 0.05))
        await db.add_influence(user_id, -cost)

        return {
            'type': 'war_attack',
            'attacker_nick': nick,
            'defender_nick': target['nickname'],
            'damage': damage,
            'cost': cost,
            'war_id': war['id']
        }

    @staticmethod
    async def _handle_karma(from_user: int, from_nick: str, target_nick: str, value: int) -> Optional[Dict]:
        """Поставить карму (+1 или -1)"""
        if value not in (1, -1):
            return {'type': 'karma_error', 'error': 'invalid_value'}

        async with db.conn.execute(
            "SELECT * FROM users WHERE username = ? OR nickname = ?",
            (target_nick, target_nick)
        ) as cursor:
            target_row = await cursor.fetchone()
            if not target_row:
                return {'type': 'karma_error', 'error': 'user_not_found', 'name': target_nick}
            target = dict(target_row)

        if target['id'] == from_user:
            return {'type': 'karma_error', 'error': 'self_karma'}

        success = await db.give_karma(from_user, target['id'], value)
        if not success:
            return {'type': 'karma_error', 'error': 'already_given_today'}

        new_karma = await db.get_karma(target['id'])
        return {
            'type': 'karma',
            'from_nick': from_nick,
            'to_nick': target['nickname'],
            'value': value,
            'new_karma': new_karma
        }

    @staticmethod
    async def _handle_deposit(user_id: int, nick: str, amount: int, days: int) -> Optional[Dict]:
        """Создать вклад"""
        if amount <= 0:
            return {'type': 'deposit_error', 'error': 'invalid_amount'}
        if days < 1:
            days = 1

        user = await db.get_user(user_id)
        if user['influence'] < amount:
            return {'type': 'deposit_error', 'error': 'not_enough_influence'}

        success = await db.create_deposit(user_id, amount, days)
        if not success:
            return {'type': 'deposit_error', 'error': 'creation_failed'}

        return {
            'type': 'deposit_created',
            'user_nick': nick,
            'amount': amount,
            'days': days,
            'interest': DEPOSIT_INTEREST * 100
        }

    @staticmethod
    async def _handle_loan(user_id: int, nick: str, amount: int, collateral: float) -> Optional[Dict]:
        """Взять кредит"""
        if amount <= 0:
            return {'type': 'loan_error', 'error': 'invalid_amount'}

        user = await db.get_user(user_id)
        if collateral > user['aura']:
            return {'type': 'loan_error', 'error': 'not_enough_collateral'}

        # Проверяем, есть ли активные кредиты (не больше 1)
        async with db.conn.execute(
            "SELECT 1 FROM loans WHERE user_id = ? AND status = 'active'",
            (user_id,)
        ) as cursor:
            if await cursor.fetchone():
                return {'type': 'loan_error', 'error': 'already_has_loan'}

        success = await db.create_loan(user_id, amount, collateral, 7)  # срок 7 дней
        if not success:
            return {'type': 'loan_error', 'error': 'creation_failed'}

        return {
            'type': 'loan_granted',
            'user_nick': nick,
            'amount': amount,
            'collateral': collateral,
            'due_days': 7,
            'interest': LOAN_INTEREST * 100
        }

    @staticmethod
    async def _handle_espionage(user_id: int, nick: str, target_clan_name: str) -> Optional[Dict]:
        """Отправить шпиона"""
        user = await db.get_user(user_id)
        if not user['clan_id']:
            return {'type': 'espionage_error', 'error': 'not_in_clan'}

        from_clan = await db.get_clan(user['clan_id'])
        if not from_clan:
            return {'type': 'espionage_error', 'error': 'clan_not_found'}

        # Только лидер или офицер
        is_leader = from_clan['leader_id'] == user_id
        is_officer = await db.has_clan_role(user_id, from_clan['id'], 'officer')
        if not (is_leader or is_officer):
            return {'type': 'espionage_error', 'error': 'not_leader_or_officer'}

        to_clan = await db.get_clan_by_name(target_clan_name)
        if not to_clan:
            return {'type': 'espionage_error', 'error': 'target_clan_not_found', 'name': target_clan_name}

        if to_clan['id'] == from_clan['id']:
            return {'type': 'espionage_error', 'error': 'self_espionage'}

        # Проверяем, не в альянсе
        alliances = await db.get_clan_alliances(from_clan['id'])
        if to_clan['id'] in alliances:
            return {'type': 'espionage_error', 'error': 'cannot_spy_alliance'}

        cost = 150  # фиксированная стоимость шпионажа
        if from_clan['influence_treasury'] < cost:
            return {'type': 'espionage_error', 'error': 'not_enough_treasury', 'required': cost}

        success = await db.start_espionage(from_clan['id'], to_clan['id'], cost)
        if not success:
            return {'type': 'espionage_error', 'error': 'creation_failed'}

        return {
            'type': 'espionage_started',
            'from_clan': from_clan['name'],
            'to_clan': to_clan['name'],
            'cost': cost
        }

    @staticmethod
    async def _handle_tournament_join(user_id: int, nick: str) -> Optional[Dict]:
        """Присоединиться к активному турниру"""
        # Ищем активный турнир
        async with db.conn.execute(
            "SELECT * FROM tournaments WHERE status = 'active' AND start_time <= datetime('now') AND end_time >= datetime('now')"
        ) as cursor:
            tournament = await cursor.fetchone()
        if not tournament:
            return {'type': 'tournament_error', 'error': 'no_active_tournament'}

        await db.join_tournament(user_id, tournament['id'])
        return {
            'type': 'tournament_joined',
            'tournament_name': tournament['name'],
            'user_nick': nick
        }

    @staticmethod
    async def _handle_invite(user_id: int, inviter_nick: str, target_nick: str, chat_id: int) -> Optional[Dict]:
        """Приглашение нового игрока (новичка)"""
        # Ищем цель по нику
        async with db.conn.execute(
            "SELECT * FROM users WHERE username = ? OR nickname = ?",
            (target_nick, target_nick)
        ) as cursor:
            target_row = await cursor.fetchone()
            if not target_row:
                return {'type': 'invite_error', 'error': 'user_not_found', 'name': target_nick}
            target = dict(target_row)

        if target['id'] == user_id:
            return {'type': 'invite_error', 'error': 'self_invite'}

        # Проверяем, не приглашал ли уже
        existing = await db.get_invite_info(target['id'])
        if existing:
            return {'type': 'invite_error', 'error': 'already_invited'}

        # Проверяем, является ли цель "новичком" (influence < 50)
        if target['influence'] >= 50:
            return {'type': 'invite_error', 'error': 'not_newbie'}

        await db.create_invite(user_id, target['id'])
        await db.update_user(target['id'], invited_by=user_id, invite_time=datetime.now().isoformat())

        # Отправляем событие с текущим прогрессом (0 сообщений)
        invite_info = await db.get_invite_info(target['id'])
        return {
            'type': 'newbie_invite',
            'inviter_nick': inviter_nick,
            'newbie_nick': target['nickname'],
            'newbie_messages': invite_info['messages_count'] if invite_info else 0
        }

    # ==================== Новые функции: события ====================
    
    @staticmethod
    async def get_events_text() -> str:
        """Получить информацию об активных событиях"""
        active_event = await db.get_active_event()
        if not active_event:
            return (
                "🎉 <b>Событий сейчас нет</b>\n"
                "━━━━━━━━━━━━━━━\n"
                "Следите за новостями — события происходят случайно!"
            )
        
        event_type = active_event['event_type']
        multiplier = active_event['multiplier']
        ends_at = datetime.fromisoformat(active_event['ends_at'])
        time_left = ends_at - datetime.now()
        hours = int(time_left.total_seconds() / 3600)
        minutes = int((time_left.total_seconds() % 3600) / 60)
        
        descriptions = {
            'bonus_hour': f"🎁 <b>БОНУСНЫЙ ЧАС!</b>\nВлияние за сообщения x{multiplier:.0f}!",
            'black_market': f"🛒 <b>ЧЁРНЫЙ РЫНОК!</b>\nАура со скидкой {int((1-multiplier)*100)}%!",
            'inflation': f"📈 <b>ИНФЛЯЦИЯ!</b>\nЦена ауры x{multiplier:.0f}!",
            'deflation': f"📉 <b>ДЕФЛЯЦИЯ!</b>\nЦена ауры x{multiplier:.0f}!",
            'double_trust': f"💚 <b>ДЕНЬ ДОВЕРИЯ!</b>\nДоверие x{multiplier:.0f}!",
        }
        
        desc = descriptions.get(event_type, f"🎯 <b>{event_type}</b>\nМножитель: x{multiplier:.0f}")
        
        return (
            f"{desc}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⏳ Осталось: {hours}ч {minutes}м"
        )
    
    # ==================== НОВЫЕ ОБРАБОТЧИКИ ====================

    @staticmethod
    async def _handle_roulette(user_id: int, nick: str, bet_type: str, bet_amount: int) -> Optional[Dict]:
        """Рулетка - чёт/нечёт"""
        if bet_amount < ROULETTE_MIN_BET:
            return {'type': 'roulette_error', 'error': 'min_bet', 'min': ROULETTE_MIN_BET}
        
        result = await db.play_roulette(user_id, bet_type, bet_amount)
        
        if not result.get('success'):
            return {'type': 'roulette_error', 'error': result.get('error', 'unknown')}
        
        return {
            'type': 'roulette_result',
            'number': result['number'],
            'bet_type': bet_type,
            'bet_amount': bet_amount,
            'result': result['result'],
            'win_amount': result['win_amount']
        }

    @staticmethod
    async def _handle_duel(user_id: int, nick: str, target_nick: str, bet_amount: int) -> Optional[Dict]:
        """Дуэль между игроками"""
        if bet_amount < DUEL_MIN_STAKE:
            return {'type': 'duel_error', 'error': 'min_stake', 'min': DUEL_MIN_STAKE}
        
        # Ищем цель
        async with db.conn.execute(
            "SELECT * FROM users WHERE username = ? OR nickname = ?", (target_nick, target_nick)
        ) as cursor:
            target = await cursor.fetchone()
        
        if not target:
            return {'type': 'duel_error', 'error': 'user_not_found'}
        
        target = dict(target)
        if target['id'] == user_id:
            return {'type': 'duel_error', 'error': 'self_duel'}
        
        user = await db.get_user(user_id)
        if user['influence'] < bet_amount:
            return {'type': 'duel_error', 'error': 'not_enough_influence'}
        if target['influence'] < bet_amount:
            return {'type': 'duel_error', 'error': 'target_not_enough'}
        
        # Списываем ставки
        await db.conn.execute(
            "UPDATE users SET influence = influence - ? WHERE id = ?", (bet_amount, user_id)
        )
        await db.conn.execute(
            "UPDATE users SET influence = influence - ? WHERE id = ?", (bet_amount, target['id'])
        )
        await db.conn.commit()
        
        duel = await db.create_duel(user_id, target['id'], bet_amount)
        
        return {
            'type': 'duel_started',
            'challenger_nick': nick,
            'target_nick': target['nickname'],
            'bet_amount': bet_amount,
            'duration': DUEL_DURATION
        }

    @staticmethod
    async def _handle_investment(user_id: int, nick: str, amount: int) -> Optional[Dict]:
        """Инвестиции"""
        if amount < INVESTMENT_MIN:
            return {'type': 'investment_error', 'error': 'min_amount', 'min': INVESTMENT_MIN}
        
        investment = await db.create_investment(user_id, amount, INVESTMENT_DURATION)
        if not investment:
            return {'type': 'investment_error', 'error': 'not_enough_influence'}
        
        return {
            'type': 'investment_started',
            'user_nick': nick,
            'amount': amount,
            'duration': INVESTMENT_DURATION,
            'potential_return': int(amount * 0.5),
            'potential_loss': int(amount * 0.5)
        }

    @staticmethod
    async def _handle_lottery(user_id: int, nick: str) -> Optional[Dict]:
        """Лотерея - купить билет"""
        # Получаем текущий раунд
        current_hour = datetime.now().hour
        round_number = datetime.now().year * 10000 + datetime.now().month * 100 + current_hour
        
        lottery_round = await db.get_lottery_round(round_number)
        if not lottery_round:
            lottery_round = await db.create_lottery_round(round_number)
        
        success = await db.buy_lottery_ticket(user_id, round_number)
        if not success:
            return {'type': 'lottery_error', 'error': 'not_enough_influence'}
        
        return {
            'type': 'lottery_ticket_bought',
            'user_nick': nick,
            'round': round_number,
            'cost': LOTTERY_TICKET_COST
        }

    @staticmethod
    async def _handle_future(user_id: int, nick: str, amount: float) -> Optional[Dict]:
        """Фьючерс на ауру"""
        if amount < FUTURES_MIN_AMOUNT:
            return {'type': 'future_error', 'error': 'min_amount', 'min': FUTURES_MIN_AMOUNT}
        
        future = await db.create_future(user_id, amount, FUTURES_DURATION)
        if not future:
            current_price = await db.get_current_aura_price()
            cost = int(amount * current_price * FUTURES_MARGIN)
            return {'type': 'future_error', 'error': 'not_enough_influence', 'required': cost}
        
        current_price = await db.get_current_aura_price()
        
        return {
            'type': 'future_created',
            'user_nick': nick,
            'amount': amount,
            'strike_price': future['strike_price'],
            'current_price': current_price,
            'duration': FUTURES_DURATION
        }

    @staticmethod
    async def _handle_insurance(user_id: int, nick: str, coverage: int) -> Optional[Dict]:
        """Страховка от потери влияния"""
        premium = int(coverage * INSURANCE_PREMIUM_RATE)
        
        insurance = await db.buy_insurance(user_id, coverage, premium)
        if not insurance:
            return {'type': 'insurance_error', 'error': 'not_enough_influence'}
        
        return {
            'type': 'insurance_bought',
            'user_nick': nick,
            'coverage': coverage,
            'premium': premium
        }

    @staticmethod
    async def _handle_racket(user_id: int, nick: str, target_nick: str, amount: int) -> Optional[Dict]:
        """Рэкет - наехать на игрока"""
        async with db.conn.execute(
            "SELECT * FROM users WHERE username = ? OR nickname = ?", (target_nick, target_nick)
        ) as cursor:
            target = await cursor.fetchone()
        
        if not target:
            return {'type': 'racket_error', 'error': 'user_not_found'}
        
        target = dict(target)
        if target['id'] == user_id:
            return {'type': 'racket_error', 'error': 'self_racket'}
        
        user = await db.get_user(user_id)
        if user['influence'] < amount * 0.2:
            return {'type': 'racket_error', 'error': 'not_enough_influence'}
        
        result = await db.create_racket(user_id, target['id'], amount)
        
        if result['success']:
            return {
                'type': 'racket_success',
                'racketeer_nick': nick,
                'target_nick': target['nickname'],
                'amount': amount
            }
        else:
            penalty = int(amount * 0.2)
            return {
                'type': 'racket_failed',
                'racketeer_nick': nick,
                'target_nick': target['nickname'],
                'penalty': penalty
            }

    @staticmethod
    async def _handle_protection(user_id: int, nick: str, target_clan_name: str) -> Optional[Dict]:
        """Крышевание - защита слабого клана"""
        user = await db.get_user(user_id)
        if not user['clan_id']:
            return {'type': 'protection_error', 'error': 'not_in_clan'}
        
        user_clan = await db.get_clan(user['clan_id'])
        
        target_clan = await db.get_clan_by_name(target_clan_name)
        if not target_clan:
            return {'type': 'protection_error', 'error': 'clan_not_found'}
        
        if target_clan['id'] == user_clan['id']:
            return {'type': 'protection_error', 'error': 'self_protection'}
        
        # Создаём защиту
        protection = await db.create_protection(target_clan['id'], user_clan['id'], PROTECTION_COST_PER_DAY)
        
        return {
            'type': 'protection_started',
            'protector_clan': user_clan['name'],
            'protected_clan': target_clan['name'],
            'cost_per_day': PROTECTION_COST_PER_DAY
        }

    @staticmethod
    async def _handle_showdown(user_id: int, nick: str, target_nick: str) -> Optional[Dict]:
        """Разборки - вызов на стрелку"""
        async with db.conn.execute(
            "SELECT * FROM users WHERE username = ? OR nickname = ?", (target_nick, target_nick)
        ) as cursor:
            target = await cursor.fetchone()
        
        if not target:
            return {'type': 'showdown_error', 'error': 'user_not_found'}
        
        target = dict(target)
        if target['id'] == user_id:
            return {'type': 'showdown_error', 'error': 'self_showdown'}
        
        user = await db.get_user(user_id)
        if user['influence'] < SHOWDOWN_STAKE:
            return {'type': 'showdown_error', 'error': 'not_enough_influence'}
        if target['influence'] < SHOWDOWN_STAKE:
            return {'type': 'showdown_error', 'error': 'target_not_enough'}
        
        # Списываем ставки
        await db.conn.execute(
            "UPDATE users SET influence = influence - ? WHERE id = ?", (SHOWDOWN_STAKE, user_id)
        )
        await db.conn.execute(
            "UPDATE users SET influence = influence - ? WHERE id = ?", (SHOWDOWN_STAKE, target['id'])
        )
        await db.conn.commit()
        
        showdown = await db.create_showdown(user_id, target['id'], SHOWDOWN_STAKE)
        
        return {
            'type': 'showdown_started',
            'challenger_nick': nick,
            'challenged_nick': target['nickname'],
            'stake': SHOWDOWN_STAKE,
            'duration': SHOWDOWN_DURATION
        }

    @staticmethod
    async def _handle_clan_wedding(user_id: int, nick: str, target_clan_name: str) -> Optional[Dict]:
        """Свадьба кланов"""
        user = await db.get_user(user_id)
        if not user['clan_id']:
            return {'type': 'wedding_error', 'error': 'not_in_clan'}
        
        user_clan = await db.get_clan(user['clan_id'])
        if user_clan['leader_id'] != user_id:
            return {'type': 'wedding_error', 'error': 'not_leader'}
        
        target_clan = await db.get_clan_by_name(target_clan_name)
        if not target_clan:
            return {'type': 'wedding_error', 'error': 'clan_not_found'}
        
        if target_clan['id'] == user_clan['id']:
            return {'type': 'wedding_error', 'error': 'self_wedding'}
        
        if user_clan['influence_treasury'] < CLAN_WEDDING_COST:
            return {'type': 'wedding_error', 'error': 'not_enough_treasury'}
        
        # Списываем стоимость
        await db.update_clan(user_clan['id'], influence_treasury=user_clan['influence_treasury'] - CLAN_WEDDING_COST)
        
        wedding = await db.propose_clan_wedding(user_clan['id'], target_clan['id'], user_id, CLAN_WEDDING_COST)
        
        return {
            'type': 'wedding_proposed',
            'clan1': user_clan['name'],
            'clan2': target_clan['name'],
            'cost': CLAN_WEDDING_COST
        }

    @staticmethod
    async def _handle_clan_boss_attack(user_id: int, nick: str) -> Optional[Dict]:
        """Атака клан-босса"""
        boss = await db.get_active_boss()
        if not boss:
            return {'type': 'boss_error', 'error': 'no_active_boss'}
        
        user = await db.get_user(user_id)
        damage = max(1, int(user['influence'] * 0.05))
        
        await db.hit_clan_boss(boss['id'], user_id, damage)
        
        return {
            'type': 'boss_attack',
            'boss_name': boss['boss_name'],
            'damage': damage,
            'boss_health': boss['current_health'] - damage
        }

    @staticmethod
    async def _handle_clan_exchange(user_id: int, nick: str, from_type: str, amount: float) -> Optional[Dict]:
        """Обмен внутри клана"""
        user = await db.get_user(user_id)
        if not user['clan_id']:
            return {'type': 'exchange_error', 'error': 'not_in_clan'}
        
        result = await db.clan_exchange(user_id, user['clan_id'], from_type, amount)
        if not result:
            return {'type': 'exchange_error', 'error': 'not_enough_resources'}
        
        return {
            'type': 'exchange_success',
            'from_type': result['from_type'],
            'to_type': result['to_type'],
            'from_amount': result['from_amount'],
            'to_amount': result['to_amount']
        }

    @staticmethod
    async def _handle_donor_transfer(user_id: int, nick: str, target_nick: str, amount: int) -> Optional[Dict]:
        """День донора - передача влияния без сделки"""
        async with db.conn.execute(
            "SELECT * FROM users WHERE username = ? OR nickname = ?", (target_nick, target_nick)
        ) as cursor:
            target = await cursor.fetchone()
        
        if not target:
            return {'type': 'donor_error', 'error': 'user_not_found'}
        
        target = dict(target)
        
        success = await db.donor_transfer(user_id, target['id'], amount)
        if not success:
            return {'type': 'donor_error', 'error': 'cooldown_or_not_enough'}
        
        return {
            'type': 'donor_success',
            'from_nick': nick,
            'to_nick': target['nickname'],
            'amount': amount
        }

    @staticmethod
    async def _handle_set_birthday(user_id: int, nick: str, birthday: str) -> Optional[Dict]:
        """Установить день рождения"""
        await db.set_birthday(user_id, birthday)
        
        return {
            'type': 'birthday_set',
            'user_nick': nick,
            'birthday': birthday
        }

    @staticmethod
    async def _handle_birthday_wish(user_id: int, nick: str, target_nick: str) -> Optional[Dict]:
        """Поздравить с днём рождения"""
        async with db.conn.execute(
            "SELECT * FROM users WHERE username = ? OR nickname = ?", (target_nick, target_nick)
        ) as cursor:
            target = await cursor.fetchone()
        
        if not target:
            return {'type': 'birthday_error', 'error': 'user_not_found'}
        
        target = dict(target)
        
        # Проверяем, день ли сегодня
        birthday_users = await db.get_birthday_users()
        birthday_ids = [u['user_id'] for u in birthday_users]
        
        if target['id'] not in birthday_ids:
            return {'type': 'birthday_error', 'error': 'not_birthday'}
        
        await db.birthday_wish(target['id'], user_id)
        
        return {
            'type': 'birthday_wish',
            'wisher_nick': nick,
            'birthday_user_nick': target['nickname']
        }

    @staticmethod
    async def _handle_season_info() -> Optional[Dict]:
        """Информация о сезоне"""
        season = await db.get_active_season()
        if not season:
            return {'type': 'season_info', 'text': 'Сезонов сейчас нет активных'}
        
        end_time = datetime.fromisoformat(season['end_time'])
        time_left = end_time - datetime.now()
        days = int(time_left.total_seconds() / 86400)
        
        return {
            'type': 'season_info',
            'season_name': season['name'],
            'days_left': days,
            'reset_type': season['reset_type']
        }

    @staticmethod
    async def _handle_goals_info(user_id: int) -> Optional[Dict]:
        """Информация о личных целях"""
        goals = await db.get_user_goals(user_id)
        if not goals:
            return {'type': 'goals_info', 'text': 'У вас нет активных целей. Они появятся в новом сезоне!'}
        
        goals_text = "🎯 Ваши цели:\n"
        for g in goals[:5]:
            progress = f"{g['current_value']}/{g['target_value']}"
            goals_text += f"- {g['goal_type']}: {progress}\n"
        
        return {'type': 'goals_info', 'text': goals_text}

    @staticmethod
    async def _handle_titles_info(user_id: int) -> Optional[Dict]:
        """Информация о титулах"""
        titles = await db.get_user_titles(user_id)
        if not titles:
            return {'type': 'titles_info', 'text': 'У вас пока нет титулов. Достигните целей!'}
        
        titles_text = "🏆 Ваши титулы:\n"
        for t in titles:
            active_mark = "✅" if t['active'] else ""
            titles_text += f"{active_mark} {t['name']} - {t['description']}\n"
        
        return {'type': 'titles_info', 'text': titles_text}

    @staticmethod
    async def _handle_title_select(user_id: int, title_name: str) -> Optional[Dict]:
        """Выбрать активный титул"""
        titles = await db.get_user_titles(user_id)
        
        for t in titles:
            if t['name'].lower() == title_name.lower():
                await db.set_active_title(user_id, t['id'])
                return {'type': 'title_selected', 'title': t['name']}
        
        return {'type': 'title_error', 'error': 'no_such_title'}

    @staticmethod
    async def _handle_authority_info(user_id: int) -> Optional[Dict]:
        """Информация об авторитете"""
        auth = await db.get_authority(user_id)
        
        ranks = {
            'none': 'Без авторитета',
            'soldier': 'Солдат',
            'capo': 'Капо',
            'boss': 'Босс',
            'legenda': 'Легенда'
        }
        
        return {
            'type': 'authority_info',
            'points': auth['points'],
            'rank': ranks.get(auth['rank'], 'Неизвестно')
        }

    @staticmethod
    async def _handle_clan_event_info() -> Optional[Dict]:
        """Информация о клановом ивенте"""
        event = await db.get_active_clan_event()
        if not event:
            return {'type': 'clan_event_info', 'text': 'Сейчас нет активных клановых ивентов'}
        
        end_time = datetime.fromisoformat(event['end_time'])
        time_left = end_time - datetime.now()
        hours = int(time_left.total_seconds() / 3600)
        
        return {
            'type': 'clan_event_info',
            'event_type': event['event_type'],
            'hours_left': hours
        }

    @staticmethod
    async def _handle_secret_command(user_id: int, trigger: str) -> Optional[Dict]:
        """Секретные команды"""
        secret = await db.get_secret_response(trigger)
        if not secret:
            return None
        
        user = await db.get_user(user_id)
        
        response = secret['response']
        response = response.replace('{influence}', str(user['influence']))
        response = response.replace('{aura}', f"{user['aura']:.1f}")
        response = response.replace('{name}', 'Решала')
        response = response.replace('{coin}', random.choice(['Орёл', 'Решка']))
        response = response.replace('{fine}', str(random.randint(10, 100)))
        response = response.replace('{outfits}', str(random.randint(0, 20)))
        
        return {
            'type': 'secret_command',
            'trigger': trigger,
            'response': response,
            'rarity': secret['rarity']
        }

    @staticmethod
    async def get_black_market_text() -> str:
        """Получить информацию о черном рынке"""
        active_event = await db.get_active_event()
        
        if active_event and active_event['event_type'] == 'black_market':
            # Черный рынок активен - показываем скидку
            discount = int((1 - active_event['multiplier']) * 100)
            current_price = await db.get_current_aura_price()
            discounted_price = current_price * active_event['multiplier']
            return (
                f"🛒 <b>ЧЁРНЫЙ РЫНОК</b> 🛒\n"
                f"━━━━━━━━━━━━━━━\n"
                f"⚡ Скидка: {discount}%!\n"
                f"💰 Обычная цена ауры: {current_price:.2f}\n"
                f"💎 Цена на черном рынке: {discounted_price:.2f}\n\n"
                f"📝 Купить: \"купить аура 10\"\n"
                f"📝 Продать: \"продать аура 10\""
            )
        else:
            # Черный рынок не активен
            current_price = await db.get_current_aura_price()
            return (
                f"🛒 <b>ЧЁРНЫЙ РЫНОК</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"Сейчас закрыт 🔒\n\n"
                f"💎 Текущая цена ауры: {current_price:.2f}\n\n"
                f"🔔 Следите за событиями — черный рынок "
                f"открывается случайно!"
            )
    
    @staticmethod
    async def trigger_random_event() -> Optional[Dict]:
        """Запустить случайное событие (вызывается периодически)"""
        import random
        
        # Шансы на событие (10% каждый час)
        if random.random() > 0.1:
            return None
        
        events = [
            ('bonus_hour', 'Бонусный час', 2.0, 1),  # 2x влияние, 1 час
            ('black_market', 'Чёрный рынок', 0.5, 3),  # 50% скидка, 3 часа
            ('inflation', 'Инфляция', 1.5, 10),  # 1.5x цена, 10 минут
            ('deflation', 'Дефляция', 0.8, 10),  # 0.8x цена, 10 минут
            ('double_trust', 'День доверия', 2.0, 1),  # 2x доверие, 1 час
        ]
        
        event = random.choice(events)
        await db.create_global_event(event[0], event[1], event[2], event[3])
        
        return {
            'type': 'random_event',
            'event_type': event[0],
            'description': event[1],
            'duration_hours': event[3]
        }
    
    @staticmethod
    async def get_level_text(user_id: int) -> str:
        user = await db.get_user(user_id)
        if not user:
            return "❌ Пользователь не найден."

        clan = await db.get_clan(user['clan_id']) if user['clan_id'] else None
        price = await db.get_current_aura_price()
        karma = await db.get_karma(user_id)
        achievements_count = await db.conn.execute(
            "SELECT COUNT(*) FROM user_achievements WHERE user_id = ?", (user_id,)
        )
        ach_count = (await achievements_count.fetchone())[0]

        profile = (
            f"👤 <b>{user['nickname']}</b> (уровень {user['level']})\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 Влияние: {user['influence']}\n"
            f"🤝 Доверие: {user['trust']}\n"
            f"✨ Аура: {user['aura']:.2f}\n"
            f"💰 Цена ауры: {price:.2f}\n"
            f"📝 Сообщений: {user['messages_count']}\n"
            f"🔄 Сделок: {user['deals_count']}\n"
            f"🏆 Достижений: {ach_count}\n"
            f"⭐ Карма: {karma}\n"
        )
        if clan:
            profile += f"🏰 Клан: {clan['name']}\n"
            profile += f"   📊 Казна: {clan['influence_treasury']} влияния, {clan['aura_treasury']:.2f} ауры"
        return profile

    @staticmethod
    async def get_top_text() -> str:
        top_inf = await db.get_top_influence(5)
        top_tr = await db.get_top_trust(5)
        top_au = await db.get_top_aura(5)
        top_cl = await db.get_top_clans(5)

        text = "🏆 <b>ТОП ИГРОКОВ</b> 🏆\n━━━━━━━━━━━━━━━\n\n"
        text += "📊 <b>Влияние:</b>\n"
        for i, u in enumerate(top_inf, 1):
            text += f"{i}. {u['nickname']} — {u['influence']}\n"

        text += "\n🤝 <b>Доверие:</b>\n"
        for i, u in enumerate(top_tr, 1):
            text += f"{i}. {u['nickname']} — {u['trust']}\n"

        text += "\n✨ <b>Аура:</b>\n"
        for i, u in enumerate(top_au, 1):
            text += f"{i}. {u['nickname']} — {u['aura']:.2f}\n"

        if top_cl:
            text += "\n🏰 <b>Кланы:</b>\n"
            for i, c in enumerate(top_cl, 1):
                text += f"{i}. {c['name']} — 💰 {c['total_wealth']:.0f}\n"
        return text

    @staticmethod
    def get_help_text() -> str:
        return (
            "🎮 <b>Правила игры</b>\n"
            "━━━━━━━━━━━━━━━\n\n"
            "📌 <b>Как зарабатывать влияние:</b>\n"
            "• Любое сообщение → +1\n"
            "• Ответ на чужое сообщение → +2 (кулдаун 3 мин)\n"
            "• Упоминание @username → +3 (кулдаун 3 мин)\n"
            "• Пригласить новичка → +15 (когда новичок напишет 5 сообщений или проведёт 24 ч)\n"
            "• Ежедневный бонус за первое сообщение → +5\n\n"

            "🤝 <b>Доверие:</b>\n"
            "• Напиши \"доверяю\" в ответ на сообщение игрока\n"
            "• +1 доверия получает тот, кому ответили\n"
            "• Один раз в 24 часа одному игроку\n"
            "• Для сделок и создания клана нужно ≥10 доверия\n\n"

            "💱 <b>Сделки:</b>\n"
            "• Формат: @username количество влияние/аура\n"
            "• Пример: @durov 50 влияние\n"
            "• Получатель должен ответить \"согласен\" в течение 3 минут\n"
            "• При нарушении отправитель теряет 1 доверие\n\n"

            "✨ <b>Аура:</b>\n"
            "• Купить: \"купить аура 10\" — цена РАСТЁТ\n"
            "• Продать: \"продать аура 5\" — цена ПАДАЕТ\n"
            "• Изменение цены: +5% за покупку, -3% за продажу\n\n"

            "🏰 <b>Кланы:</b>\n"
            "• Создать: \"создать клан Название\" (200 влияния + 10 доверия)\n"
            "• Альянс: \"альянс с НазваниеКлана\"\n"
            "• Профиль клана: \"профиль клана Название\"\n\n"

            "⚔️ <b>Войны:</b>\n"
            "• Объявить: \"война НазваниеКлана\" (100 из казны)\n"
            "• Атаковать: \"атака @ник\" (урон = влияние*0.1 + доверие*2)\n"
            "• Статус: \"статус войны НазваниеКлана\"\n"
            "• Рейтинг силы: \"рейтинг кланов\"\n\n"

            "⭐ <b>Карма:</b>\n"
            "• Поставить +1/-1: \"поставить +1 @ник\" (раз в день)\n\n"

            "🏦 <b>Банк:</b>\n"
            "• Вклад: \"вклад 100 на 7 дн\"\n"
            "• Кредит: \"кредит 200 под залог 50 ауры\"\n\n"

            "🕵️ <b>Шпионаж:</b>\n"
            "• Отправить шпиона: \"шпионаж НазваниеКлана\"\n\n"

            "🏆 <b>Турниры:</b>\n"
            "• Участвовать: \"турнир\"\n\n"

            "📊 <b>Другие команды:</b>\n"
            "• \"Профиль\" — твоя статистика\n"
            "• \"Топ\" — рейтинги\n"
            "• \"Достижения\" — список полученных ачивок\n"
            "• \"Уровень\" — текущий уровень и опыт\n"
            "• \"Помощь\" — это сообщение"
        )

    @staticmethod
    async def get_clan_profile_text(clan_id: int) -> str:
        clan = await db.get_clan(clan_id)
        if not clan:
            return "❌ Клан не найден."

        members = await db.get_clan_members(clan_id)
        alliances = await db.get_clan_alliances(clan_id)
        alliance_names = []
        for ally_id in alliances:
            ally = await db.get_clan(ally_id)
            if ally:
                alliance_names.append(ally['name'])

        leader = await db.get_user(clan['leader_id'])
        price = await db.get_current_aura_price()
        roles = await db.get_clan_roles(clan_id)

        total_wealth = clan['influence_treasury'] + clan['aura_treasury'] * price
        text = (
            f"🏰 <b>Клан {clan['name']}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👑 Лидер: {leader['nickname'] if leader else '?'}\n"
            f"📊 Статус: {clan['status']}\n"
            f"💰 Казна: {clan['influence_treasury']} влияния, {clan['aura_treasury']:.2f} ауры\n"
            f"💎 Общая стоимость: {total_wealth:.0f}\n"
            f"👥 Участников: {len(members)}\n"
            f"🏆 Побед в войнах: {clan['wars_won']} | Поражений: {clan['wars_lost']}\n"
        )
        if alliance_names:
            text += f"🤝 Альянсы: {', '.join(alliance_names)}\n"
        if roles['officer']:
            officers = []
            for oid in roles['officer']:
                u = await db.get_user(oid)
                if u:
                    officers.append(u['nickname'])
            text += f"🔰 Офицеры: {', '.join(officers)}\n"
        if roles['treasurer']:
            treasurers = []
            for tid in roles['treasurer']:
                u = await db.get_user(tid)
                if u:
                    treasurers.append(u['nickname'])
            text += f"💰 Казначеи: {', '.join(treasurers)}\n"

        text += "\n👤 <b>Участники:</b>\n"
        for member in members[:10]:
            text += f"• {member['nickname']} (В:{member['influence']} Д:{member['trust']})\n"
        if len(members) > 10:
            text += f"... и ещё {len(members) - 10}\n"
        return text

    @staticmethod
    async def get_profile_text(user_id: int) -> str:
        user = await db.get_user(user_id)
        if not user:
            return "❌ Пользователь не найден."

        clan_name = "Нет"
        if user.get('clan_id'):
            clan = await db.get_clan(user['clan_id'])
            if clan:
                clan_name = clan['name']

        text = (
            f"👤 <b>Профиль игрока {user['nickname']}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Влияние: {user['influence']}\n"
            f"⭐ Доверие: {user['trust']}\n"
            f"💎 Аура: {user['aura']:.2f}\n"
            f"🏰 Клан: {clan_name}\n"
            f"📊 Уровень: {user['level']} (XP: {user['experience']})\n"
            f"💬 Сообщений: {user['messages_count']}\n"
            f"🤝 Сделок: {user['deals_count']}\n"
            f"⚔️ Побед в войнах: {user['war_wins']}\n"
            f"🤝 Альянсов: {user['alliances_count']}\n"
            f"📨 Приглашений: {user['invites_count']}\n"
        )
        return text

    @staticmethod
    async def get_clan_power_rating_text(limit: int = 10) -> str:
        top_clans = await db.get_clans_by_power(limit)
        text = "⚔️ <b>РЕЙТИНГ СИЛЫ КЛАНОВ</b> ⚔️\n━━━━━━━━━━━━━━━\n\n"
        for i, clan in enumerate(top_clans, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "📌"
            leader = await db.get_user(clan['leader_id'])
            leader_name = leader['nickname'] if leader else "?"
            text += (
                f"{medal} <b>{i}. {clan['name']}</b>\n"
                f"   👑 Лидер: {leader_name}\n"
                f"   ⚡ Сила: {clan['power']:.0f}\n"
                f"   👥 Участников: {clan['member_count']}\n"
                f"   🏆 Побед: {clan['wars_won']} | Поражений: {clan['wars_lost']}\n\n"
            )
        return text

    @staticmethod
    async def get_war_status_text(war_id: int) -> str:
        async with db.conn.execute(
            """SELECT w.*, 
                      c1.name as clan1_name, 
                      c2.name as clan2_name,
                      COUNT(wa.id) as total_attacks
               FROM wars w
               JOIN clans c1 ON w.clan1_id = c1.id
               JOIN clans c2 ON w.clan2_id = c2.id
               LEFT JOIN war_attacks wa ON w.id = wa.war_id
               WHERE w.id = ?
               GROUP BY w.id""",
            (war_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return "Война не найдена."
        war = dict(row)
        ends_at = datetime.fromisoformat(war['ends_at'])
        time_left = ends_at - datetime.now()
        hours_left = max(0, int(time_left.total_seconds() / 3600))
        minutes_left = max(0, int((time_left.total_seconds() % 3600) / 60))
        status = "АКТИВНА" if war['status'] == 'active' else "ЗАВЕРШЕНА"
        text = (
            f"⚔️ <b>ВОЙНА: {war['clan1_name']} VS {war['clan2_name']}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 Статус: {status}\n"
            f"⏳ Осталось: {hours_left}ч {minutes_left}м\n"
            f"💥 Всего атак: {war['total_attacks']}\n\n"
            f"<b>Счёт:</b>\n"
            f"🔴 {war['clan1_name']}: {war['clan1_damage']} урона\n"
            f"🔵 {war['clan2_name']}: {war['clan2_damage']} урона\n"
        )
        if war['status'] == 'finished' and war['winner_id']:
            winner_name = war['clan1_name'] if war['winner_id'] == war['clan1_id'] else war['clan2_name']
            text += f"\n🏆 <b>Победитель: {winner_name}</b>\n"
        return text

    @staticmethod
    async def get_achievements_text(user_id: int) -> str:
        user = await db.get_user(user_id)
        if not user:
            return "❌ Пользователь не найден."
        async with db.conn.execute(
            "SELECT a.* FROM achievements a JOIN user_achievements ua ON a.id = ua.achievement_id WHERE ua.user_id = ?",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            return "У вас пока нет достижений."
        text = f"🏆 <b>Достижения {user['nickname']}</b>\n━━━━━━━━━━━━━━━\n\n"
        for row in rows:
            ach = dict(row)
            text += f"✨ <b>{ach['name']}</b> — {ach['description']}\n"
            bonuses = []
            if ach['reward_influence']:
                bonuses.append(f"+{ach['reward_influence']} влияния")
            if ach['reward_aura']:
                bonuses.append(f"+{ach['reward_aura']} ауры")
            if ach['reward_bonus'] and ach['reward_bonus'] != '{}':
                bonus_data = json.loads(ach['reward_bonus'])
                for k, v in bonus_data.items():
                    bonuses.append(f"{k}: +{v*100}%")
            if bonuses:
                text += f"   🎁 Награда: {', '.join(bonuses)}\n"
            text += "\n"
        return text

    @staticmethod
    async def get_level_text(user_id: int) -> str:
        user = await db.get_user(user_id)
        if not user:
            return "❌ Пользователь не найден."
        level = user['level']
        exp = user['experience']
        next_exp = level * EXP_PER_LEVEL
        progress = exp / next_exp * 100 if next_exp > 0 else 0
        bar = "█" * int(progress // 10) + "░" * (10 - int(progress // 10))
        return (
            f"📊 <b>Уровень {user['nickname']}</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Текущий уровень: {level}\n"
            f"Опыт: {exp} / {next_exp}\n"
            f"[{bar}] {progress:.1f}%\n"
            f"Следующий уровень через {next_exp - exp} опыта."
        )

    @staticmethod
    async def check_expired_deals() -> List[Dict]:
        expired = await db.get_expired_deals()
        events = []
        for deal in expired:
            await db.update_deal_status(deal['id'], 'cancelled')
            sender = await db.get_user(deal['sender_id'])
            recipient = await db.get_user(deal['recipient_id'])
            events.append({
                'type': 'deal_cancelled',
                'sender_nick': sender['nickname'] if sender else '?',
                'recipient_nick': recipient['nickname'] if recipient else '?'
            })
        return events

    @staticmethod
    async def finish_wars_periodically():
        """Проверка завершившихся войн (вызывать раз в минуту из фоновой задачи)"""
        async with db.conn.execute(
            "SELECT * FROM wars WHERE status = 'active' AND ends_at <= datetime('now')"
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            war = dict(row)
            result = await db.finish_war(war['id'])
            if result:
                # Увеличиваем счётчик побед участникам победившего клана (для достижений)
                winner_members = await db.get_clan_members(result['winner_id'])
                for member in winner_members:
                    await db.update_user(member['id'], war_wins=member['war_wins'] + 1)
                    await db.check_achievements(member['id'])
                # TODO: оповестить кланы (можно через events)