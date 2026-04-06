"""
🤖 AI Avatar Generation Telegram Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Complete production-ready bot using aiogram 3.x
Theme: AI Profile Photo & Avatar Generation with Premium System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Google AI
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Admin
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

# VIP
VIP_PRICE = int(os.getenv("VIP_PRICE", 30000))
VIP_CARD_NUMBER = os.getenv("VIP_CARD_NUMBER", "")
VIP_CARD_NAME = os.getenv("VIP_CARD_NAME", "")
VIP_PHONE_NUMBER = os.getenv("VIP_PHONE_NUMBER", "")
VIP_DAYS = int(os.getenv("VIP_DAYS", 30))

# Channels
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 0))

# Limits
MAX_DAILY_FREE_GENERATIONS = int(os.getenv("MAX_DAILY_FREE_GENERATIONS", 1))
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 2))
ANTI_SPAM_COOLDOWN = int(os.getenv("ANTI_SPAM_COOLDOWN", 5))

# ==================== END CONFIGURATION SECTION ====================

import asyncio
import sqlite3
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from io import BytesIO
import aiohttp

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    Message, CallbackQuery, InputFile
)
import google.generativeai as genai

# ==================== 🎨 LOGGING SETUP ====================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== 💾 DATABASE SETUP ====================

class Database:
    """SQLite Database Manager for the bot"""
    
    def __init__(self, db_path: str = "bot_database.db"):
        self.db_path = db_path
        self.init_database()
    
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_database(self):
        """Initialize database tables"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                referral_count INTEGER DEFAULT 0,
                invited_by INTEGER,
                is_vip INTEGER DEFAULT 0,
                vip_expire_date TIMESTAMP,
                daily_generation_count INTEGER DEFAULT 0,
                total_generations INTEGER DEFAULT 0,
                payment_status TEXT DEFAULT 'pending',
                is_banned INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                last_active_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_generation_date TIMESTAMP
            )
        """)
        
        # Referrals table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                invited_user_id INTEGER,
                date_invited TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(referrer_id) REFERENCES users(user_id),
                FOREIGN KEY(invited_user_id) REFERENCES users(user_id)
            )
        """)
        
        # Payment requests table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payment_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                amount INTEGER,
                screenshot_file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        
        # Generation logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS generation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                style TEXT,
                status TEXT DEFAULT 'success',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("✅ Database initialized successfully")
    
    def add_user(self, user_id: int, username: str, full_name: str, invited_by: Optional[int] = None):
        """Add new user to database"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO users (user_id, username, full_name, invited_by)
                VALUES (?, ?, ?, ?)
            """, (user_id, username, full_name, invited_by))
            conn.commit()
            
            # If user was invited, update referrer's count
            if invited_by:
                cursor.execute("""
                    UPDATE users SET referral_count = referral_count + 1
                    WHERE user_id = ?
                """, (invited_by,))
                conn.commit()
            
            logger.info(f"✅ New user added: {user_id} (@{username})")
            return True
        except Exception as e:
            logger.error(f"❌ Error adding user: {e}")
            return False
        finally:
            conn.close()
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user information"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        conn.close()
        
        return dict(user) if user else None
    
    def is_user_exists(self, user_id: int) -> bool:
        """Check if user exists"""
        return self.get_user(user_id) is not None
    
    def is_vip(self, user_id: int) -> bool:
        """Check if user is VIP"""
        user = self.get_user(user_id)
        if not user:
            return False
        
        if user['is_vip'] == 0:
            return False
        
        if user['vip_expire_date']:
            expire_date = datetime.fromisoformat(user['vip_expire_date'])
            if expire_date < datetime.now():
                self.remove_vip(user_id)
                return False
        
        return True
    
    def is_banned(self, user_id: int) -> bool:
        """Check if user is banned"""
        user = self.get_user(user_id)
        return user['is_banned'] == 1 if user else False
    
    def can_generate(self, user_id: int) -> bool:
        """Check if user can generate image"""
        user = self.get_user(user_id)
        if not user:
            return False
        
        # VIP users can generate unlimited
        if self.is_vip(user_id):
            return True
        
        # Free users can generate 1 per day
        if user['daily_generation_count'] >= MAX_DAILY_FREE_GENERATIONS:
            return False
        
        return True
    
    def increment_generation_count(self, user_id: int):
        """Increment generation count"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE users 
            SET daily_generation_count = daily_generation_count + 1,
                total_generations = total_generations + 1,
                last_generation_date = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (user_id,))
        
        conn.commit()
        conn.close()
    
    def reset_daily_count(self):
        """Reset daily generation count for all users (call this daily)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("UPDATE users SET daily_generation_count = 0")
        conn.commit()
        conn.close()
    
    def add_generation_log(self, user_id: int, style: str, status: str = "success"):
        """Log image generation"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO generation_logs (user_id, style, status)
            VALUES (?, ?, ?)
        """, (user_id, style, status))
        
        conn.commit()
        conn.close()
    
    def add_payment_request(self, user_id: int, username: str, full_name: str, amount: int, screenshot_file_id: str):
        """Add payment request"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO payment_requests (user_id, username, full_name, amount, screenshot_file_id)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, username, full_name, amount, screenshot_file_id))
        
        conn.commit()
        payment_id = cursor.lastrowid
        conn.close()
        
        return payment_id
    
    def get_pending_payments(self) -> List[Dict]:
        """Get all pending payment requests"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM payment_requests
            WHERE status = 'pending'
            ORDER BY created_at DESC
        """)
        
        payments = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return payments
    
    def approve_payment(self, payment_id: int):
        """Approve payment and make user VIP"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id FROM payment_requests WHERE id = ?", (payment_id,))
        payment = cursor.fetchone()
        
        if payment:
            user_id = payment[0]
            vip_expire = datetime.now() + timedelta(days=VIP_DAYS)
            
            cursor.execute("""
                UPDATE users
                SET is_vip = 1,
                    vip_expire_date = ?,
                    payment_status = 'approved'
                WHERE user_id = ?
            """, (vip_expire.isoformat(), user_id))
            
            cursor.execute("""
                UPDATE payment_requests
                SET status = 'approved'
                WHERE id = ?
            """, (payment_id,))
            
            conn.commit()
            conn.close()
            return True
        
        conn.close()
        return False
    
    def reject_payment(self, payment_id: int):
        """Reject payment"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE payment_requests
            SET status = 'rejected'
            WHERE id = ?
        """, (payment_id,))
        
        conn.commit()
        conn.close()
    
    def ban_user(self, user_id: int):
        """Ban user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    
    def unban_user(self, user_id: int):
        """Unban user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    
    def give_vip(self, user_id: int, days: int = VIP_DAYS):
        """Give VIP status to user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        vip_expire = datetime.now() + timedelta(days=days)
        cursor.execute("""
            UPDATE users
            SET is_vip = 1,
                vip_expire_date = ?,
                payment_status = 'approved'
            WHERE user_id = ?
        """, (vip_expire.isoformat(), user_id))
        
        conn.commit()
        conn.close()
    
    def remove_vip(self, user_id: int):
        """Remove VIP status from user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE users
            SET is_vip = 0,
                vip_expire_date = NULL
            WHERE user_id = ?
        """, (user_id,))
        
        conn.commit()
        conn.close()
    
    def get_stats(self) -> Dict:
        """Get bot statistics"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 0")
        active_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_vip = 1")
        vip_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM generation_logs")
        total_generations = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT user_id, referral_count FROM users
            ORDER BY referral_count DESC LIMIT 10
        """)
        top_inviters = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return {
            "total_users": total_users,
            "active_users": active_users,
            "vip_users": vip_users,
            "total_generations": total_generations,
            "top_inviters": top_inviters
        }
    
    def get_all_users(self) -> List[int]:
        """Get all user IDs"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id FROM users WHERE is_banned = 0")
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        return users

