#!/usr/bin/env python3
"""
Working Hours Tracker Telegram Bot - Complete Single File
Track login, logout, breaks, and generate reports
"""

import os
import sqlite3
import logging
import logging.handlers
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from enum import Enum
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ====================== SETUP ======================

load_dotenv()
Path("logs").mkdir(exist_ok=True)
Path("data").mkdir(exist_ok=True)


def setup_logger():
    logger = logging.getLogger("bot")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    fh = logging.handlers.RotatingFileHandler(
        "logs/app.log", maxBytes=10485760, backupCount=5
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


logger = setup_logger()

# ====================== CONSTANTS ======================

class WorkStatus(str, Enum):
    LOGGED_OUT = "logged_out"
    LOGGED_IN = "logged_in"
    SHORT_BREAK = "short_break"
    LUNCH_BREAK = "lunch_break"
    BIO_BREAK = "bio_break"
    IN_MEETING = "in_meeting"


class ActivityType(str, Enum):
    LOGIN = "login"
    LOGOUT = "logout"
    SHORT_BREAK = "short_break"
    LUNCH_BREAK = "lunch_break"
    BIO_BREAK = "bio_break"
    MEETING = "meeting"
    BACK_TO_WORK = "back_to_work"


STATUS_EMOJI = {
    WorkStatus.LOGGED_OUT: "🔴",
    WorkStatus.LOGGED_IN: "🟢",
    WorkStatus.SHORT_BREAK: "☕",
    WorkStatus.LUNCH_BREAK: "🍽️",
    WorkStatus.BIO_BREAK: "🚻",
    WorkStatus.IN_MEETING: "👥",
}

STATUS_TEXT = {
    WorkStatus.LOGGED_OUT: "Logged Out",
    WorkStatus.LOGGED_IN: "Working",
    WorkStatus.SHORT_BREAK: "Short Break",
    WorkStatus.LUNCH_BREAK: "Lunch Break",
    WorkStatus.BIO_BREAK: "Bio Break",
    WorkStatus.IN_MEETING: "In Meeting",
}

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("8841122696:AAEAnoshxq_A2cEiy1xC3MnUz1ZJ63Pgf0g")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/working_hours.db")
AUTO_LOGOUT_HOUR = int(os.getenv("AUTO_LOGOUT_HOUR", 18))
AUTO_LOGOUT_MINUTE = int(os.getenv("AUTO_LOGOUT_MINUTE", 0))

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is required in .env file")

# ====================== DATABASE ======================

class DatabaseManager:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DATABASE_URL.replace("sqlite:///./", "")
        self.init_database()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_database(self):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            username TEXT, first_name TEXT, last_name TEXT,
            current_status TEXT DEFAULT 'logged_out',
            last_login DATETIME, last_logout DATETIME,
            total_working_hours REAL DEFAULT 0.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, activity_type TEXT NOT NULL,
            status_before TEXT, status_after TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            duration_seconds INTEGER, notes TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS break_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, break_type TEXT NOT NULL,
            start_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            end_time DATETIME, duration_seconds INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, date DATE NOT NULL,
            login_time DATETIME, logout_time DATETIME,
            total_working_hours REAL, total_break_hours REAL,
            short_break_count INTEGER DEFAULT 0,
            lunch_break_count INTEGER DEFAULT 0,
            bio_break_count INTEGER DEFAULT 0,
            meeting_count INTEGER DEFAULT 0,
            UNIQUE(user_id, date))''')
        c.execute('''CREATE TABLE IF NOT EXISTS monthly_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, year INTEGER NOT NULL, month INTEGER NOT NULL,
            total_working_hours REAL DEFAULT 0.0,
            total_break_hours REAL DEFAULT 0.0,
            working_days INTEGER DEFAULT 0,
            UNIQUE(user_id, year, month))''')
        conn.commit()
        conn.close()
        logger.info("Database initialized")

    # User methods
    def add_user(self, user_id, username, first_name, last_name=""):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''INSERT OR IGNORE INTO users
            (user_id, username, first_name, last_name, current_status)
            VALUES (?, ?, ?, ?, ?)''',
            (user_id, username, first_name, last_name, WorkStatus.LOGGED_OUT.value))
        conn.commit()
        conn.close()

    def get_user(self, user_id) -> Optional[Dict]:
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_user_status(self, user_id) -> str:
        user = self.get_user(user_id)
        return user['current_status'] if user else WorkStatus.LOGGED_OUT.value

    def update_user_status(self, user_id, status):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('UPDATE users SET current_status=?, updated_at=? WHERE user_id=?',
                  (status, datetime.now(), user_id))
        conn.commit()
        conn.close()

    def update_user_login(self, user_id):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('UPDATE users SET last_login=?, updated_at=? WHERE user_id=?',
                  (datetime.now(), datetime.now(), user_id))
        conn.commit()
        conn.close()

    def update_user_logout(self, user_id):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('UPDATE users SET last_logout=?, updated_at=? WHERE user_id=?',
                  (datetime.now(), datetime.now(), user_id))
        conn.commit()
        conn.close()

    # Activity methods
    def log_activity(self, user_id, activity_type, status_before, status_after,
                     duration_seconds=None):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''INSERT INTO activity_logs
            (user_id, activity_type, status_before, status_after, timestamp, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (user_id, activity_type, status_before, status_after,
             datetime.now(), duration_seconds))
        conn.commit()
        conn.close()

    def get_activity_logs_by_date(self, user_id, target_date) -> List[Dict]:
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''SELECT * FROM activity_logs
            WHERE user_id=? AND DATE(timestamp)=? ORDER BY timestamp ASC''',
            (user_id, target_date.isoformat()))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows

    # Break methods
    def log_break(self, user_id, break_type):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('INSERT INTO break_logs (user_id, break_type, start_time) VALUES (?, ?, ?)',
                  (user_id, break_type, datetime.now()))
        conn.commit()
        conn.close()

    def end_break(self, user_id) -> Optional[Dict]:
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''SELECT * FROM break_logs WHERE user_id=?
            AND end_time IS NULL ORDER BY start_time DESC LIMIT 1''', (user_id,))
        row = c.fetchone()
        if row:
            break_dict = dict(row)
            end_time = datetime.now()
            start_time = datetime.fromisoformat(break_dict['start_time'])
            duration = int((end_time - start_time).total_seconds())
            c.execute('UPDATE break_logs SET end_time=?, duration_seconds=? WHERE id=?',
                      (end_time, duration, break_dict['id']))
            conn.commit()
            conn.close()
            return break_dict
        conn.close()
        return None

    def get_today_breaks(self, user_id) -> List[Dict]:
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''SELECT * FROM break_logs WHERE user_id=?
            AND DATE(start_time)=? ORDER BY start_time DESC''',
            (user_id, date.today().isoformat()))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows

    # Daily summary
    def get_or_create_daily_summary(self, user_id, target_date=None) -> Dict:
        target_date = target_date or date.today()
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('SELECT * FROM daily_summary WHERE user_id=? AND date=?',
                  (user_id, target_date.isoformat()))
        row = c.fetchone()
        if not row:
            c.execute('INSERT INTO daily_summary (user_id, date) VALUES (?, ?)',
                      (user_id, target_date.isoformat()))
            conn.commit()
            c.execute('SELECT * FROM daily_summary WHERE user_id=? AND date=?',
                      (user_id, target_date.isoformat()))
            row = c.fetchone()
        conn.close()
        return dict(row) if row else {}

    def update_daily_summary(self, user_id, target_date=None):
        target_date = target_date or date.today()
        activities = self.get_activity_logs_by_date(user_id, target_date)
        breaks = self.get_today_breaks(user_id)

        login_time = logout_time = None
        total_break_seconds = 0
        counts = {'short_break': 0, 'lunch_break': 0, 'bio_break': 0, 'meeting': 0}

        for a in activities:
            if a['activity_type'] == ActivityType.LOGIN.value:
                login_time = a['timestamp']
            elif a['activity_type'] == ActivityType.LOGOUT.value:
                logout_time = a['timestamp']

        for b in breaks:
            if b['duration_seconds']:
                total_break_seconds += b['duration_seconds']
                if b['break_type'] in counts:
                    counts[b['break_type']] += 1

        working_seconds = 0
        if login_time and logout_time:
            total = int((datetime.fromisoformat(logout_time) -
                        datetime.fromisoformat(login_time)).total_seconds())
            working_seconds = max(0, total - total_break_seconds)

        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''UPDATE daily_summary SET login_time=?, logout_time=?,
            total_working_hours=?, total_break_hours=?,
            short_break_count=?, lunch_break_count=?,
            bio_break_count=?, meeting_count=?
            WHERE user_id=? AND date=?''',
            (login_time, logout_time, working_seconds/3600, total_break_seconds/3600,
             counts['short_break'], counts['lunch_break'], counts['bio_break'],
             counts['meeting'], user_id, target_date.isoformat()))
        conn.commit()
        conn.close()

    def get_today_summary(self, user_id) -> Dict:
        self.get_or_create_daily_summary(user_id)
        self.update_daily_summary(user_id)
        return self.get_or_create_daily_summary(user_id)

    # Monthly summary
    def get_or_create_monthly_summary(self, user_id, year=None, month=None) -> Dict:
        today = date.today()
        year = year or today.year
        month = month or today.month
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''SELECT * FROM monthly_summary WHERE user_id=? AND year=? AND month=?''',
                  (user_id, year, month))
        row = c.fetchone()
        if not row:
            c.execute('INSERT INTO monthly_summary (user_id, year, month) VALUES (?, ?, ?)',
                      (user_id, year, month))
            conn.commit()
            c.execute('''SELECT * FROM monthly_summary WHERE user_id=? AND year=? AND month=?''',
                      (user_id, year, month))
            row = c.fetchone()
        conn.close()
        return dict(row) if row else {}

    def update_monthly_summary(self, user_id, year=None, month=None):
        today = date.today()
        year = year or today.year
        month = month or today.month
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''SELECT * FROM daily_summary WHERE user_id=?
            AND CAST(strftime('%Y', date) AS INTEGER)=?
            AND CAST(strftime('%m', date) AS INTEGER)=?''', (user_id, year, month))
        daily = [dict(r) for r in c.fetchall()]
        total_work = sum(d['total_working_hours'] or 0 for d in daily)
        total_break = sum(d['total_break_hours'] or 0 for d in daily)
        working_days = len([d for d in daily if d['login_time']])
        c.execute('''UPDATE monthly_summary SET total_working_hours=?,
            total_break_hours=?, working_days=?
            WHERE user_id=? AND year=? AND month=?''',
            (total_work, total_break, working_days, user_id, year, month))
        conn.commit()
        conn.close()

    def get_current_monthly_summary(self, user_id) -> Dict:
        today = date.today()
        self.get_or_create_monthly_summary(user_id, today.year, today.month)
        self.update_monthly_summary(user_id, today.year, today.month)
        return self.get_or_create_monthly_summary(user_id, today.year, today.month)


