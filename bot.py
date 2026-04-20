import nest_asyncio
nest_asyncio.apply()

import asyncio
import aiosqlite
import aiohttp
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat, BotCommand
from telegram.error import Forbidden
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ==================== الإعدادات ====================
import os
BOT_TOKEN = os.getenv("TOKENbotss")
ADMIN_ID       = 7726643439
DB_NAME        = "series.db"
OPENAI_API_KEY = "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"

# ==================== حالات المحادثة ====================
(
    WAITING_SEASON_NUMBER,
    WAITING_EP_SEASON,
    WAITING_EP_NUMBER,
    WAITING_EP_VIDEO,
    WAITING_EP_DESC,
    WAITING_DELETE_EP,
    WAITING_DELETE_SEASON,
    WAITING_EDIT_SEASON_NAME,
    WAITING_EDIT_EP_NAME,
    WAITING_ADD_CHANNEL,
    WAITING_AI_QUESTION,
    WAITING_SHORTCUT_NAME,
    WAITING_SHORTCUT_CONTENT,
) = range(13)

EMOTIONAL_WORDS = ["أحبك","احبك","بحبك","حبيبي","عشقتك","i love you","love you","حبك","هواك"]
DEVELOPER_WORDS = ["المطور","مطور","المبرمج","مبرمج","المالك","مالك","developer","owner","من صنعك","من طورك"]

SYSTEM_PROMPT = """أنت مساعد ذكي متخصص حصرياً في مسلسل The Last Kingdom.
تجيب فقط على أسئلة تتعلق بهذا المسلسل: الحلقات، المواسم، القصة، الشخصيات، الممثلين، منصات المشاهدة، التقييمات.
أسلوبك: قصير، مرتب، احترافي، ودود. لا تكتب ردوداً طويلة.
إذا كان السؤال خارج نطاق المسلسل: رد بـ"عذراً، أستطيع المساعدة فقط بخصوص مسلسل The Last Kingdom. 🏰"
اللغة: رد بنفس لغة المستخدم (عربي أو إنجليزي)."""