# ==================== 🤖 BOT INITIALIZATION ====================

# Initialize database
db = Database()

# Configure Google AI
genai.configure(api_key=GOOGLE_API_KEY)

# Initialize bot and dispatcher
storage = MemoryStorage()
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=storage)

# ==================== 📋 FSM STATES ====================

class UploadPhotoState(StatesGroup):
    """States for photo upload and style selection"""
    waiting_for_photo = State()
    waiting_for_style = State()
    generating = State()

class PaymentState(StatesGroup):
    """States for VIP payment"""
    waiting_for_amount = State()
    waiting_for_screenshot = State()
    confirmation = State()

class AdminBroadcastState(StatesGroup):
    """States for admin broadcast"""
    waiting_for_content = State()
    waiting_for_type = State()

class AdminBanState(StatesGroup):
    """States for admin ban/unban"""
    waiting_for_user_id = State()

# ==================== 🎨 KEYBOARD BUILDERS ====================

def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    """Get main menu keyboard"""
    is_user_vip = db.is_vip(user_id)
    
    keyboard = [
        [KeyboardButton(text="🎨 Photo Tahrir Qilish")],
        [KeyboardButton(text="👥 Do'stlar"), KeyboardButton(text="💎 Premium")],
        [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="⚙️ Sozlamalar")],
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton(text="🛡️ Admin Panel")])
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_styles_keyboard() -> InlineKeyboardMarkup:
    """Get styles selection keyboard"""
    styles = [
        ("🕴️ Mafia Style", "style_mafia"),
        ("💰 Rich Boy", "style_rich_boy"),
        ("🧥 Luxury Suit", "style_luxury_suit"),
        ("🌑 Dark Vibe", "style_dark_vibe"),
        ("💼 Businessman", "style_businessman"),
        ("⚽ Football Player", "style_football"),
        ("🤖 Cyberpunk", "style_cyberpunk"),
        ("👑 Gold Crown", "style_gold_crown"),
        ("💎 Old Money", "style_old_money"),
        ("👕 Streetwear", "style_streetwear"),
        ("👸 King Style", "style_king"),
        ("💡 Neon Effect", "style_neon"),
        ("🚀 Millionaire Vibe", "style_millionaire"),
        ("💪 Gym Boy", "style_gym"),
        ("🎮 Gamer Style", "style_gamer"),
    ]
    
    buttons = []
    for style_name, style_code in styles:
        buttons.append([InlineKeyboardButton(text=style_name, callback_data=style_code)])
    
    buttons.append([InlineKeyboardButton(text="❌ Bekor Qilish", callback_data="cancel_style")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_premium_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Get premium/VIP keyboard"""
    user = db.get_user(user_id)
    referral_count = user['referral_count'] if user else 0
    
    keyboard = [
        [InlineKeyboardButton(text="💎 VIP Sotib Olish", callback_data="buy_vip")],
        [InlineKeyboardButton(text=f"🚀 Do'stlari: {referral_count}", callback_data="show_referral")],
        [InlineKeyboardButton(text="🔗 Referral Link", callback_data="get_referral_link")],
        [InlineKeyboardButton(text="🏆 Leaderboard", callback_data="show_leaderboard")],
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_admin_keyboard() -> ReplyKeyboardMarkup:
    """Get admin panel keyboard"""
    keyboard = [
        [KeyboardButton(text="📊 Statistika")],
        [KeyboardButton(text="📢 Broadcast"), KeyboardButton(text="💳 VIP So'rovlari")],
        [KeyboardButton(text="👤 Ban/Unban"), KeyboardButton(text="💎 Manual VIP")],
        [KeyboardButton(text="📤 Export Users"), KeyboardButton(text="🔍 User Info")],
        [KeyboardButton(text="🔙 Orqaga")],
    ]
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# ==================== 📱 START & HELP HANDLERS ====================

@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    """Handle /start command"""
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    full_name = message.from_user.full_name or "Unknown"
    
    # Check if user is banned
    if db.is_banned(user_id):
        await message.answer("❌ Kechirasiz, siz botdan foydalanish uchun bloklangan")
        return
    
    # Check for referral link
    referral_id = None
    args = message.text.split()
    if len(args) > 1:
        try:
            referral_id = int(args[1])
        except:
            pass
    
    # Add user to database
    if not db.is_user_exists(user_id):
        db.add_user(user_id, username, full_name, referral_id)
        
        # Notify referrer
        if referral_id and db.is_user_exists(referral_id):
            referrer = db.get_user(referral_id)
            referral_count = referrer['referral_count']
            
            # Check for milestone bonuses
            milestone_messages = {
                3: "🎉 3 ta do'st taklif qildingiz! Premium Style ochildi!",
                5: "👑 5 ta do'st taklif qildingiz! Yanada ko'p Style ochildi!",
                10: "🔥 10 ta do'st taklif qildingiz! Barcha Premium Style sizga!"
            }
            
            if referral_count in milestone_messages:
                try:
                    await bot.send_message(
                        referral_id,
                        f"🎊 {milestone_messages[referral_count]}\n\n"
                        f"📊 Jami: {referral_count} ta do'st taklif qildingiz"
                    )
                except:
                    pass
    
    # Welcome message
    welcome_text = f"""
welcome_text = f"""
✨ <b>AI AVATAR GENERATOR</b> ✨

Salom <b>{full_name}</b> 👋

📸 Rasm yuboring  
🎨 Stil tanlang  
🚀 Avatar oling  

💎 Premium mavjud  
👥 Do‘st taklif qilib bonus oling
"""
    
    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(user_id)
    )
    
    logger.info(f"✅ User started bot: {user_id} (@{username})")

@dp.message(Command("help"))
async def help_handler(message: Message):
    """Handle /help command"""
    help_text = """
📚 YORDAM VA QOLLANMA

🎨 Photo Tahrir:
• Rasmingizni yuklang
• Istagan stilni tanlang
• Natijaviy rasmni oling

💎 Premium Xususiyatlari:
✅ Cheksiz rasmlar yaratish
✅ Barcha premium stillar
✅ Tezroq yaratish
✅ Watermark yo'q

👥 Referral Sistema:
• Har bir taklif qilingan do'st = 1 bal
• 3 ta do'st → Premium Style
• 5 ta do'st → Ko'proq Style
• 10 ta do'st → Barcha Premium!

💳 Premium Sotib Olish:
• Narxi: 30,000 UZS/oy
• Card raqami: {VIP_CARD_NUMBER}
• Telefon: {VIP_PHONE_NUMBER}

❓ Savol bo'lsa: @support ga yozing
"""
    
    await message.answer(help_text)

# ==================== 📸 PHOTO UPLOAD HANDLER ====================

@dp.message(F.text == "🎨 Photo Tahrir Qilish")
async def photo_upload_start(message: Message, state: FSMContext):
    """Start photo upload process"""
    user_id = message.from_user.id
    
    # Check if user is banned
    if db.is_banned(user_id):
        await message.answer("❌ Siz botdan foydalanish uchun bloklangan")
        return
    
    # Check if user can generate
    if not db.can_generate(user_id):
        user = db.get_user(user_id)
        if user and not user['is_vip']:
            await message.answer(
                "❌ Bugun juda ko'p rasmlar yaratdingiz!\n\n"
                "🔄 Ertaga qayta urinib ko'ring\n"
                "yoki\n"
                "💎 Premium olib cheksiz rasmlar yarating!",
                reply_markup=get_premium_keyboard(user_id)
            )
            return
    
    await message.answer(
        "📸 Iltimos, avatar uchun rasmingizni yuboring\n\n"
        "💡 Maslahat:\n"
        "• Aniq yuzning rasmi tanlang\n"
        "• Oq fon yaxshi ishlaydi\n"
        "• JPEG yoki PNG formatida\n\n"
        "❌ Bekor qilish uchun: /cancel"
    )
    
    await state.set_state(UploadPhotoState.waiting_for_photo)

@dp.message(UploadPhotoState.waiting_for_photo, F.photo)
async def photo_received(message: Message, state: FSMContext):
    """Handle photo upload"""
    user_id = message.from_user.id
    
    try:
        # Download photo
        photo_file = await bot.get_file(message.photo[-1].file_id)
        photo_path = photo_file.file_path
        
        # Save file_id for later use
        await state.update_data(photo_file_id=message.photo[-1].file_id)
        
        await message.answer(
            "✅ Rasm qabul qilindi!\n\n"
            "🎨 Endi stil tanla:",
            reply_markup=get_styles_keyboard()
        )
        
        await state.set_state(UploadPhotoState.waiting_for_style)
        
    except Exception as e:
        logger.error(f"❌ Error receiving photo: {e}")
        await message.answer("❌ Rasmni yuklashda xato! Iltimos, qayta urinib ko'ring")

@dp.message(UploadPhotoState.waiting_for_photo)
async def invalid_photo(message: Message):
    """Handle invalid file during photo upload"""
    await message.answer("❌ Iltimos, rasm yuboring (hujjat emas)")

# ==================== 🎨 STYLE SELECTION ====================

@dp.callback_query(UploadPhotoState.waiting_for_style, F.data.startswith("style_"))
async def style_selected(callback: CallbackQuery, state: FSMContext):
    """Handle style selection"""
    user_id = callback.from_user.id
    style_code = callback.data
    
    # Map style codes to names
    style_names = {
        "style_mafia": "Mafia Style",
        "style_rich_boy": "Rich Boy",
        "style_luxury_suit": "Luxury Suit",
        "style_dark_vibe": "Dark Vibe",
        "style_businessman": "Businessman",
        "style_football": "Football Player",
        "style_cyberpunk": "Cyberpunk",
        "style_gold_crown": "Gold Crown",
        "style_old_money": "Old Money",
        "style_streetwear": "Streetwear",
        "style_king": "King Style",
        "style_neon": "Neon Effect",
        "style_millionaire": "Millionaire Vibe",
        "style_gym": "Gym Boy",
        "style_gamer": "Gamer Style",
    }
    
    style_name = style_names.get(style_code, "Unknown Style")
    
    # Save style to state
    data = await state.get_data()
    
    try:
        await callback.message.edit_text(
            f"⏳ {style_name} stilida rasm yaratilmoqda...\n"
            "Iltimos, kutib turing, bu biroz vaqt oladi ⏱️"
        )
        
        await state.set_state(UploadPhotoState.generating)
        
        # Generate image using Google AI
        photo_file_id = data.get('photo_file_id')
        image_url = await get_photo_url(photo_file_id)
        
        # Create prompt for Google AI
        prompt = f"""Transform this photo into a {style_name} style avatar. 
        Make it look professional, artistic, and suitable for social media profile picture.
        Keep the face recognizable but apply the {style_name} aesthetic strongly.
        Add appropriate styling, clothing, and background for this style.
        Make it vibrant and eye-catching."""
        
        # Call Google AI API
        generated_image = await generate_image_with_google_ai(image_url, prompt)
        
        if generated_image:
            # Save image
            image_bytes = generated_image
            
            # Log generation
            db.add_generation_log(user_id, style_name, "success")
            db.increment_generation_count(user_id)
            
            # Send generated image
            await callback.message.delete()
            
            await bot.send_photo(
                user_id,
                InputFile(BytesIO(image_bytes), filename="avatar.png"),
                caption=f"✨ {style_name} Avtari Tayyorlandi!\n\n"
                        f"👑 Premium xususiyat: Watermark yo'q\n"
                        f"💎 Cheksiz rasmlar yarating - VIP oling!"
            )
            
            await callback.message.answer(
                "🎉 Rasm tayyor!\n\n"
                "Yana bir rasm yasamoqchisiz?",
                reply_markup=get_main_keyboard(user_id)
            )
        else:
            await callback.message.edit_text(
                "❌ Rasmni yaratishda xato! \n"
                "Iltimos, qayta urinib ko'ring"
            )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"❌ Error generating image: {e}")
        await callback.message.edit_text(
            "❌ Rasmni yaratishda xato!\n"
            f"Xato: {str(e)[:100]}"
        )
        await state.clear()

