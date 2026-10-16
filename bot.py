import logging
import os
import sqlite3
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler, ContextTypes,
    MessageHandler, filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
log = logging.getLogger("jobby-welcome-bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "/data/jobby-welcome-bot.sqlite3")
ADMIN_IDS = {
    int(item.strip()) for item in os.getenv("ADMIN_IDS", "").split(",")
    if item.strip().isdigit()
}
COMMUNITY_URL = os.getenv("COMMUNITY_URL", "https://t.me/JOBBYChat")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/JOBBYSOL")
X_URL = os.getenv("X_URL", "https://x.com/JOBBYSOL")
WEBSITE_URL = os.getenv("WEBSITE_URL", "https://jobby.lol").strip() or "https://jobby.lol"
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID", "").strip()

LEGACY_DEFAULT_WELCOME = (
    "Welcome, {name}!\n\n"
    "You’re now part of the JOBBY community. Join the conversation, "
    "share your ideas, and have fun with us."
)

DEFAULT_WELCOME = (
    "Welcome to JOBBY\n\n"
    "No lore. Just JOBBY.\n"
    "Meme him. Remix him. Put him anywhere.\n\n"
    "Drop a meme, say hello and join the chaos.\n"
    "$JOBBY on Solana · Launching soon.\n\n"
    "Official links:\n"
    "🌐: https://jobby.lol\n"
    "𝕏: https://x.com/JOBBYSOL\n"
    "✈️: https://t.me/JOBBYSOL\n\n"
    "Admins will never DM you first or ask for your seed phrase."
)

def connect_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            chat_id INTEGER PRIMARY KEY,
            welcome_text TEXT NOT NULL,
            photo_file_id TEXT
        )
    """)
    # Upgrade only the original stock message; preserve admin-customized text.
    conn.execute(
        "UPDATE settings SET welcome_text=? WHERE welcome_text=?",
        (DEFAULT_WELCOME, LEGACY_DEFAULT_WELCOME),
    )
    conn.commit()
    return conn

def get_settings(chat_id):
    with connect_db() as conn:
        row = conn.execute(
            "SELECT welcome_text, photo_file_id FROM settings WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        if row:
            return row[0], row[1]
        conn.execute(
            "INSERT OR IGNORE INTO settings(chat_id, welcome_text, photo_file_id) VALUES(?,?,?)",
            (chat_id, DEFAULT_WELCOME, None),
        )
        conn.commit()
        return DEFAULT_WELCOME, None

def save_settings(chat_id, welcome_text=None, photo_file_id=None, set_photo=False):
    text, photo = get_settings(chat_id)
    if welcome_text is not None:
        text = welcome_text
    if set_photo:
        photo = photo_file_id
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO settings(chat_id, welcome_text, photo_file_id) VALUES(?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET welcome_text=excluded.welcome_text, "
            "photo_file_id=excluded.photo_file_id",
            (chat_id, text, photo),
        )
        conn.commit()

def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in ADMIN_IDS)

def settings_chat_id(update: Update):
    """Resolve the group whose welcome settings should be edited from private admin chat."""
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        return chat.id
    if TARGET_CHAT_ID.lstrip("-").isdigit():
        return int(TARGET_CHAT_ID)
    # If TARGET_CHAT_ID was not configured, use the most recently saved group.
    try:
        with connect_db() as conn:
            row = conn.execute(
                "SELECT chat_id FROM settings WHERE chat_id < 0 ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if row:
                return row[0]
    except Exception:
        log.exception("Could not resolve the configured welcome group")
    return chat.id if chat else None

def make_keyboard(include_admin=False):
    rows = [
        [InlineKeyboardButton("X ↗", url=X_URL),
         InlineKeyboardButton("Channel ↗", url=CHANNEL_URL),
         InlineKeyboardButton("Group ↗", url=COMMUNITY_URL)],
        [InlineKeyboardButton("Website ↗", url=WEBSITE_URL)],
    ]
    if include_admin:
        rows.append([InlineKeyboardButton("Admin Controls", callback_data="admin_controls"),
                     InlineKeyboardButton("My Telegram ID", callback_data="show_myid")])
    return InlineKeyboardMarkup(rows)

async def send_welcome(chat_id, member, context):
    welcome_text, photo_id = get_settings(chat_id)
    name = member.full_name or member.first_name or "there"
    message = welcome_text.replace("{name}", name).replace("{username}", ("@" + member.username) if member.username else name)
    keyboard = make_keyboard()
    try:
        if photo_id:
            await context.bot.send_photo(
                chat_id=chat_id, photo=photo_id, caption=message,
                reply_markup=keyboard,
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id, text=message, reply_markup=keyboard,
                disable_web_page_preview=True,
            )
    except Exception:
        log.exception("Could not send welcome message in chat %s", chat_id)

async def new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    for member in message.new_chat_members or []:
        if context.bot and member.id == context.bot.id:
            continue
        await send_welcome(message.chat_id, member, context)

async def member_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    change = update.chat_member
    if not change:
        return
    old = change.old_chat_member
    new = change.new_chat_member
    if old.status in ("left", "kicked") and new.status in ("member", "administrator", "creator"):
        if context.bot and new.user.id != context.bot.id:
            await send_welcome(update.effective_chat.id, new.user, context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message:
        await update.effective_message.reply_text(
            "Welcome to JOBBY Welcome Bot.\n\n"
            "Use the buttons below to open our community and social pages, view your Telegram ID, "
            "or see the admin controls.",
            reply_markup=make_keyboard(include_admin=True),
            disable_web_page_preview=True,
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if query.data == "show_myid":
        user = query.from_user
        await query.message.reply_text(f"Your Telegram user ID: {user.id}")
    elif query.data == "admin_controls":
        if query.from_user.id not in ADMIN_IDS:
            await query.message.reply_text("These controls are available to configured bot admins only. Use /myid to check your ID.")
            return
        await query.message.reply_text(
            "JOBBY Admin Controls\n\n"
            "Change welcome text:\n/setwelcome Welcome, {name}! Welcome to JOBBY.\n\n"
            "Set welcome image: send a photo, then reply to it with /setphoto.\n\n"
            "Remove welcome image: /clearphoto\n\n"
            "Show commands: /help",
            disable_web_page_preview=True,
        )

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message and update.effective_user:
        await update.effective_message.reply_text(f"Your Telegram user ID: {update.effective_user.id}")

async def setwelcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_chat:
        return
    if not is_admin(update):
        await update.effective_message.reply_text("This command is restricted to configured bot admins.")
        return
    new_text = " ".join(context.args).strip()
    if not new_text:
        await update.effective_message.reply_text(
            "Usage: /setwelcome Welcome, {name}!\n\nWelcome to JOBBY.\n"
            "Supported placeholders: {name}, {username}. Keep the message in one command."
        )
        return
    if len(new_text) > 900:
        await update.effective_message.reply_text("Please keep the welcome message under 900 characters.")
        return
    chat_id = settings_chat_id(update)
    if chat_id is None:
        await update.effective_message.reply_text("Set TARGET_CHAT_ID in Railway Variables first.")
        return
    save_settings(chat_id, welcome_text=new_text)
    await update.effective_message.reply_text("Welcome message saved for this group.")

async def setphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_chat:
        return
    if not is_admin(update):
        await update.effective_message.reply_text("This command is restricted to configured bot admins.")
        return
    reply = update.effective_message.reply_to_message
    if not reply or not reply.photo:
        await update.effective_message.reply_text(
            "Reply to a photo with /setphoto to use it as the welcome image. "
            "You can do this in this private chat or in the group."
        )
        return
    chat_id = settings_chat_id(update)
    if chat_id is None:
        await update.effective_message.reply_text("Set TARGET_CHAT_ID in Railway Variables first.")
        return
    save_settings(
        chat_id,
        photo_file_id=reply.photo[-1].file_id,
        set_photo=True,
    )
    await update.effective_message.reply_text("Welcome image saved for this group.")

async def clearphoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_chat:
        return
    if not is_admin(update):
        await update.effective_message.reply_text("This command is restricted to configured bot admins.")
        return
    chat_id = settings_chat_id(update)
    if chat_id is None:
        await update.effective_message.reply_text("Set TARGET_CHAT_ID in Railway Variables first.")
        return
    save_settings(chat_id, photo_file_id=None, set_photo=True)
    await update.effective_message.reply_text("Welcome image removed for this group.")

async def quick_text_triggers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to exact social-link shortcuts in group chats."""
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or chat.type not in ("group", "supergroup"):
        return
    raw = (message.text or "").strip()
    if not raw:
        return
    key = raw.lower().strip().strip("!?.,:;()[]{}<> ")
    key = key.lstrip("@#").strip()
    if key == "x":
        await message.reply_text(f"Follow JOBBY on X: {X_URL}", disable_web_page_preview=True)
    elif key == "channel":
        await message.reply_text(f"JOBBY official channel: {CHANNEL_URL}", disable_web_page_preview=True)
    elif key == "telegram":
        await message.reply_text(
            "Join JOBBY on Telegram:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Community", url=COMMUNITY_URL)],
                [InlineKeyboardButton("Channel", url=CHANNEL_URL)],
            ]),
            disable_web_page_preview=True,
        )
    elif key == "community":
        await message.reply_text(f"JOBBY community: {COMMUNITY_URL}", disable_web_page_preview=True)
    elif key == "links":
        rows = [
            [InlineKeyboardButton("Community", url=COMMUNITY_URL),
             InlineKeyboardButton("Channel", url=CHANNEL_URL)],
            [InlineKeyboardButton("Follow JOBBY on X", url=X_URL)],
        ]
        if WEBSITE_URL.startswith(("https://", "http://")):
            rows.append([InlineKeyboardButton("Website", url=WEBSITE_URL)])
        await message.reply_text(
            "Official JOBBY links:",
            reply_markup=InlineKeyboardMarkup(rows),
            disable_web_page_preview=True,
        )
    elif key == "ca":
        await message.reply_text("SOON")
    elif key == "web":
        if WEBSITE_URL.startswith(("https://", "http://")):
            await message.reply_text(f"JOBBY website: {WEBSITE_URL}", disable_web_page_preview=True)
        else:
            await message.reply_text("The JOBBY website link has not been configured yet.")

async def settings_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message:
        await update.effective_message.reply_text(
            "Admin commands (configure ADMIN_IDS in Railway Variables):\n"
            "/myid — show your Telegram user ID\n"
            "/setwelcome Your text with {name} — update welcome text\n"
            "Reply to a photo with /setphoto — set welcome image\n"
            "Reply to any message with /clearphoto — remove welcome image"
        )

def main():
    if not TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")
    connect_db().close()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("setwelcome", setwelcome))
    app.add_handler(CommandHandler("setphoto", setphoto))
    app.add_handler(CommandHandler("clearphoto", clearphoto))
    app.add_handler(CommandHandler("help", settings_help))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quick_text_triggers))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_members))
    app.add_handler(ChatMemberHandler(member_status, ChatMemberHandler.CHAT_MEMBER))
    log.info("JOBBY welcome bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)

if __name__ == "__main__":
    main()