# ==================== قاعدة البيانات ====================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_number INTEGER UNIQUE, season_name TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id INTEGER, episode_number INTEGER,
            episode_name TEXT, episode_video TEXT,
            episode_desc TEXT DEFAULT '',
            view_count INTEGER DEFAULT 0,
            rating_sum INTEGER DEFAULT 0,
            rating_count INTEGER DEFAULT 0,
            FOREIGN KEY (season_id) REFERENCES seasons(id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS episode_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            episode_id INTEGER, user_id INTEGER, rating INTEGER,
            UNIQUE(episode_id, user_id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE, channel_name TEXT, channel_link TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE, username TEXT,
            first_name TEXT, joined_at TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS group_admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER, user_id INTEGER,
            UNIQUE(group_id, user_id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS shortcuts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER, trigger TEXT,
            content_type TEXT, content TEXT, file_id TEXT,
            UNIQUE(group_id, trigger))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_key TEXT UNIQUE, stat_value INTEGER DEFAULT 0)""")
        await db.execute("INSERT OR IGNORE INTO stats (stat_key, stat_value) VALUES ('total_messages', 0)")
        # أعمدة إضافية للجداول القديمة
        for col_def in [
            "ALTER TABLE episodes ADD COLUMN view_count INTEGER DEFAULT 0",
            "ALTER TABLE episodes ADD COLUMN rating_sum INTEGER DEFAULT 0",
            "ALTER TABLE episodes ADD COLUMN rating_count INTEGER DEFAULT 0",
            "ALTER TABLE episodes ADD COLUMN episode_desc TEXT DEFAULT ''",
        ]:
            try:
                await db.execute(col_def)
            except: pass
        await db.commit()

# ==================== دوال مساعدة ====================
def is_admin(user_id): return user_id == ADMIN_ID

async def get_seasons():
    async with aiosqlite.connect(DB_NAME) as db:
        c = await db.execute("SELECT * FROM seasons ORDER BY season_number")
        return await c.fetchall()

async def get_episodes(season_id):
    async with aiosqlite.connect(DB_NAME) as db:
        c = await db.execute("SELECT * FROM episodes WHERE season_id=? ORDER BY episode_number", (season_id,))
        return await c.fetchall()

async def get_channels():
    async with aiosqlite.connect(DB_NAME) as db:
        c = await db.execute("SELECT * FROM channels")
        return await c.fetchall()

async def get_episode_by_id(ep_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        c = await db.execute("""
            SELECT e.*, s.season_number, s.season_name
            FROM episodes e JOIN seasons s ON e.season_id=s.id WHERE e.id=?""", (ep_id,))
        return await c.fetchone()

async def increment_view(ep_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE episodes SET view_count=view_count+1 WHERE id=?", (ep_id,))
        await db.commit()

async def increment_messages():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE stats SET stat_value=stat_value+1 WHERE stat_key='total_messages'")
        await db.commit()

async def get_shortcuts_for_group(group_id):
    async with aiosqlite.connect(DB_NAME) as db:
        c = await db.execute("SELECT trigger, content_type FROM shortcuts WHERE group_id=?", (group_id,))
        return await c.fetchall()

# ==================== تسجيل الاختصارات كأوامر تيليغرام ====================
async def sync_shortcuts_to_commands(app, group_id):
    """تسجيل الاختصارات كأوامر رسمية تظهر عند كتابة /"""
    try:
        shortcuts = await get_shortcuts_for_group(group_id)
        # الأوامر الثابتة
        base_commands = [
            BotCommand("start",    "ابدأ البوت"),
            BotCommand("stats",    "الإحصائيات"),
            BotCommand("promote",  "رفع أدمن"),
            BotCommand("demote",   "تنزيل أدمن"),
            BotCommand("shortcut", "إضافة اختصار"),
        ]
        # إضافة الاختصارات
        sc_commands = []
        for sc in shortcuts:
            trigger = sc[0]
            # تيليغرام يقبل فقط أحرف إنجليزية وأرقام وـ للأوامر
            # نحول النص لأمر آمن ونحفظ الاسم الأصلي كـ description
            safe_cmd = "sc_" + str(abs(hash(trigger)))[:8]
            sc_commands.append(BotCommand(safe_cmd, trigger))

        all_commands = base_commands + sc_commands
        await app.bot.set_my_commands(
            all_commands,
            scope={"type": "chat", "chat_id": group_id}
        )
    except Exception as e:
        logger.error(f"sync_shortcuts error: {e}")

async def register_shortcut_command(app, group_id, trigger):
    """إضافة اختصار واحد للأوامر"""
    await sync_shortcuts_to_commands(app, group_id)

# ==================== نظام المستخدمين ====================
async def register_user(user, context):
    async with aiosqlite.connect(DB_NAME) as db:
        c = await db.execute("SELECT id FROM users WHERE user_id=?", (user.id,))
        if not await c.fetchone():
            await db.execute(
                "INSERT INTO users (user_id, username, first_name, joined_at) VALUES (?,?,?,?)",
                (user.id, user.username or "", user.first_name or "",
                 datetime.now().strftime("%Y-%m-%d %H:%M"))
            )
            await db.commit()
            try:
                uname = f"@{user.username}" if user.username else "بدون يوزر"
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"👤 *مستخدم جديد!*\nالاسم: {user.first_name}\n{uname}\nID: `{user.id}`",
                    parse_mode="Markdown"
                )
            except: pass

async def remove_blocked_user(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        await db.commit()

# ==================== الاشتراك الإجباري ====================
async def check_subscription(user_id, context):
    channels = await get_channels()
    if not channels: return True, []
    not_sub = []
    for ch in channels:
        try:
            m = await context.bot.get_chat_member(chat_id=ch[1], user_id=user_id)
            if m.status in ("left","kicked","banned"):
                not_sub.append(ch)
        except: not_sub.append(ch)
    return len(not_sub)==0, not_sub

async def sub_keyboard(not_sub):
    kb = [[InlineKeyboardButton(f"📢 اشترك في {ch[2]}", url=ch[3])] for ch in not_sub]
    kb.append([InlineKeyboardButton("✅ تحققت من الاشتراك", callback_data="check_sub")])
    return InlineKeyboardMarkup(kb)

async def require_sub(update, context):
    user_id = update.effective_user.id
    if is_admin(user_id): return True
    ok, not_sub = await check_subscription(user_id, context)
    if not ok:
        kb = await sub_keyboard(not_sub)
        txt = "⚠️ *يجب الاشتراك في القنوات التالية أولاً:*"
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")
        else:
            await update.effective_message.reply_text(txt, reply_markup=kb, parse_mode="Markdown")
        return False
    return True

# ==================== الذكاء الاصطناعي (مُصلَح) ====================
async def ask_ai(question: str, context_info: str = "") -> str:
    q = question.lower().strip()
    if any(w in q for w in EMOTIONAL_WORDS):
        return "❤️ حبيبي، الحب لعبد الزهرة بس — مطوري العظيم! 😄"
    if any(w in q for w in DEVELOPER_WORDS):
        return "__DEVELOPER__"

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context_info:
        messages.append({"role": "system", "content": f"السياق الحالي: {context_info}"})
    messages.append({"role": "user", "content": question})

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "gpt-3.5-turbo",
                    "messages": messages,
                    "max_tokens": 400,
                    "temperature": 0.7
                },
                timeout=aiohttp.ClientTimeout(total=25)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                elif resp.status == 401:
                    logger.error("OpenAI: مفتاح API غير صالح")
                    return "⚠️ مفتاح AI غير صالح، تواصل مع المطور."
                elif resp.status == 429:
                    return "⏳ تجاوزت حد الطلبات، حاول بعد قليل."
                else:
                    body = await resp.text()
                    logger.error(f"OpenAI {resp.status}: {body[:200]}")
                    return "⚠️ حصل خطأ مؤقت، حاول لاحقاً."
    except asyncio.TimeoutError:
        return "⏳ انتهى وقت الاتصال، حاول مرة ثانية."
    except aiohttp.ClientConnectorError:
        return "⚠️ تعذر الاتصال بالإنترنت، تحقق من الاتصال."
    except Exception as e:
        logger.error(f"AI error: {type(e).__name__}: {e}")
        return "⚠️ حصل خطأ مؤقت، حاول لاحقاً."

# ==================== معلومات المطور (مُصلَحة) ====================
async def send_developer_info(chat_id, context):
    caption = (
        "👨‍💻 *معلومات المطور*\n\n"
        "الاسم: *عبد الزهرة*\n"
        "التواصل: @O_76j"
    )
    try:
        # جلب صورة الأدمن من ايدي الكود مباشرة
        photos = await context.bot.get_user_profile_photos(user_id=ADMIN_ID, limit=1)
        if photos and photos.total_count > 0:
            file_id = photos.photos[0][-1].file_id
            await context.bot.send_photo(
                chat_id=chat_id, photo=file_id,
                caption=caption, parse_mode="Markdown"
            )
            return
    except Exception as e:
        logger.warning(f"get_user_profile_photos failed: {e}")

    # بديل: إرسال نص فقط
    try:
        await context.bot.send_message(
            chat_id=chat_id, text=caption, parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"send_developer_info fallback error: {e}")

# ==================== تقييم الحلقة ====================
async def rate_episode_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ep_id = int(query.data.split("_")[2])
    context.user_data["rating_ep_id"] = ep_id
    keyboard = [
        [
            InlineKeyboardButton("⭐ 1", callback_data=f"do_rate_{ep_id}_1"),
            InlineKeyboardButton("⭐⭐ 2", callback_data=f"do_rate_{ep_id}_2"),
            InlineKeyboardButton("⭐⭐⭐ 3", callback_data=f"do_rate_{ep_id}_3"),
        ],
        [
            InlineKeyboardButton("⭐⭐⭐⭐ 4", callback_data=f"do_rate_{ep_id}_4"),
            InlineKeyboardButton("⭐⭐⭐⭐⭐ 5", callback_data=f"do_rate_{ep_id}_5"),
        ],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"ep_{ep_id}")],
    ]
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))

async def submit_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts   = query.data.split("_")
    ep_id   = int(parts[2])
    rating  = int(parts[3])
    user_id = query.from_user.id

    async with aiosqlite.connect(DB_NAME) as db:
        # تحقق إذا قيّم مسبقاً
        c = await db.execute(
            "SELECT rating FROM episode_ratings WHERE episode_id=? AND user_id=?",
            (ep_id, user_id)
        )
        existing = await c.fetchone()

        if existing:
            old_rating = existing[0]
            # تحديث التقييم القديم
            await db.execute(
                "UPDATE episode_ratings SET rating=? WHERE episode_id=? AND user_id=?",
                (rating, ep_id, user_id)
            )
            await db.execute(
                "UPDATE episodes SET rating_sum=rating_sum-?+? WHERE id=?",
                (old_rating, rating, ep_id)
            )
        else:
            await db.execute(
                "INSERT INTO episode_ratings (episode_id, user_id, rating) VALUES (?,?,?)",
                (ep_id, user_id, rating)
            )
            await db.execute(
                "UPDATE episodes SET rating_sum=rating_sum+?, rating_count=rating_count+1 WHERE id=?",
                (rating, ep_id)
            )
        await db.commit()

        # جلب المتوسط
        c2 = await db.execute(
            "SELECT rating_sum, rating_count FROM episodes WHERE id=?", (ep_id,)
        )
        row = await c2.fetchone()
        avg = round(row[0] / row[1], 1) if row and row[1] > 0 else 0

    stars = "⭐" * rating
    keyboard = [
        [InlineKeyboardButton("🔙 رجوع للحلقة", callback_data=f"ep_{ep_id}")],
    ]
    await query.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"✅ *تم تسجيل تقييمك!*\n\nتقييمك: {stars} ({rating}/5)\n📊 متوسط التقييم: ⭐ {avg}/5",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# ==================== القائمة الرئيسية ====================
async def main_keyboard(is_adm):
    if is_adm:
        keyboard = [
            [InlineKeyboardButton("➕ إضافة موسم",          callback_data="add_season")],
            [InlineKeyboardButton("🎬 إضافة حلقة",           callback_data="add_episode")],
            [InlineKeyboardButton("✏️ تعديل موسم",           callback_data="edit_season")],
            [InlineKeyboardButton("📝 تعديل حلقة",           callback_data="edit_episode_menu")],
            [InlineKeyboardButton("🗑 حذف موسم",             callback_data="delete_season")],
            [InlineKeyboardButton("❌ حذف حلقة",             callback_data="delete_episode")],
            [InlineKeyboardButton("📢 إدارة قنوات الاشتراك",  callback_data="manage_channels")],
            [InlineKeyboardButton("📊 الإحصائيات",            callback_data="show_stats")],
            [InlineKeyboardButton("📺 عرض المسلسل",          callback_data="view_series")],
            [InlineKeyboardButton("🔄 مسح قاعدة البيانات",   callback_data="reset_db")],
        ]
        text = "👑 *لوحة تحكم المطور*\n\nاختر العملية:"
    else:
        keyboard = [[InlineKeyboardButton("📺 مشاهدة المسلسل", callback_data="view_series")]]
        text = "🎬 *أهلاً بك في بوت مسلسل The Last Kingdom!*\n\nاضغط للمشاهدة:"
    return text, keyboard

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update.effective_user, context)
    text, kb = await main_keyboard(is_admin(update.effective_user.id))
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text, kb = await main_keyboard(is_admin(query.from_user.id))
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ok, not_sub = await check_subscription(query.from_user.id, context)
    if ok:
        text, kb = await main_keyboard(is_admin(query.from_user.id))
        await query.edit_message_text("✅ *تم التحقق!*\n\n" + text,
                                      reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        kb = await sub_keyboard(not_sub)
        await query.edit_message_text("❌ *لم تشترك في جميع القنوات بعد!*",
                                      reply_markup=kb, parse_mode="Markdown")

# ==================== الإحصائيات ====================
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id if query else update.effective_user.id
    if query: await query.answer()
    if not is_admin(user_id):
        if query: await query.answer("⛔ ليس لديك صلاحية!", show_alert=True)
        return
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        total_users   = (await (await db.execute("SELECT COUNT(*) as c FROM users")).fetchone())["c"]
        total_msgs    = (await (await db.execute("SELECT stat_value FROM stats WHERE stat_key='total_messages'")).fetchone())["stat_value"]
        total_eps     = (await (await db.execute("SELECT COUNT(*) as c FROM episodes")).fetchone())["c"]
        total_seasons = (await (await db.execute("SELECT COUNT(*) as c FROM seasons")).fetchone())["c"]
        top_ep = await (await db.execute("""
            SELECT e.episode_name, e.episode_number, s.season_number, e.view_count,
                   CASE WHEN e.rating_count>0 THEN ROUND(e.rating_sum*1.0/e.rating_count,1) ELSE 0 END as avg_rating
            FROM episodes e JOIN seasons s ON e.season_id=s.id
            ORDER BY e.view_count DESC LIMIT 1""")).fetchone()
        top_season = await (await db.execute("""
            SELECT s.season_name, SUM(e.view_count) as total
            FROM episodes e JOIN seasons s ON e.season_id=s.id
            GROUP BY e.season_id ORDER BY total DESC LIMIT 1""")).fetchone()

    text = (
        f"📊 *إحصائيات البوت*\n\n"
        f"👥 المستخدمون: `{total_users}`\n"
        f"💬 الرسائل: `{total_msgs}`\n"
        f"📺 المواسم: `{total_seasons}`\n"
        f"🎬 الحلقات: `{total_eps}`\n\n"
    )
    if top_ep:
        text += (f"🔥 أكثر حلقة مشاهدة:\n"
                 f"الموسم {top_ep['season_number']} | الحلقة {top_ep['episode_number']} - {top_ep['episode_name']}\n"
                 f"({top_ep['view_count']} مشاهدة | ⭐ {top_ep['avg_rating']}/5)\n\n")
    if top_season:
        text += f"⭐ أكثر موسم تفاعلاً:\n{top_season['season_name']} ({top_season['total']} مشاهدة)"

    kb = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_stats(update, context)

# ==================== إدارة القنوات ====================
async def manage_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ ليس لديك صلاحية!", show_alert=True)
        return
    channels = await get_channels()
    text = "📢 *قنوات الاشتراك الإجباري*\n\n"
    kb = []
    if channels:
        for ch in channels:
            text += f"• {ch[2]} (`{ch[1]}`)\n"
            kb.append([InlineKeyboardButton(f"🗑 حذف {ch[2]}", callback_data=f"delch_{ch[0]}")])
    else:
        text += "لا توجد قنوات."
    kb += [[InlineKeyboardButton("➕ إضافة قناة", callback_data="add_channel")],
           [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def delete_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    ch_id = int(query.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM channels WHERE id=?", (ch_id,))
        await db.commit()
    await manage_channels(update, context)

async def add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ ليس لديك صلاحية!", show_alert=True)
        return ConversationHandler.END
    await query.edit_message_text(
        "📢 *إضافة قناة*\n\nأرسل معرف القناة:\n`@mychannel` أو `-1001234567890`\n\n⚠️ البوت يجب أن يكون مشرفاً!",
        parse_mode="Markdown"
    )
    return WAITING_ADD_CHANNEL

async def add_channel_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    ch_id = raw if raw.startswith(("@","-")) else f"@{raw}"
    try:
        chat: Chat = await context.bot.get_chat(ch_id)
        name = chat.title or ch_id
        link = f"https://t.me/{chat.username}" if chat.username else (
            await context.bot.export_chat_invite_link(ch_id) if True else ch_id)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR REPLACE INTO channels (channel_id, channel_name, channel_link) VALUES (?,?,?)",
                             (ch_id, name, link))
            await db.commit()
        kb = [[InlineKeyboardButton("📢 إدارة القنوات", callback_data="manage_channels")],
              [InlineKeyboardButton("🏠 الرئيسية", callback_data="back_main")]]
        await update.message.reply_text(f"✅ تم إضافة *{name}*!", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ فشل: `{e}`", parse_mode="Markdown")
        return WAITING_ADD_CHANNEL
    return ConversationHandler.END

# ==================== مسح البيانات ====================
async def reset_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ ليس لديك صلاحية!", show_alert=True)
        return
    kb = [[InlineKeyboardButton("✅ نعم احذف", callback_data="confirm_reset")],
          [InlineKeyboardButton("❌ إلغاء", callback_data="back_main")]]
    await query.edit_message_text("⚠️ *تحذير!* سيتم حذف جميع المواسم والحلقات!",
                                  reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def confirm_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id): return
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM episodes")
        await db.execute("DELETE FROM seasons")
        await db.execute("DELETE FROM episode_ratings")
        try:
            await db.execute("DELETE FROM sqlite_sequence WHERE name IN ('episodes','seasons','episode_ratings')")
        except: pass
        await db.commit()
    kb = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="back_main")]]
    await query.edit_message_text("✅ تم مسح قاعدة البيانات!", reply_markup=InlineKeyboardMarkup(kb))

# ==================== عرض المواسم ====================
async def view_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await require_sub(update, context): return
    seasons = await get_seasons()
    if not seasons:
        await query.edit_message_text("❌ لا توجد مواسم بعد.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]))
        return
    kb = [[InlineKeyboardButton(f"🗂 الموسم {s[1]} - {s[2]}", callback_data=f"season_{s[0]}")] for s in seasons]
    kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
    await query.edit_message_text("📂 *اختر الموسم:*", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def view_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await require_sub(update, context): return
    season_id = int(query.data.split("_")[1])
    episodes  = await get_episodes(season_id)
    async with aiosqlite.connect(DB_NAME) as db:
        c = await db.execute("SELECT * FROM seasons WHERE id=?", (season_id,))
        season = await c.fetchone()
    if not episodes:
        kb = [[InlineKeyboardButton("🔙 رجوع", callback_data="view_series")]]
        await query.edit_message_text(f"❌ لا توجد حلقات في الموسم {season[1]}.", reply_markup=InlineKeyboardMarkup(kb))
        return
    kb = [[InlineKeyboardButton(f"▶️ الحلقة {ep[2]} - {ep[3]}", callback_data=f"ep_{ep[0]}")] for ep in episodes]
    kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="view_series")])
    await query.edit_message_text(f"📺 *الموسم {season[1]} - {season[2]}*\n\nاختر الحلقة:",
                                  reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

# ==================== عرض الحلقة ====================
async def view_episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await require_sub(update, context): return
    ep_id = int(query.data.split("_")[1])
    ep    = await get_episode_by_id(ep_id)
    if not ep:
        await query.edit_message_text("❌ الحلقة غير موجودة!")
        return
    await increment_view(ep_id)

    video_file_id  = ep["episode_video"]
    season_number  = ep["season_number"]
    season_name    = ep["season_name"]
    episode_number = ep["episode_number"]
    episode_name   = ep["episode_name"]
    episode_desc   = ep["episode_desc"] or ""
    season_id      = ep["season_id"]
    rating_sum     = ep["rating_sum"] or 0
    rating_count   = ep["rating_count"] or 0
    avg_rating     = round(rating_sum / rating_count, 1) if rating_count > 0 else 0

    caption = f"🎬 *{season_name} | الحلقة {episode_number}*\n📌 {episode_name}"
    if episode_desc:
        caption += f"\n\n📝 {episode_desc}"
    if rating_count > 0:
        caption += f"\n\n⭐ التقييم: {avg_rating}/5 ({rating_count} تقييم)"

    context.user_data["current_ep"] = {
        "season": season_number, "season_name": season_name,
        "episode": episode_number, "name": episode_name, "desc": episode_desc,
    }

    kb = [
        [InlineKeyboardButton("🤖 اسأل عن الحلقة",  callback_data=f"ai_ep_{ep_id}"),
         InlineKeyboardButton("⭐ قيّم الحلقة",       callback_data=f"rate_ep_{ep_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"season_{season_id}")],
    ]
    await query.message.delete()

    if video_file_id:
        try:
            await context.bot.send_video(
                chat_id=query.message.chat_id, video=video_file_id,
                caption=caption, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        except Exception as e:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ خطأ في الفيديو: {e}\n\n{caption}",
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
            )
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"❌ لا يوجد فيديو!\n\n{caption}",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
        )

# ==================== AI ====================
async def ai_episode_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ep_id = int(query.data.split("_")[2])
    context.user_data["ai_ep_id"] = ep_id
    kb = [
        [InlineKeyboardButton("📖 ملخص الحلقة",  callback_data=f"ai_quick_summary_{ep_id}")],
        [InlineKeyboardButton("⭐ تقييم IMDb",    callback_data=f"ai_quick_rating_{ep_id}")],
        [InlineKeyboardButton("🎭 الشخصيات",      callback_data=f"ai_quick_chars_{ep_id}")],
        [InlineKeyboardButton("✍️ اكتب سؤالك",    callback_data=f"ai_custom_{ep_id}")],
        [InlineKeyboardButton("🔙 رجوع",          callback_data=f"ep_{ep_id}")],
    ]
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

async def ai_quick_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ جاري التفكير...")
    parts  = query.data.split("_")
    action = parts[2]
    ep_id  = int(parts[3])
    ep     = await get_episode_by_id(ep_id)
    ep_info = f"الموسم {ep['season_number']} - الحلقة {ep['episode_number']} - {ep['episode_name']}" if ep else ""
    questions = {
        "summary": f"ملخص مختصر لـ {ep_info} من The Last Kingdom",
        "rating":  f"تقييم {ep_info} على IMDb وRotten Tomatoes",
        "chars":   f"أبرز الشخصيات في {ep_info} من The Last Kingdom",
    }
    question = questions.get(action, "")
    if not question: return
    thinking = await context.bot.send_message(
        chat_id=query.message.chat_id, text="🤖 _جاري البحث..._", parse_mode="Markdown"
    )
    answer = await ask_ai(question, ep_info)
    if answer == "__DEVELOPER__":
        await thinking.delete()
        await send_developer_info(query.message.chat_id, context)
        return
    kb = [[InlineKeyboardButton("✍️ سؤال آخر", callback_data=f"ai_ep_{ep_id}")],
          [InlineKeyboardButton("🔙 رجوع للحلقة", callback_data=f"ep_{ep_id}")]]
    await thinking.edit_text(f"🤖 *The Last Kingdom AI*\n\n{answer}",
                             reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def ai_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ep_id = int(query.data.split("_")[2])
    context.user_data["ai_ep_id"] = ep_id
    kb = [[InlineKeyboardButton("❌ إلغاء", callback_data=f"ai_ep_{ep_id}")]]
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="✍️ *اكتب سؤالك عن الحلقة أو المسلسل:*",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return WAITING_AI_QUESTION

async def ai_handle_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = update.message.text.strip()
    ep_id    = context.user_data.get("ai_ep_id")
    ep_info  = ""
    if ep_id:
        ep = await get_episode_by_id(ep_id)
        if ep:
            ep_info = f"الموسم {ep['season_number']} - الحلقة {ep['episode_number']} - {ep['episode_name']}"

    thinking = await update.message.reply_text("🤖 _جاري التفكير..._", parse_mode="Markdown")
    answer   = await ask_ai(question, ep_info)

    if answer == "__DEVELOPER__":
        await thinking.delete()
        await send_developer_info(update.message.chat_id, context)
        return ConversationHandler.END

    kb = []
    if ep_id:
        kb = [[InlineKeyboardButton("✍️ سؤال آخر", callback_data=f"ai_ep_{ep_id}")],
              [InlineKeyboardButton("🔙 رجوع للحلقة", callback_data=f"ep_{ep_id}")]]
    else:
        kb = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="back_main")]]

    await thinking.edit_text(f"🤖 *The Last Kingdom AI*\n\n{answer}",
                             reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return ConversationHandler.END

# ==================== الرسائل ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    chat_type = msg.chat.type
    text      = msg.text.strip()
    await increment_messages()

    if chat_type in ("group","supergroup"):
        bot_username = context.bot.username
        is_reply_bot = (msg.reply_to_message and
                        msg.reply_to_message.from_user and
                        msg.reply_to_message.from_user.id == context.bot.id)
        is_mention = f"@{bot_username}" in text
        if not is_reply_bot and not is_mention:
            await check_shortcuts(update, context)
            return
        text = text.replace(f"@{bot_username}", "").strip()

    q = text.lower()
    if any(w in q for w in DEVELOPER_WORDS):
        await send_developer_info(msg.chat_id, context)
        return

    thinking = await msg.reply_text("🤖 _جاري التفكير..._", parse_mode="Markdown")
    ep_data  = context.user_data.get("current_ep")
    ep_info  = f"الموسم {ep_data['season']} - الحلقة {ep_data['episode']} - {ep_data['name']}" if ep_data else ""
    answer   = await ask_ai(text, ep_info)

    if answer == "__DEVELOPER__":
        await thinking.delete()
        await send_developer_info(msg.chat_id, context)
        return

    await thinking.edit_text(f"🤖 *The Last Kingdom AI*\n\n{answer}", parse_mode="Markdown")

# ==================== الاختصارات ====================
async def check_shortcuts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return
    trigger = msg.text.strip()
    async with aiosqlite.connect(DB_NAME) as db:
        c = await db.execute(
            "SELECT * FROM shortcuts WHERE group_id=? AND trigger=?", (msg.chat_id, trigger))
        sc = await c.fetchone()
    if not sc: return
    ct, content, file_id = sc[3], sc[4], sc[5]
    try:
        if ct == "text":   await msg.reply_text(content)
        elif ct == "photo": await msg.reply_photo(photo=file_id, caption=content or "")
        elif ct == "video": await msg.reply_video(video=file_id, caption=content or "")
        elif ct == "audio": await msg.reply_audio(audio=file_id, caption=content or "")
        elif ct == "voice": await msg.reply_voice(voice=file_id)
    except Exception as e: logger.error(f"Shortcut error: {e}")

async def add_shortcut_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if update.effective_chat.type not in ("group","supergroup"):
        await update.message.reply_text("هذا الأمر يعمل فقط في الجروبات.")
        return
    if not context.args:
        await update.message.reply_text("الاستخدام:\n`/shortcut اسم_الاختصار`\nثم أرسل المحتوى.", parse_mode="Markdown")
        return
    trigger = " ".join(context.args).strip()
    context.user_data["shortcut_trigger"] = trigger
    context.user_data["shortcut_group"]   = update.effective_chat.id
    await update.message.reply_text(
        f"✅ الاختصار: *{trigger}*\n\nأرسل المحتوى الآن (نص/صورة/فيديو/صوت):",
        parse_mode="Markdown"
    )
    return WAITING_SHORTCUT_CONTENT

async def save_shortcut_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    msg      = update.message
    trigger  = context.user_data.get("shortcut_trigger")
    group_id = context.user_data.get("shortcut_group")
    if not trigger or not group_id: return ConversationHandler.END

    ct, content, file_id = "text", "", None
    if msg.text:    ct, content = "text", msg.text
    elif msg.photo: ct, file_id, content = "photo", msg.photo[-1].file_id, msg.caption or ""
    elif msg.video: ct, file_id, content = "video", msg.video.file_id, msg.caption or ""
    elif msg.audio: ct, file_id, content = "audio", msg.audio.file_id, msg.caption or ""
    elif msg.voice: ct, file_id = "voice", msg.voice.file_id
    else:
        await msg.reply_text("❌ نوع غير مدعوم!")
        return ConversationHandler.END

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO shortcuts (group_id, trigger, content_type, content, file_id) VALUES (?,?,?,?,?)",
            (group_id, trigger, ct, content, file_id)
        )
        await db.commit()

    # ✅ تسجيل الاختصار كأمر رسمي في تيليغرام ليظهر عند كتابة /
    await sync_shortcuts_to_commands(context.application, group_id)

    await msg.reply_text(f"✅ تم حفظ الاختصار: *{trigger}*\nيظهر الآن عند كتابة `/` في الجروب.", parse_mode="Markdown")
    return ConversationHandler.END

async def list_shortcuts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض جميع الاختصارات"""
    if update.effective_chat.type not in ("group","supergroup"):
        await update.message.reply_text("هذا الأمر يعمل فقط في الجروبات.")
        return
    shortcuts = await get_shortcuts_for_group(update.effective_chat.id)
    if not shortcuts:
        await update.message.reply_text("لا توجد اختصارات مضافة بعد.")
        return
    text = "📋 *الاختصارات المتاحة:*\n\n"
    for sc in shortcuts:
        text += f"• `{sc[0]}` — {sc[1]}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ==================== إدارة الأدمن في الجروب ====================
async def promote_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    target_id = None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            m = await context.bot.get_chat_member(update.effective_chat.id, f"@{context.args[0].replace('@','')}")
            target_id = m.user.id
        except: pass
    if not target_id:
        await update.message.reply_text("❌ حدد المستخدم بالريبلاي أو: /promote @username")
        return
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO group_admins (group_id, user_id) VALUES (?,?)",
                         (update.effective_chat.id, target_id))
        await db.commit()
    await update.message.reply_text("✅ تم رفع الأدمن!")