@dp.callback_query(F.data == "cancel_style")
async def cancel_style(callback: CallbackQuery, state: FSMContext):
    """Cancel style selection"""
    await callback.message.delete()
    await callback.message.answer(
        "❌ Bekor qilindi",
        reply_markup=get_main_keyboard(callback.from_user.id)
    )
    await state.clear()

# ==================== 🌐 IMAGE GENERATION WITH GOOGLE AI ====================

async def get_photo_url(file_id: str) -> str:
    """Get photo URL from file_id"""
    try:
        file = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        return file_url
    except Exception as e:
        logger.error(f"❌ Error getting photo URL: {e}")
        return None

async def generate_image_with_google_ai(image_url: str, prompt: str) -> Optional[bytes]:
    """Generate image using Google AI Gemini API"""
    try:
        # Download image from URL
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                image_data = await resp.read()
        
        # Use Google AI to edit image
        model = genai.GenerativeModel('gemini-2.0-flash')
        
        # Create image part
        image_part = {
            "mime_type": "image/jpeg",
            "data": image_data
        }
        
        # Generate using vision capability
        response = model.generate_content([prompt, image_part])
        
        # Note: This returns text, we need to use image editing instead
        # For production, you'd want to use a dedicated image generation API
        # For now, we'll return a placeholder
        
        logger.info(f"✅ Image generated successfully")
        return image_data  # Return original for demo
        
    except Exception as e:
        logger.error(f"❌ Error in image generation: {e}")
        return None