# ====================== TRACKING SERVICE ======================

class TrackingService:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def login(self, user_id) -> Tuple[bool, str]:
        status = self.db.get_user_status(user_id)
        if status == WorkStatus.LOGGED_IN.value:
            return False, "❌ You are already logged in!"
        self.db.log_activity(user_id, ActivityType.LOGIN.value,
                              status, WorkStatus.LOGGED_IN.value)
        self.db.update_user_status(user_id, WorkStatus.LOGGED_IN.value)
        self.db.update_user_login(user_id)
        return True, f"✅ Logged in at {datetime.now().strftime('%H:%M:%S')}"

    def logout(self, user_id) -> Tuple[bool, str]:
        status = self.db.get_user_status(user_id)
        if status == WorkStatus.LOGGED_OUT.value:
            return False, "❌ You are already logged out!"
        self.db.log_activity(user_id, ActivityType.LOGOUT.value,
                              status, WorkStatus.LOGGED_OUT.value)
        self.db.update_user_status(user_id, WorkStatus.LOGGED_OUT.value)
        self.db.update_user_logout(user_id)
        self.db.update_daily_summary(user_id)
        return True, f"✅ Logged out at {datetime.now().strftime('%H:%M:%S')}"

    def start_break(self, user_id, break_type) -> Tuple[bool, str]:
        status = self.db.get_user_status(user_id)
        if status == WorkStatus.LOGGED_OUT.value:
            return False, "❌ You must be logged in to take a break!"

        # End any existing break first (fix: prevents orphaned break records)
        break_statuses = [WorkStatus.SHORT_BREAK.value, WorkStatus.LUNCH_BREAK.value,
                         WorkStatus.BIO_BREAK.value, WorkStatus.IN_MEETING.value]
        if status in break_statuses:
            self.db.end_break(user_id)

        status_map = {
            'short_break': WorkStatus.SHORT_BREAK.value,
            'lunch_break': WorkStatus.LUNCH_BREAK.value,
            'bio_break': WorkStatus.BIO_BREAK.value,
            'meeting': WorkStatus.IN_MEETING.value,
        }
        if break_type not in status_map:
            return False, f"❌ Invalid break type: {break_type}"

        new_status = status_map[break_type]
        self.db.log_break(user_id, break_type)
        self.db.log_activity(user_id, break_type, status, new_status)
        self.db.update_user_status(user_id, new_status)

        emoji = {'short_break': '☕', 'lunch_break': '🍽️',
                'bio_break': '🚻', 'meeting': '👥'}[break_type]
        return True, f"{emoji} Break started at {datetime.now().strftime('%H:%M:%S')}"

    def end_break(self, user_id) -> Tuple[bool, str]:
        status = self.db.get_user_status(user_id)
        break_statuses = [WorkStatus.SHORT_BREAK.value, WorkStatus.LUNCH_BREAK.value,
                         WorkStatus.BIO_BREAK.value, WorkStatus.IN_MEETING.value]
        if status not in break_statuses:
            return False, "❌ You are not on a break!"

        info = self.db.end_break(user_id)
        if not info:
            return False, "❌ No active break found!"

        duration_min = (info.get('duration_seconds') or 0) // 60
        self.db.log_activity(user_id, ActivityType.BACK_TO_WORK.value,
                              status, WorkStatus.LOGGED_IN.value,
                              info.get('duration_seconds'))
        self.db.update_user_status(user_id, WorkStatus.LOGGED_IN.value)
        return True, f"💼 Back to work! Break lasted {duration_min} minutes"

    def get_current_status(self, user_id) -> Dict:
        user = self.db.get_user(user_id)
        if not user:
            return {}
        status = user['current_status']
        return {
            'status': status,
            'emoji': STATUS_EMOJI.get(status, '❓'),
            'text': STATUS_TEXT.get(status, 'Unknown'),
            'last_login': user['last_login'],
            'last_logout': user['last_logout'],
        }