async def demote_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    target_id = None
    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            m = await context.bot.get_chat_member(update.effective_chat.id, f"@{context.args[0].replace('@','')}")
            target_id = m.user.id
        except: pass
    if not target_id:
        await update.message.reply_text("❌ حدد المستخدم بالريبلاي أو: /demote @username")
        return
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM group_admins WHERE group_id=? AND user_id=?",
                         (update.effective_chat.id, target_id))
        await db.commit()
    await update.message.reply_text("✅ تم تنزيل الأدمن!")

async def demote_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM group_admins WHERE group_id=?", (update.effective_chat.id,))
        await db.commit()
    await update.message.reply_text("✅ تم تنزيل جميع الأدمن!")

# ==================== إضافة موسم ====================
async def add_season_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ ليس لديك صلاحية!", show_alert=True)
        return ConversationHandler.END
    await query.edit_message_text("📝 أرسل *رقم الموسم* (مثال: 1):", parse_mode="Markdown")
    return WAITING_SEASON_NUMBER

async def add_season_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: num = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ أرسل رقماً!")
        return WAITING_SEASON_NUMBER
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT INTO seasons (season_number, season_name) VALUES (?,?)", (num, f"الموسم {num}"))
            await db.commit()
        kb = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="back_main")]]
        await update.message.reply_text(f"✅ تم إضافة الموسم {num}!", reply_markup=InlineKeyboardMarkup(kb))
    except: await update.message.reply_text("❌ الموسم موجود مسبقاً!")
    return ConversationHandler.END