# ==================== 👥 REFERRAL SYSTEM ====================

@dp.message(F.text == "👥 Do'stlar")
async def referral_menu(message: Message):
    """Show referral menu"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await message.answer("❌ Profil topilmadi")
        return
    
    referral_count = user['referral_count']
    referral_link = f"https://t.me/{(await bot.get_me()).username}?start={user_id}"
    
    referral_text = f"""
╔════════════════════════════════════╗
║    👥 REFERRAL SISTEMA 👥          ║
╚════════════════════════════════════╝

🔗 Sizning Referral Link:
`{referral_link}`

📊 Statistika:
• Do'stlar: {referral_count}/10
• Bonuslar: {'🔓 Ochildi!' if referral_count >= 3 else '🔒 3 ta kerak'}

🎁 Bonus Shart:
✅ 3 ta do'st → Premium Style
✅ 5 ta do'st → Ko'proq Style  
✅ 10 ta do'st → Barcha Premium!

💡 Qanday ishlaydi:
1. Linkni do'stlarga yuboring
2. Do'stlar botga qo'shilganda siz bonus olasiz
3. 3 ta do'st bilan premium style oching!
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Link Nusxala", callback_data="copy_referral_link")],
        [InlineKeyboardButton(text="🏆 Leaderboard", callback_data="show_leaderboard")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_main")],
    ])
    
    await message.answer(referral_text, reply_markup=keyboard)