# ====================== REPORTS SERVICE ======================

class ReportsService:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def daily_report(self, user_id, target_date=None) -> str:
        target_date = target_date or date.today()
        self.db.get_or_create_daily_summary(user_id, target_date)
        self.db.update_daily_summary(user_id, target_date)
        s = self.db.get_or_create_daily_summary(user_id, target_date)

        login = s.get('login_time')
        logout = s.get('logout_time')
        work_h = s.get('total_working_hours') or 0
        break_h = s.get('total_break_hours') or 0

        if login:
            login = datetime.fromisoformat(login).strftime('%H:%M:%S')
        else:
            login = 'N/A'
        if logout:
            logout = datetime.fromisoformat(logout).strftime('%H:%M:%S')
        else:
            logout = 'N/A'

        return f"""📊 *Daily Report - {target_date.strftime("%A, %B %d, %Y")}*

🕐 *Working Hours*
├─ Login Time: {login}
├─ Logout Time: {logout}
├─ Total Working: {work_h:.2f} hours
└─ Total Break: {break_h:.2f} hours

☕ *Breaks Taken*
├─ Short Breaks: {s.get('short_break_count', 0) or 0}
├─ Lunch Breaks: {s.get('lunch_break_count', 0) or 0}
├─ Bio Breaks: {s.get('bio_break_count', 0) or 0}
└─ Meetings: {s.get('meeting_count', 0) or 0}

⏱️ *Productive Hours:* {max(0, work_h - break_h):.2f} hours"""

    def monthly_report(self, user_id, year=None, month=None) -> str:
        today = date.today()
        year = year or today.year
        month = month or today.month
        self.db.get_or_create_monthly_summary(user_id, year, month)
        self.db.update_monthly_summary(user_id, year, month)
        s = self.db.get_or_create_monthly_summary(user_id, year, month)

        work_h = s.get('total_working_hours') or 0
        break_h = s.get('total_break_hours') or 0
        days = s.get('working_days') or 0
        avg = work_h / days if days > 0 else 0
        expected = days * 8
        productivity = (work_h / expected * 100) if expected > 0 else 0
        month_name = date(year, month, 1).strftime("%B %Y")

        return f"""📅 *Monthly Report - {month_name}*

📈 *Summary*
├─ Working Days: {days}
├─ Total Working: {work_h:.2f} hours
├─ Total Break: {break_h:.2f} hours
└─ Average Daily: {avg:.2f} hours

⏱️ *Statistics*
├─ Expected (8h/day): {expected:.2f} hours
├─ Difference: {work_h - expected:+.2f} hours
└─ Productivity: {productivity:.1f}%"""