# ==================== إضافة حلقة ====================
async def add_episode_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ ليس لديك صلاحية!", show_alert=True)
        return ConversationHandler.END
    seasons = await get_seasons()
    if not seasons:
        await query.edit_message_text("❌ أضف موسماً أولاً!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]))
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"الموسم {s[1]} - {s[2]}", callback_data=f"epseason_{s[0]}")] for s in seasons]
    await query.edit_message_text("📺 اختر الموسم:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_EP_SEASON

async def add_episode_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["ep_season_id"] = int(query.data.split("_")[1])
    await query.edit_message_text("📝 أرسل *رقم الحلقة* (مثال: 1):", parse_mode="Markdown")
    return WAITING_EP_NUMBER

async def add_episode_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        num = int(update.message.text.strip())
        context.user_data["ep_number"] = num
        context.user_data["ep_name"]   = f"الحلقة {num}"
    except ValueError:
        await update.message.reply_text("❌ أرسل رقماً!")
        return WAITING_EP_NUMBER
    await update.message.reply_text("🎬 أرسل *فيديو الحلقة* الآن:", parse_mode="Markdown")
    return WAITING_EP_VIDEO

async def add_episode_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.video:
        fid = update.message.video.file_id
    elif update.message.document and update.message.document.mime_type and "video" in update.message.document.mime_type:
        fid = update.message.document.file_id
    else:
        await update.message.reply_text("❌ أرسل فيديو صحيحاً!")
        return WAITING_EP_VIDEO
    context.user_data["ep_video"] = fid
    kb = [[InlineKeyboardButton("📝 إضافة وصف", callback_data="ep_add_desc")],
          [InlineKeyboardButton("⏭ تخطي",        callback_data="ep_skip_desc")]]
    await update.message.reply_text("✅ *تم استقبال الفيديو!*\n\nهل تريد إضافة وصف؟",
                                    reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return WAITING_EP_DESC

async def ep_add_desc_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📝 أرسل *وصف الحلقة*:", parse_mode="Markdown")
    return WAITING_EP_DESC

async def ep_save_with_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ep_desc"] = update.message.text.strip()
    return await _save_ep(update.message, context)

async def ep_skip_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["ep_desc"] = ""
    return await _save_ep(query, context, from_callback=True)

async def _save_ep(msg_or_query, context, from_callback=False):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "INSERT INTO episodes (season_id, episode_number, episode_name, episode_video, episode_desc) VALUES (?,?,?,?,?)",
            (context.user_data["ep_season_id"], context.user_data["ep_number"],
             context.user_data["ep_name"], context.user_data["ep_video"],
             context.user_data.get("ep_desc",""))
        )
        await db.commit()
        ep_id = cursor.lastrowid
    kb = [[InlineKeyboardButton("▶️ عرض الحلقة",  callback_data=f"ep_{ep_id}")],
          [InlineKeyboardButton("🏠 الرئيسية",    callback_data="back_main")]]
    text = f"✅ *تمت إضافة الحلقة {context.user_data['ep_number']} بنجاح!*"
    if from_callback:
        await msg_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await msg_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return ConversationHandler.END

# ==================== تعديل الموسم ====================
async def edit_season_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ ليس لديك صلاحية!", show_alert=True)
        return ConversationHandler.END
    seasons = await get_seasons()
    if not seasons:
        await query.edit_message_text("❌ لا توجد مواسم!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]))
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"✏️ الموسم {s[1]} - {s[2]}", callback_data=f"editseason_{s[0]}")] for s in seasons]
    kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
    await query.edit_message_text("اختر الموسم:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_EDIT_SEASON_NAME

async def edit_season_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["edit_season_id"] = int(query.data.split("_")[1])
    await query.edit_message_text("📝 أرسل *الاسم الجديد*:", parse_mode="Markdown")
    return WAITING_EDIT_SEASON_NAME

async def edit_season_name_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE seasons SET season_name=? WHERE id=?", (name, context.user_data["edit_season_id"]))
        await db.commit()
    kb = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="back_main")]]
    await update.message.reply_text(f"✅ تم التعديل إلى: *{name}*", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return ConversationHandler.END

# ==================== تعديل الحلقة ====================
async def edit_episode_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ ليس لديك صلاحية!", show_alert=True)
        return ConversationHandler.END
    seasons = await get_seasons()
    if not seasons:
        await query.edit_message_text("❌ لا توجد مواسم!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]))
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"الموسم {s[1]} - {s[2]}", callback_data=f"editepseason_{s[0]}")] for s in seasons]
    kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
    await query.edit_message_text("اختر الموسم:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_EDIT_EP_NAME

async def edit_episode_pick_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    episodes = await get_episodes(int(query.data.split("_")[1]))
    if not episodes:
        await query.edit_message_text("❌ لا توجد حلقات!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]))
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"الحلقة {ep[2]} - {ep[3]}", callback_data=f"editep_{ep[0]}")] for ep in episodes]
    kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
    await query.edit_message_text("اختر الحلقة:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_EDIT_EP_NAME

async def edit_episode_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["edit_ep_id"] = int(query.data.split("_")[1])
    kb = [[InlineKeyboardButton("✏️ تعديل الاسم", callback_data="editep_name")],
          [InlineKeyboardButton("📝 تعديل الوصف", callback_data="editep_desc")],
          [InlineKeyboardButton("🔙 رجوع",         callback_data="back_main")]]
    await query.edit_message_text("ماذا تريد تعديل؟", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_EDIT_EP_NAME

async def edit_ep_choose_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["edit_ep_field"] = "episode_name"
    await query.edit_message_text("📝 أرسل *الاسم الجديد*:", parse_mode="Markdown")
    return WAITING_EDIT_EP_NAME

async def edit_ep_choose_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["edit_ep_field"] = "episode_desc"
    await query.edit_message_text("📝 أرسل *الوصف الجديد*:", parse_mode="Markdown")
    return WAITING_EDIT_EP_NAME

async def edit_episode_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val   = update.message.text.strip()
    ep_id = context.user_data["edit_ep_id"]
    col   = context.user_data.get("edit_ep_field", "episode_name")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"UPDATE episodes SET {col}=? WHERE id=?", (val, ep_id))
        await db.commit()
    kb = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="back_main")]]
    await update.message.reply_text(f"✅ تم التعديل!", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# ==================== حذف موسم ====================
async def delete_season_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ ليس لديك صلاحية!", show_alert=True)
        return ConversationHandler.END
    seasons = await get_seasons()
    if not seasons:
        await query.edit_message_text("❌ لا توجد مواسم!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]))
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"🗑 الموسم {s[1]} - {s[2]}", callback_data=f"delseason_{s[0]}")] for s in seasons]
    kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
    await query.edit_message_text("⚠️ اختر الموسم للحذف:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_DELETE_SEASON

async def confirm_delete_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sid = int(query.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        c = await db.execute("SELECT * FROM seasons WHERE id=?", (sid,))
        season = await c.fetchone()
        await db.execute("DELETE FROM episodes WHERE season_id=?", (sid,))
        await db.execute("DELETE FROM seasons WHERE id=?", (sid,))
        await db.commit()
    kb = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="back_main")]]
    await query.edit_message_text(f"✅ تم حذف الموسم {season[1]}!", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

# ==================== حذف حلقة ====================
async def delete_episode_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("⛔ ليس لديك صلاحية!", show_alert=True)
        return ConversationHandler.END
    seasons = await get_seasons()
    if not seasons:
        await query.edit_message_text("❌ لا توجد مواسم!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]))
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"الموسم {s[1]} - {s[2]}", callback_data=f"delep_season_{s[0]}")] for s in seasons]
    kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
    await query.edit_message_text("اختر الموسم:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_DELETE_EP

async def delete_episode_pick_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    episodes = await get_episodes(int(query.data.split("_")[2]))
    if not episodes:
        await query.edit_message_text("❌ لا توجد حلقات!",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]))
        return ConversationHandler.END
    kb = [[InlineKeyboardButton(f"❌ الحلقة {ep[2]} - {ep[3]}", callback_data=f"delepid_{ep[0]}")] for ep in episodes]
    kb.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_main")])
    await query.edit_message_text("اختر الحلقة للحذف:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_DELETE_EP

async def confirm_delete_episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ep_id = int(query.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        c = await db.execute("SELECT * FROM episodes WHERE id=?", (ep_id,))
        ep = await c.fetchone()
        await db.execute("DELETE FROM episodes WHERE id=?", (ep_id,))
        await db.execute("DELETE FROM episode_ratings WHERE episode_id=?", (ep_id,))
        await db.commit()
    kb = [[InlineKeyboardButton("🏠 الرئيسية", callback_data="back_main")]]
    await query.edit_message_text(f"✅ تم حذف الحلقة {ep[2]}!", reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء.")
    return ConversationHandler.END

# ==================== معالجة الأخطاء ====================
async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, Forbidden):
        if update and hasattr(update, 'effective_user') and update.effective_user:
            await remove_blocked_user(update.effective_user.id)
    else:
        logger.error(f"Error: {context.error}", exc_info=True)

# ==================== التشغيل الرئيسي ====================
async def main():
    await init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    await app.bot.delete_webhook(drop_pending_updates=True)

    # تسجيل الأوامر الثابتة
    await app.bot.set_my_commands([
        BotCommand("start",     "ابدأ البوت"),
        BotCommand("stats",     "الإحصائيات"),
        BotCommand("shortcut",  "إضافة اختصار - للأدمن"),
        BotCommand("shortcuts", "عرض جميع الاختصارات"),
        BotCommand("promote",   "رفع أدمن"),
        BotCommand("demote",    "تنزيل أدمن"),
        BotCommand("demote_all","تنزيل جميع الأدمن"),
    ])

    season_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_season_start, pattern="^add_season$")],
        states={WAITING_SEASON_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_season_number)]},
        fallbacks=[CommandHandler("cancel", cancel)], per_message=False,
    )
    episode_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_episode_start, pattern="^add_episode$")],
        states={
            WAITING_EP_SEASON: [CallbackQueryHandler(add_episode_season, pattern="^epseason_")],
            WAITING_EP_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_episode_number)],
            WAITING_EP_VIDEO:  [MessageHandler(filters.VIDEO | filters.Document.VIDEO, add_episode_video)],
            WAITING_EP_DESC: [
                CallbackQueryHandler(ep_add_desc_prompt, pattern="^ep_add_desc$"),
                CallbackQueryHandler(ep_skip_desc,       pattern="^ep_skip_desc$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ep_save_with_desc),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)], per_message=False,
    )
    edit_season_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_season_start, pattern="^edit_season$")],
        states={
            WAITING_EDIT_SEASON_NAME: [
                CallbackQueryHandler(edit_season_pick, pattern="^editseason_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_season_name_save),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)], per_message=False,
    )
    edit_episode_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_episode_menu_start, pattern="^edit_episode_menu$")],
        states={
            WAITING_EDIT_EP_NAME: [
                CallbackQueryHandler(edit_episode_pick_season, pattern="^editepseason_"),
                CallbackQueryHandler(edit_episode_pick,        pattern="^editep_\\d+$"),
                CallbackQueryHandler(edit_ep_choose_name,      pattern="^editep_name$"),
                CallbackQueryHandler(edit_ep_choose_desc,      pattern="^editep_desc$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_episode_save),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)], per_message=False,
    )
    delete_season_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(delete_season_start, pattern="^delete_season$")],
        states={WAITING_DELETE_SEASON: [CallbackQueryHandler(confirm_delete_season, pattern="^delseason_")]},
        fallbacks=[CommandHandler("cancel", cancel)], per_message=False,
    )
    delete_episode_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(delete_episode_start, pattern="^delete_episode$")],
        states={
            WAITING_DELETE_EP: [
                CallbackQueryHandler(delete_episode_pick_season, pattern="^delep_season_"),
                CallbackQueryHandler(confirm_delete_episode,     pattern="^delepid_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)], per_message=False,
    )
    channel_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_channel_start, pattern="^add_channel$")],
        states={WAITING_ADD_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_save)]},
        fallbacks=[CommandHandler("cancel", cancel)], per_message=False,
    )
    ai_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ai_custom_start, pattern="^ai_custom_\\d+$")],
        states={WAITING_AI_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, ai_handle_question)]},
        fallbacks=[CommandHandler("cancel", cancel)], per_message=False,
    )
    shortcut_conv = ConversationHandler(
        entry_points=[CommandHandler("shortcut", add_shortcut_command)],
        states={WAITING_SHORTCUT_CONTENT: [
            MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE,
                           save_shortcut_content)
        ]},
        fallbacks=[CommandHandler("cancel", cancel)], per_message=False,
    )

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("stats",      stats_command))
    app.add_handler(CommandHandler("shortcuts",  list_shortcuts_command))
    app.add_handler(CommandHandler("promote",    promote_admin))
    app.add_handler(CommandHandler("demote",     demote_admin))
    app.add_handler(CommandHandler("demote_all", demote_all))

    app.add_handler(season_conv)
    app.add_handler(episode_conv)
    app.add_handler(edit_season_conv)
    app.add_handler(edit_episode_conv)
    app.add_handler(delete_season_conv)
    app.add_handler(delete_episode_conv)
    app.add_handler(channel_conv)
    app.add_handler(ai_conv)
    app.add_handler(shortcut_conv)

    app.add_handler(CallbackQueryHandler(view_series,          pattern="^view_series$"))
    app.add_handler(CallbackQueryHandler(view_season,          pattern="^season_\\d+$"))
    app.add_handler(CallbackQueryHandler(view_episode,         pattern="^ep_\\d+$"))
    app.add_handler(CallbackQueryHandler(back_main,            pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(reset_db,             pattern="^reset_db$"))
    app.add_handler(CallbackQueryHandler(confirm_reset,        pattern="^confirm_reset$"))
    app.add_handler(CallbackQueryHandler(manage_channels,      pattern="^manage_channels$"))
    app.add_handler(CallbackQueryHandler(delete_channel,       pattern="^delch_\\d+$"))
    app.add_handler(CallbackQueryHandler(check_sub_callback,   pattern="^check_sub$"))
    app.add_handler(CallbackQueryHandler(ai_episode_start,     pattern="^ai_ep_\\d+$"))
    app.add_handler(CallbackQueryHandler(ai_quick_answer,      pattern="^ai_quick_\\w+_\\d+$"))
    app.add_handler(CallbackQueryHandler(show_stats,           pattern="^show_stats$"))
    app.add_handler(CallbackQueryHandler(rate_episode_start,   pattern="^rate_ep_\\d+$"))
    app.add_handler(CallbackQueryHandler(submit_rating,        pattern="^do_rate_\\d+_\\d+$"))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (filters.ChatType.PRIVATE | filters.ChatType.GROUPS),
        handle_message
    ))
    app.add_error_handler(handle_error)

    print("✅ البوت يعمل...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