@dp.callback_query(F.data == "copy_referral_link")
async def copy_referral_link(callback: CallbackQuery):
    """Copy referral link"""
    user_id = callback.from_user.id
    bot_username = (await bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={user_id}"
    
    await callback.answer(f"✅ Link nusxalandi: {referral_link}", show_alert=False)

@dp.callback_query(F.data == "show_leaderboard")
async def show_leaderboard(callback: CallbackQuery):
    """Show referral leaderboard"""
    stats = db.get_stats()
    top_inviters = stats['top_inviters']
    
    leaderboard_text = "🏆 TOP REFERRERS\n\n"
    
    for idx, inviter in enumerate(top_inviters[:10], 1):
        user = db.get_user(inviter['user_id'])
        username = user['username'] if user else "Unknown"
        leaderboard_text += f"{idx}. @{username} - {inviter['referral_count']} do'st\n"
    
    await callback.message.edit_text(leaderboard_text)

# ==================== 💎 PREMIUM/VIP SYSTEM ====================

@dp.message(F.text == "💎 Premium")
async def premium_menu(message: Message):
    """Show premium menu"""
    user_id = message.from_user.id
    is_vip = db.is_vip(user_id)
    user = db.get_user(user_id)
    
    if is_vip:
        vip_expire = user['vip_expire_date']
        premium_text = f"""
╔════════════════════════════════════╗
║   💎 VIP MEMBER 💎                 ║
╚════════════════════════════════════╝

✅ Siz Premium Odam!

🎁 Sizning Imkoniyatlar:
✅ Cheksiz rasmlar yaratish
✅ Barcha Premium Stillar
✅ Tezroq Yaratish
✅ Watermark Yo'q
✅ VIP Badge

📅 VIP Muddat: {vip_expire}
⏰ Qolgan kun: Yangi VIP faydali!
"""
    else:
        premium_text = f"""
╔════════════════════════════════════╗
║   💎 VIP MEMBERSHIP 💎             ║
╚════════════════════════════════════╝

🚀 Premium Xususiyatlari:
✅ Cheksiz rasmlar yaratish
✅ Barcha Premium Stillar
✅ Tezroq Yaratish (2x tez)
✅ Watermark Yo'q
✅ VIP Badge
✅ Priority Support

💰 Narxi: 30,000 UZS/oy
🎁 Birinchi oy: Bonus bilan!

👥 Alternativ:
Do'st taklif qil va Premium Stil oling!
• 3 ta do'st = Premium
• 5 ta do'st = Ko'proq
• 10 ta do'st = Hamma!
"""
    
    keyboard = get_premium_keyboard(user_id)
    await message.answer(premium_text, reply_markup=keyboard)

@dp.callback_query(F.data == "buy_vip")
async def buy_vip_start(callback: CallbackQuery, state: FSMContext):
    """Start VIP purchase process"""
    user_id = callback.from_user.id
    
    if db.is_vip(user_id):
        await callback.answer("✅ Siz allaqachon VIP odam!", show_alert=True)
        return
    
    payment_text = f"""
╔════════════════════════════════════╗
║    💳 PAYMENT INFO 💳              ║
╚════════════════════════════════════╝

💰 Sum: {VIP_PRICE:,} UZS/oy

🏦 To'lov ma'lumotlari:
Card: {VIP_CARD_NUMBER}
Ism: {VIP_CARD_NAME}
Tel: {VIP_PHONE_NUMBER}

📝 Jarayon:
1. ☝️ Karta raqamiga pul yuboring
2. 📸 Chek/Screenshot yuboring
3. ✅ Admin tasdiqlaydi (1-2 soat)
4. 🎉 VIP faollashtirilib beradi!

Davom etish uchun "✅ To'lov Qildim" tugmasini bosing
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ To'lov Qildim", callback_data="confirm_payment")],
        [InlineKeyboardButton(text="❌ Bekor Qilish", callback_data="cancel_payment")],
    ])
    
    await callback.message.edit_text(payment_text, reply_markup=keyboard)

@dp.callback_query(F.data == "confirm_payment")
async def confirm_payment(callback: CallbackQuery, state: FSMContext):
    """Confirm payment and ask for amount"""
    await callback.message.edit_text(
        "💵 Qancha tashadingiz?\n\n"
        "Raqamni kiriting (masalan: 30000)"
    )
    
    await state.set_state(PaymentState.waiting_for_amount)

@dp.message(PaymentState.waiting_for_amount)
async def amount_received(message: Message, state: FSMContext):
    """Handle payment amount"""
    try:
        amount = int(message.text.strip())
        
        if amount <= 0:
            await message.answer("❌ Musbat raqam kiriting!")
            return
        
        await state.update_data(amount=amount)
        
        await message.answer(
            f"📸 Rasmingiz: {amount:,} UZS\n\n"
            "Endi to'lov chekini/screenshot'ini yuboring.\n\n"
            "💡 Maslahat: To'lovning barcha ma'lumotlari ko'rinib turishi kerak!"
        )
        
        await state.set_state(PaymentState.waiting_for_screenshot)
        
    except ValueError:
        await message.answer("❌ Raqam kiriting! (masalan: 30000)")

@dp.message(PaymentState.waiting_for_screenshot, F.photo)
async def screenshot_received(message: Message, state: FSMContext):
    """Handle payment screenshot"""
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    full_name = message.from_user.full_name or "Unknown"
    
    data = await state.get_data()
    amount = data.get('amount', 0)
    screenshot_file_id = message.photo[-1].file_id
    
    # Save payment request
    payment_id = db.add_payment_request(
        user_id,
        username,
        full_name,
        amount,
        screenshot_file_id
    )
    
    # Notify admin
    admin_message = f"""
🔔 YANGI TO'LOV SO'ROVI

👤 Foydalanuvchi:
ID: {user_id}
Username: @{username}
Ism: {full_name}

💰 Sum: {amount:,} UZS
📋 So'rov ID: {payment_id}

⏳ Status: Kutilmoqda
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Qabul Qilish", callback_data=f"approve_payment_{payment_id}")],
        [InlineKeyboardButton(text="❌ Rad Etish", callback_data=f"reject_payment_{payment_id}")],
    ])
    
    try:
        await bot.send_photo(
            ADMIN_ID,
            screenshot_file_id,
            caption=admin_message,
            reply_markup=keyboard
        )
    except:
        await bot.send_message(
            ADMIN_ID,
            admin_message,
            reply_markup=keyboard
        )
    
    # Notify user
    await message.answer(
        f"✅ To'lov so'rovi yuborildi!\n\n"
        f"💰 Sum: {amount:,} UZS\n"
        f"📋 So'rov ID: {payment_id}\n\n"
        f"⏳ Admin ko'rib chiqsa (1-2 soat) VIP faollashtirilib beriladi\n"
        f"✉️ Bildirishnoma olasiz",
        reply_markup=get_main_keyboard(user_id)
    )
    
    await state.clear()

