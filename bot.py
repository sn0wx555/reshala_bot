import asyncio
import re
from telethon import TelegramClient, events
from collections import Counter
from datetime import datetime
import logging
import json
import os

API_ID = 33054445
API_HASH = '25bd08942b2d82d8d66bbb35090df1eb'
PHONE = '+380961919188'
CHAT_LINK = 'https://t.me/reshala_cIub'
YOUR_ID = 8516597211

SESSION_FILE = 'roulette_player'
DATA_FILE = 'game_stats.json'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class AutoRoulettePlayer:
    def __init__(self):
        self.client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        self.chat = None
        self.balance = 0
        self.current_bet_total = 0
        self.hot_numbers = []
        self.stats = self.load_stats()
        self.last_results = []
        self.game_active = False
        
    def load_stats(self):
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        return {
            'total_games': 0,
            'wins': 0,
            'losses': 0,
            'profit': 0,
            'max_balance': 0,
            'total_bet': 0,
            'history': []
        }
    
    def save_stats(self):
        with open(DATA_FILE, 'w') as f:
            json.dump(self.stats, f, indent=2)
    
    async def start(self):
        await self.client.start(phone=PHONE)
        logging.info("✅ Успешно залогинился!")
        
        self.chat = await self.client.get_entity(CHAT_LINK)
        logging.info(f"✅ Подключился к чату: {self.chat.title}")
        
        @self.client.on(events.NewMessage(chats=self.chat))
        async def handler(event):
            await self.handle_message(event)
        
        logging.info("🔄 Бот запущен и слушает чат...")
        
        # Сразу запрашиваем баланс
        await asyncio.sleep(3)
        await self.client.send_message(self.chat, 'б')
        
        await self.client.run_until_disconnected()
    
    async def handle_message(self, event):
        text = event.raw_text
        
        # Обновление баланса
        if text.startswith('б') or 'баланс' in text.lower() or 'Balance' in text:
            await self.update_balance(text)
        
        # Получение результата рулетки
        elif ('⚫' in text or '🔴' in text or '🟢' in text) and len(text) < 20:
            await self.process_result(text)
        
        # Получение лога
        elif text.lower() == 'лог':
            await asyncio.sleep(2)
        
        # Ответ бота с логом (много строк с числами)
        elif '⚫' in text and len(text.split('\n')) > 5:
            await self.analyze_log(text)
    
    async def update_balance(self, text):
        """Парсит баланс из сообщения"""
        # Ищем числа в тексте (может быть с пробелами)
        numbers = re.findall(r'[\d\s]+', text)
        for num_str in numbers:
            clean_num = num_str.replace(' ', '').replace(',', '')
            if clean_num.isdigit() and len(clean_num) > 3:  # Баланс обычно больше 1000
                new_balance = int(clean_num)
                old_balance = self.balance
                self.balance = new_balance
                
                # Обновляем максимальный баланс
                if self.balance > self.stats['max_balance']:
                    self.stats['max_balance'] = self.balance
                
                # Считаем изменение
                if old_balance > 0:
                    change = self.balance - old_balance
                    if change > 0:
                        await self.send_report(f"💰 Прибыль: +{change} (баланс: {self.balance})")
                    elif change < 0:
                        await self.send_report(f"📉 Убыток: {change} (баланс: {self.balance})")
                else:
                    await self.send_report(f"💰 Баланс: {self.balance}")
                
                logging.info(f"💰 Баланс: {self.balance}")
                
                # Если есть горячие числа и баланс обновился - делаем ставку
                if self.hot_numbers and not self.game_active:
                    await self.make_bet()
                break
    
    async def analyze_log(self, text):
        """Анализирует лог и находит горячие числа"""
        lines = text.strip().split('\n')
        numbers = []
        
        for line in lines:
            match = re.search(r'(\d+)(?:[⚫🔴🟢])', line)
            if match:
                num = int(match.group(1))
                numbers.append(num)
        
        if len(numbers) >= 10:
            self.last_results = numbers[-30:]
            
            # Анализируем частоту выпадений
            counter = Counter(numbers[-20:])
            total = len(numbers[-20:])
            
            # Ищем числа, выпадающие чаще среднего (> 5% при норме 2.7%)
            hot = []
            for num, count in counter.most_common(15):
                percentage = (count / total) * 100
                if percentage > 6:  # Порог 6% (выше нормы)
                    hot.append(num)
            
            if len(hot) >= 5:  # Если нашли минимум 5 горячих
                self.hot_numbers = hot[:12]  # Берем топ-12 (максимум)
                logging.info(f"🔥 Горячие числа: {self.hot_numbers}")
                await self.send_report(f"🔥 Горячие числа: {', '.join(map(str, self.hot_numbers))}")
                
                # Если есть баланс - делаем ставку
                if self.balance > 0 and not self.game_active:
                    await self.make_bet()
            else:
                logging.info("⚠️ Недостаточно горячих чисел, ждем еще данных")
    
    async def make_bet(self):
        """Делает ставку на горячие числа"""
        if not self.hot_numbers:
            return
        
        # ПРАВИЛЬНЫЙ РАСЧЕТ СТАВКИ
        numbers_to_bet = min(len(self.hot_numbers), 12)  # Максимум 12 чисел
        hot_slice = self.hot_numbers[:numbers_to_bet]
        
        # Рассчитываем ставку на каждое число
        bet_per_number = int(self.balance / 3 / numbers_to_bet)
        
        # Округляем до красивых чисел (убираем копейки)
        if bet_per_number >= 1000:
            bet_per_number = (bet_per_number // 100) * 100  # Округляем до сотен
        elif bet_per_number >= 100:
            bet_per_number = (bet_per_number // 10) * 10  # Округляем до десятков
        
        total_bet = bet_per_number * numbers_to_bet
        
        # Проверяем, что ставка не больше трети баланса
        if total_bet > self.balance / 3:
            # Корректируем вниз
            bet_per_number = int((self.balance / 3) / numbers_to_bet)
            total_bet = bet_per_number * numbers_to_bet
        
        if bet_per_number < 1:
            logging.warning("⚠️ Слишком маленький баланс для ставки")
            return
        
        # Формируем сообщение со ставкой
        bet_text = f"{bet_per_number} " + " ".join(map(str, hot_slice))
        
        self.current_bet_total = total_bet
        self.game_active = True
        
        logging.info(f"🎲 Ставка: {bet_text}")
        logging.info(f"📊 Всего: {total_bet} (по {bet_per_number} на {numbers_to_bet} чисел)")
        
        await self.send_report(
            f"🎲 Ставлю {total_bet} GRAM\n"
            f"📊 По {bet_per_number} на числа: {', '.join(map(str, hot_slice))}"
        )
        
        # Отправляем ставку
        await self.client.send_message(self.chat, bet_text)
        
        # Ждем результат
        await asyncio.sleep(10)
    
    async def process_result(self, text):
        """Обрабатывает результат рулетки"""
        match = re.search(r'(\d+)([⚫🔴🟢])', text)
        if match:
            number = int(match.group(1))
            
            logging.info(f"🎯 Результат: {text}")
            
            if self.game_active and self.current_bet_total > 0:
                if number in self.hot_numbers:
                    # Выигрыш! Ставка на число дает x36
                    win_amount = self.current_bet_total * 36
                    profit = win_amount - self.current_bet_total
                    
                    self.stats['wins'] += 1
                    self.stats['profit'] += profit
                    self.stats['total_bet'] += self.current_bet_total
                    
                    await self.send_report(
                        f"🎉 **ВЫИГРЫШ!**\n"
                        f"💰 Ставка: {self.current_bet_total}\n"
                        f"💎 Выигрыш: {win_amount}\n"
                        f"📈 Прибыль: +{profit}"
                    )
                    
                    logging.info(f"🎉 ВЫИГРЫШ! +{profit}")
                else:
                    # Проигрыш
                    self.stats['losses'] += 1
                    self.stats['profit'] -= self.current_bet_total
                    self.stats['total_bet'] += self.current_bet_total
                    
                    await self.send_report(
                        f"😢 **ПРОИГРЫШ**\n"
                        f"💰 Потеряно: {self.current_bet_total}"
                    )
                    
                    logging.info(f"😢 Проигрыш: -{self.current_bet_total}")
                
                self.stats['total_games'] += 1
                self.save_stats()
                
                # Сбрасываем флаг игры
                self.game_active = False
                self.current_bet_total = 0
                
                # Запрашиваем новый баланс
                await asyncio.sleep(3)
                await self.client.send_message(self.chat, 'б')
    
    async def send_report(self, message):
        """Отправляет отчет в избранное"""
        await self.client.send_message(YOUR_ID, message)
        
    async def periodic_balance_check(self):
        """Периодически проверяет баланс"""
        while True:
            await asyncio.sleep(120)  # Каждые 2 минуты
            if not self.game_active:  # Не проверяем во время игры
                await self.client.send_message(self.chat, 'б')

async def main():
    player = AutoRoulettePlayer()
    
    # Запускаем периодическую проверку баланса
    asyncio.create_task(player.periodic_balance_check())
    
    await player.start()

if __name__ == '__main__':
    asyncio.run(main())