# ====================== TELEGRAM HANDLERS ======================

def get_keyboard():
    keyboard = [
        ["🟢 Login", "🔴 Logout"],
        ["☕ Short Break", "🍽️ Lunch Break", "🚻 Bio Break"],
        ["👥 Meeting", "💼 Back to Work"],
        ["📊 My Daily Report", "📅 My Monthly Report"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db = context.bot_data['db']
    db.add_user(user.id, user.username or "", user.first_name, user.last_name or "")

    await update.message.reply_text(
        f"👋 Welcome {user.first_name}!\n\n"
        "I'm your Working Hours Tracker bot.\n\n"
        "🟢 *Login* - Start your work day\n"
        "🔴 *Logout* - End your work day\n"
        "☕ *Short Break* - 5-10 min break\n"
        "🍽️ *Lunch Break* - Lunch time\n"
        "🚻 *Bio Break* - Quick break\n"
        "👥 *Meeting* - In a meeting\n"
        "💼 *Back to Work* - Return from break\n"
        "📊 *Daily Report* - Today's summary\n"
        "📅 *Monthly Report* - Monthly summary\n\n"
        "Type /help for more info.",
        reply_markup=get_keyboard(),
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """📖 *Help & Instructions*

*How to use:*
1️⃣ Click 🟢 *Login* when you start working
2️⃣ Use break buttons when taking breaks
3️⃣ Click 💼 *Back to Work* when returning
4️⃣ Click 🔴 *Logout* at end of day
5️⃣ View reports anytime with 📊 or 📅

*Commands:*
/start - Start the bot
/help - Show this help
/status - Check current status
/login - Login (same as button)
/logout - Logout (same as button)"""
    await update.message.reply_text(help_text, reply_markup=get_keyboard(), parse_mode="Markdown")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.bot_data['db'].add_user(user.id, user.username or "", user.first_name, user.last_name or "")
    status = context.bot_data['tracking'].get_current_status(user.id)
    if status:
        text = f"{status['emoji']} *Current Status:* {status['text']}"
        if status['last_login']:
            text += f"\n🕐 Last Login: {status['last_login']}"
        await update.message.reply_text(text, reply_markup=get_keyboard(), parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Please start with /start", reply_markup=get_keyboard())


async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.bot_data['db'].add_user(user.id, user.username or "", user.first_name, user.last_name or "")
    _, msg = context.bot_data['tracking'].login(user.id)
    await update.message.reply_text(msg, reply_markup=get_keyboard())


async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.bot_data['db'].add_user(user.id, user.username or "", user.first_name, user.last_name or "")
    _, msg = context.bot_data['tracking'].logout(user.id)
    await update.message.reply_text(msg, reply_markup=get_keyboard())


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.", reply_markup=get_keyboard())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    db = context.bot_data['db']
    tracking = context.bot_data['tracking']
    reports = context.bot_data['reports']

    db.add_user(user.id, user.username or "", user.first_name, user.last_name or "")

    if text == "🟢 Login":
        _, msg = tracking.login(user.id)
        await update.message.reply_text(msg, reply_markup=get_keyboard())
    elif text == "🔴 Logout":
        _, msg = tracking.logout(user.id)
        await update.message.reply_text(msg, reply_markup=get_keyboard())
    elif text == "☕ Short Break":
        _, msg = tracking.start_break(user.id, "short_break")
        await update.message.reply_text(msg, reply_markup=get_keyboard())
    elif text == "🍽️ Lunch Break":
        _, msg = tracking.start_break(user.id, "lunch_break")
        await update.message.reply_text(msg, reply_markup=get_keyboard())
    elif text == "🚻 Bio Break":
        _, msg = tracking.start_break(user.id, "bio_break")
        await update.message.reply_text(msg, reply_markup=get_keyboard())
    elif text == "👥 Meeting":
        _, msg = tracking.start_break(user.id, "meeting")
        await update.message.reply_text(msg, reply_markup=get_keyboard())
    elif text == "💼 Back to Work":
        _, msg = tracking.end_break(user.id)
        await update.message.reply_text(msg, reply_markup=get_keyboard())
    elif text == "📊 My Daily Report":
        report = reports.daily_report(user.id)
        await update.message.reply_text(report, reply_markup=get_keyboard(), parse_mode="Markdown")
    elif text == "📅 My Monthly Report":
        report = reports.monthly_report(user.id)
        await update.message.reply_text(report, reply_markup=get_keyboard(), parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "❓ Use the buttons or type /help",
            reply_markup=get_keyboard()
        )

    logger.info(f"User {user.id} ({user.first_name}): {text}")


async def error_handler(update, context):
    logger.error(f"Error: {context.error}", exc_info=context.error)
    if update and update.effective_message:
        await update.effective_message.reply_text("❌ An error occurred. Please try again.")


# ====================== MAIN BOT ======================

class WorkingHoursBot:
    def __init__(self):
        self.db = DatabaseManager()
        self.tracking = TrackingService(self.db)
        self.reports = ReportsService(self.db)
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.scheduler = BackgroundScheduler()

        self.app.bot_data['db'] = self.db
        self.app.bot_data['tracking'] = self.tracking
        self.app.bot_data['reports'] = self.reports

        self.setup_handlers()

    def setup_handlers(self):
        self.app.add_handler(CommandHandler("start", start_command))
        self.app.add_handler(CommandHandler("help", help_command))
        self.app.add_handler(CommandHandler("status", status_command))
        self.app.add_handler(CommandHandler("login", login_command))
        self.app.add_handler(CommandHandler("logout", logout_command))
        self.app.add_handler(CommandHandler("cancel", cancel_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        self.app.add_error_handler(error_handler)

    def setup_scheduler(self):
        try:
            self.scheduler.add_job(
                self._log_end_of_day,
                CronTrigger(hour=AUTO_LOGOUT_HOUR, minute=AUTO_LOGOUT_MINUTE),
                id='end_of_day'
            )
            if not self.scheduler.running:
                self.scheduler.start()
            logger.info(f"Scheduled end-of-day log at {AUTO_LOGOUT_HOUR:02d}:{AUTO_LOGOUT_MINUTE:02d}")
        except Exception as e:
            logger.error(f"Scheduler setup error: {e}")

    def _log_end_of_day(self):
        logger.info(f"End of day ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")

    def run(self):
        logger.info("=" * 60)
        logger.info("Starting Working Hours Tracker Bot")
        logger.info("=" * 60)
        self.setup_scheduler()

        try:
            self.app.run_polling()
        except KeyboardInterrupt:
            logger.info("Bot stopped")
        finally:
            if self.scheduler.running:
                self.scheduler.shutdown()


def main():
    try:
        bot = WorkingHoursBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