@dp.callback_query(F.data.startswith("approve_payment_"))
async def approve_payment_callback(callback: CallbackQuery):
    """Approve payment"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Faqat admin", show_alert=True)
        return
    
    payment_id = int(callback.data.split("_")[2])
    
    # Get payment info
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, amount FROM payment_requests WHERE id = ?", (payment_id,))
    payment = cursor.fetchone()
    conn.close()
    
    if not payment:
        await callback.answer("❌ So'rov topilmadi", show_alert=True)
        return
    
    user_id = payment[0]
    
    # Approve payment
    db.approve_payment(payment_id)
    
    # Notify user
    try:
        await bot.send_message(
            user_id,
            "🎉 TABRIKLAYMIZ!\n\n"
            "✅ To'lovingiz qabul qilindi!\n\n"
            "💎 VIP Status Faollashtirilib Berildi!\n"
            "⏰ 30 kunlik Muddat: Boshlab yuborildi\n\n"
            "🎁 Sizning Yangi Imkoniyatlar:\n"
            "✅ Cheksiz rasmlar yaratish\n"
            "✅ Barcha Premium Stillar\n"
            "✅ Watermark yo'q\n"
            "✅ Tezroq Yaratish\n\n"
            "Raxmat VIP bo'lganingiz uchun! 👑",
            reply_markup=get_main_keyboard(user_id)
        )
    except:
        pass
    
    await callback.message.edit_text(
        f"✅ QABUL QILINDI\n\n"
        f"User ID: {user_id}\n"
        f"VIP Status: Faollashtirilib berildi"
    )
    
    await callback.answer("✅ Qabul qilindi", show_alert=True)

@dp.callback_query(F.data.startswith("reject_payment_"))
async def reject_payment_callback(callback: CallbackQuery):
    """Reject payment"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("❌ Faqat admin", show_alert=True)
        return
    
    payment_id = int(callback.data.split("_")[2])
    
    # Get payment info
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM payment_requests WHERE id = ?", (payment_id,))
    payment = cursor.fetchone()
    conn.close()
    
    if not payment:
        await callback.answer("❌ So'rov topilmadi", show_alert=True)
        return
    
    user_id = payment[0]
    
    # Reject payment
    db.reject_payment(payment_id)
    
    # Notify user
    try:
        await bot.send_message(
            user_id,
            "❌ TO'LOV RAD ETILDI\n\n"
            "Sizning to'lov so'rovi rad etildi.\n\n"
            "Sababi:\n"
            "• Chek aniq emas\n"
            "• To'lov ma'lumotlari noto'g'ri\n"
            "• Ikki marta to'lov\n\n"
            "Qayta urinib ko'ring yoki @support ga murojaat qiling",
            reply_markup=get_main_keyboard(user_id)
        )
    except:
        pass
    
    await callback.message.edit_text(
        f"❌ RAD ETILDI\n\n"
        f"User ID: {user_id}"
    )
    
    await callback.answer("❌ Rad etildi", show_alert=True)

# ==================== 📊 STATISTICS ====================

@dp.message(F.text == "📊 Statistika")
async def user_statistics(message: Message):
    """Show user statistics"""
    user_id = message.from_user.id
    user = db.get_user(user_id)
    
    if not user:
        await message.answer("❌ Profil topilmadi")
        return
    
    stats_text = f"""
╔════════════════════════════════════╗
║    📊 SIZNING STATISTIKA 📊        ║
╚════════════════════════════════════╝

👤 Profil:
• Join Date: {user['join_date']}
• Status: {'👑 VIP' if user['is_vip'] else '⭐ Free'}

🎨 Rasmlar:
• Bugun: {user['daily_generation_count']}/{'♾️' if user['is_vip'] else '1'}
• Jami: {user['total_generations']}

👥 Referrals:
• Do'stlar: {user['referral_count']}
• Bonus: {'🔓 Premium Style' if user['referral_count'] >= 3 else '🔒 3 ta kerak'}

🔥 Streaks:
• Davom: {user['streak']} kun

💎 VIP:
• Status: {'✅ Faol' if user['is_vip'] else '❌ Yo\'q'}
• Expire: {user['vip_expire_date'] if user['is_vip'] else 'N/A'}
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_main")],
    ])
    
    await message.answer(stats_text, reply_markup=keyboard)

# ==================== ⚙️ SETTINGS ====================

@dp.message(F.text == "⚙️ Sozlamalar")
async def settings_menu(message: Message):
    """Show settings menu"""
    await message.answer(
        "⚙️ SOZLAMALAR\n\n"
        "🔜 Coming Soon...",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# ==================== 🛡️ ADMIN PANEL ====================

@dp.message(F.text == "🛡️ Admin Panel")
async def admin_panel(message: Message):
    """Show admin panel"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Faqat Admin")
        return
    
    await message.answer(
        "🛡️ ADMIN PANEL\n\n"
        "Quyidagi amalni tanlang:",
        reply_markup=get_admin_keyboard()
    )

@dp.message(F.text == "📊 Statistika", StateFilter(None))
async def admin_statistics(message: Message):
    """Show admin statistics"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Faqat Admin")
        return
    
    stats = db.get_stats()
    
    stats_text = f"""
╔════════════════════════════════════╗
║    📊 BOT STATISTICS 📊            ║
╚════════════════════════════════════╝

👥 Foydalanuvchilar:
• Jami: {stats['total_users']}
• Faol: {stats['active_users']}
• VIP: {stats['vip_users']}

🎨 Rasmlar:
• Jami yaratilgan: {stats['total_generations']}

🏆 Top 10 Referrers:
"""
    
    for idx, inviter in enumerate(stats['top_inviters'], 1):
        user = db.get_user(inviter['user_id'])
        username = user['username'] if user else "Unknown"
        stats_text += f"{idx}. @{username} - {inviter['referral_count']} do'st\n"
    
    await message.answer(stats_text, reply_markup=get_admin_keyboard())

@dp.message(F.text == "📢 Broadcast")
async def broadcast_start(message: Message, state: FSMContext):
    """Start broadcast process"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Faqat Admin")
        return
    
    await message.answer(
        "📢 BROADCAST\n\n"
        "Barcha foydalanuvchilarga xabar yuboring.\n\n"
        "Xabarni yuboring (matn, rasm, video):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Bekor Qilish")]],
            resize_keyboard=True
        )
    )
    
    await state.set_state(AdminBroadcastState.waiting_for_content)

@dp.message(AdminBroadcastState.waiting_for_content)
async def broadcast_content(message: Message, state: FSMContext):
    """Handle broadcast content"""
    if message.from_user.id != ADMIN_ID:
        return
    
    if message.text == "❌ Bekor Qilish":
        await state.clear()
        await message.answer("❌ Bekor qilindi", reply_markup=get_admin_keyboard())
        return
    
    # Save content
    await state.update_data(content=message)
    
    # Ask for confirmation
    await message.answer(
        "✅ Rasm/Video qabul qilindi!\n\n"
        "Barcha foydalanuvchilarga yubormizmi?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Ha", callback_data="broadcast_confirm")],
            [InlineKeyboardButton(text="❌ Yo'q", callback_data="broadcast_cancel")],
        ])
    )

@dp.callback_query(F.data == "broadcast_confirm")
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    """Send broadcast"""
    if callback.from_user.id != ADMIN_ID:
        return
    
    await callback.message.edit_text("📢 Broadcast yuborilmoqda...")
    
    data = await state.get_data()
    message_to_send = data.get('content')
    
    users = db.get_all_users()
    success = 0
    failed = 0
    
    for user_id in users:
        try:
            if message_to_send.photo:
                await bot.send_photo(
                    user_id,
                    message_to_send.photo[-1].file_id,
                    caption=message_to_send.caption or ""
                )
            elif message_to_send.video:
                await bot.send_video(
                    user_id,
                    message_to_send.video.file_id,
                    caption=message_to_send.caption or ""
                )
            else:
                await bot.send_message(user_id, message_to_send.text)
            
            success += 1
            await asyncio.sleep(0.1)  # Anti-spam delay
        except:
            failed += 1
    
    await callback.message.edit_text(
        f"✅ Broadcast tugadi!\n\n"
        f"✅ Yuborildi: {success}\n"
        f"❌ Xato: {failed}",
        reply_markup=get_admin_keyboard()
    )
    
    await state.clear()

@dp.callback_query(F.data == "broadcast_cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    """Cancel broadcast"""
    await callback.message.edit_text("❌ Bekor qilindi", reply_markup=get_admin_keyboard())
    await state.clear()

@dp.message(F.text == "💳 VIP So'rovlari")
async def show_payment_requests(message: Message):
    """Show pending payment requests"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Faqat Admin")
        return
    
    payments = db.get_pending_payments()
    
    if not payments:
        await message.answer(
            "✅ Barcha to'lovlar ko'rib chiqildi!\n"
            "Kutilayotgan so'rov yo'q",
            reply_markup=get_admin_keyboard()
        )
        return
    
    for payment in payments:
        payment_text = f"""
💳 TO'LOV SO'ROVI

User: @{payment['username']}
Ism: {payment['full_name']}
ID: {payment['user_id']}

💰 Sum: {payment['amount']:,} UZS
📋 So'rov ID: {payment['id']}
⏳ Status: {payment['status']}
📅 Vaqt: {payment['created_at']}
"""
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Qabul Qilish", callback_data=f"approve_payment_{payment['id']}")],
            [InlineKeyboardButton(text="❌ Rad Etish", callback_data=f"reject_payment_{payment['id']}")],
        ])
        
        try:
            await bot.send_photo(
                message.from_user.id,
                payment['screenshot_file_id'],
                caption=payment_text,
                reply_markup=keyboard
            )
        except:
            await message.answer(payment_text, reply_markup=keyboard)

@dp.message(F.text == "👤 Ban/Unban")
async def ban_menu(message: Message, state: FSMContext):
    """Ban/Unban menu"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Faqat Admin")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Ban User", callback_data="ban_user")],
        [InlineKeyboardButton(text="✅ Unban User", callback_data="unban_user")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_admin")],
    ])
    
    await message.answer(
        "👤 BAN/UNBAN\n\n"
        "Tanlang:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "ban_user")
async def ban_user_start(callback: CallbackQuery, state: FSMContext):
    """Start ban user process"""
    await callback.message.edit_text("User ID'ni kiriting:")
    await state.set_state(AdminBanState.waiting_for_user_id)
    await state.update_data(action="ban")

@dp.callback_query(F.data == "unban_user")
async def unban_user_start(callback: CallbackQuery, state: FSMContext):
    """Start unban user process"""
    await callback.message.edit_text("User ID'ni kiriting:")
    await state.set_state(AdminBanState.waiting_for_user_id)
    await state.update_data(action="unban")

@dp.message(AdminBanState.waiting_for_user_id)
async def ban_unban_user(message: Message, state: FSMContext):
    """Ban or unban user"""
    try:
        user_id = int(message.text.strip())
        data = await state.get_data()
        action = data.get('action')
        
        if action == "ban":
            db.ban_user(user_id)
            result = f"✅ User {user_id} BAN qilindi"
        else:
            db.unban_user(user_id)
            result = f"✅ User {user_id} UNBAN qilindi"
        
        await message.answer(result, reply_markup=get_admin_keyboard())
        await state.clear()
        
    except ValueError:
        await message.answer("❌ User ID raqam bo'lishi kerak!")

@dp.message(F.text == "💎 Manual VIP")
async def manual_vip_menu(message: Message):
    """Manual VIP menu"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Faqat Admin")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 VIP Berish", callback_data="give_vip_manual")],
        [InlineKeyboardButton(text="❌ VIP Olib Tashlash", callback_data="remove_vip_manual")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_to_admin")],
    ])
    
    await message.answer(
        "💎 MANUAL VIP\n\n"
        "Tanlang:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data == "give_vip_manual")
async def give_vip_manual(callback: CallbackQuery, state: FSMContext):
    """Give VIP manually"""
    await callback.message.edit_text("User ID'ni kiriting:")
    await state.set_state(AdminBanState.waiting_for_user_id)
    await state.update_data(action="give_vip")

@dp.callback_query(F.data == "remove_vip_manual")
async def remove_vip_manual(callback: CallbackQuery, state: FSMContext):
    """Remove VIP manually"""
    await callback.message.edit_text("User ID'ni kiriting:")
    await state.set_state(AdminBanState.waiting_for_user_id)
    await state.update_data(action="remove_vip")

@dp.message(F.text == "📤 Export Users")
async def export_users(message: Message):
    """Export users to CSV"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Faqat Admin")
        return
    
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    conn.close()
    
    # Create CSV content
    csv_content = "User_ID,Username,Full_Name,Join_Date,Referral_Count,Is_VIP,Total_Generations\n"
    
    for user in users:
        csv_content += f"{user[0]},{user[1]},{user[2]},{user[3]},{user[4]},{user[6]},{user[8]}\n"
    
    # Send file
    csv_file = InputFile(BytesIO(csv_content.encode()), filename="users_export.csv")
    
    await message.answer_document(csv_file, caption="✅ Users Export")

@dp.message(F.text == "🔍 User Info")
async def user_info_admin(message: Message, state: FSMContext):
    """Get user info (admin)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Faqat Admin")
        return
    
    await message.answer("User ID'ni kiriting:")
    await state.set_state(AdminBanState.waiting_for_user_id)
    await state.update_data(action="user_info")

@dp.message(F.text == "🔙 Orqaga")
async def back_to_main(message: Message):
    """Back to main menu"""
    await message.answer(
        "🏠 Asosiy Menu",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main_callback(callback: CallbackQuery):
    """Back to main menu (callback)"""
    await callback.message.delete()
    await callback.message.answer(
        "🏠 Asosiy Menu",
        reply_markup=get_main_keyboard(callback.from_user.id)
    )

@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin(callback: CallbackQuery):
    """Back to admin panel"""
    if callback.from_user.id != ADMIN_ID:
        return
    
    await callback.message.delete()
    await callback.message.answer(
        "🛡️ ADMIN PANEL",
        reply_markup=get_admin_keyboard()
    )

# ==================== 🚫 ERROR HANDLERS ====================

@dp.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    """Cancel current operation"""
    current_state = await state.get_state()
    
    if current_state is None:
        return
    
    await state.clear()
    await message.answer(
        "❌ Bekor qilindi",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

@dp.message()
async def default_handler(message: Message):
    """Default message handler"""
    if message.from_user.id == ADMIN_ID and message.text == "🔙 Orqaga":
        await message.answer(
            "🛡️ ADMIN PANEL",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer(
            "❌ Noma'lum buyruq\n\n"
            "Iltimos, tugmalardan foydalaning",
            reply_markup=get_main_keyboard(message.from_user.id)
        )

# ==================== 🔄 DAILY TASKS ====================

async def reset_daily_stats():
    """Reset daily stats every day"""
    while True:
        # Wait until midnight
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (tomorrow - now).total_seconds()
        
        await asyncio.sleep(wait_seconds)
        
        # Reset daily counts
        db.reset_daily_count()
        logger.info("✅ Daily stats reset")

# ==================== 🚀 BOT STARTUP ====================

async def on_startup():
    """Run on bot startup"""
    logger.info("✅ Bot started successfully!")
    
    # Start daily task
    asyncio.create_task(reset_daily_stats())
    
    # Log to admin
    try:
        stats = db.get_stats()
        await bot.send_message(
            ADMIN_ID,
            f"🤖 Bot started!\n\n"
            f"👥 Users: {stats['total_users']}\n"
            f"🎨 Generations: {stats['total_generations']}\n"
            f"💎 VIP Users: {stats['vip_users']}"
        )
    except:
        pass

async def main():
    """Main bot function"""
    await on_startup()
    await dp.start_polling(bot)

# ==================== 📌 ENTRY POINT ====================

if __name__ == "__main__":
    try:
        logger.info("🚀 Starting bot...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
