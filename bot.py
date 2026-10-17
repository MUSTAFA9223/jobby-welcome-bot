import html
import logging
import os
import sqlite3
import time
from pathlib import Path

import message_stats
import message_stats_ui

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
GLOBAL_SETTINGS_ID = 0

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
            photo_file_id TEXT,
            welcome_html TEXT
        )
    """)
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(settings)").fetchall()
    }
    if "welcome_html" not in columns:
        conn.execute("ALTER TABLE settings ADD COLUMN welcome_html TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS links (
            chat_id INTEGER PRIMARY KEY,
            community_url TEXT NOT NULL,
            channel_url TEXT NOT NULL,
            x_url TEXT NOT NULL,
            website_url TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            config_key TEXT PRIMARY KEY,
            config_value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shortcuts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger TEXT NOT NULL UNIQUE COLLATE NOCASE,
            response_text TEXT NOT NULL,
            response_html TEXT
        )
    """)
    message_stats.ensure_schema(conn)
    # Upgrade only the original stock message; preserve admin-customized text.
    conn.execute(
        "UPDATE settings SET welcome_text=? WHERE welcome_text=?",
        (DEFAULT_WELCOME, LEGACY_DEFAULT_WELCOME),
    )
    conn.commit()
    return conn

def normalize_trigger(value):
    return value.casefold().strip().strip("!?.,:;()[]{}<> ").lstrip("@#").strip()

def list_shortcuts():
    with connect_db() as conn:
        return conn.execute(
            "SELECT id, trigger, response_text, response_html "
            "FROM shortcuts ORDER BY trigger COLLATE NOCASE"
        ).fetchall()

def get_shortcut(trigger):
    with connect_db() as conn:
        return conn.execute(
            "SELECT response_text, response_html FROM shortcuts "
            "WHERE trigger=? COLLATE NOCASE",
            (normalize_trigger(trigger),),
        ).fetchone()

def save_shortcut(trigger, response_text, response_html):
    normalized = normalize_trigger(trigger)
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO shortcuts(trigger, response_text, response_html) "
            "VALUES(?, ?, ?) "
            "ON CONFLICT(trigger) DO UPDATE SET "
            "response_text=excluded.response_text, response_html=excluded.response_html",
            (normalized, response_text, response_html),
        )

def delete_shortcut(shortcut_id):
    with connect_db() as conn:
        cursor = conn.execute("DELETE FROM shortcuts WHERE id=?", (shortcut_id,))
        return cursor.rowcount > 0

def _legacy_target_id(conn):
    row = conn.execute(
        "SELECT config_value FROM bot_config WHERE config_key='target_chat_id'"
    ).fetchone()
    if row and str(row[0]).lstrip("-").isdigit():
        return int(row[0])
    if TARGET_CHAT_ID.lstrip("-").isdigit():
        return int(TARGET_CHAT_ID)
    return None

def get_settings(chat_id=None):
    """Return the one global welcome template shared by every group."""
    with connect_db() as conn:
        row = conn.execute(
            "SELECT welcome_text, welcome_html, photo_file_id "
            "FROM settings WHERE chat_id=?",
            (GLOBAL_SETTINGS_ID,),
        ).fetchone()
        if row:
            return row[0], row[1], row[2]

        # Preserve the most recently edited rich template from the old per-chat setup.
        text_row = conn.execute(
            "SELECT welcome_text, welcome_html FROM settings "
            "WHERE chat_id != ? AND welcome_html IS NOT NULL "
            "ORDER BY rowid DESC LIMIT 1",
            (GLOBAL_SETTINGS_ID,),
        ).fetchone()
        if not text_row:
            text_row = conn.execute(
                "SELECT welcome_text, welcome_html FROM settings "
                "WHERE chat_id != ? AND welcome_text NOT IN (?, ?) "
                "ORDER BY rowid DESC LIMIT 1",
                (GLOBAL_SETTINGS_ID, DEFAULT_WELCOME, LEGACY_DEFAULT_WELCOME),
            ).fetchone()

        legacy_target = _legacy_target_id(conn)
        if not text_row and legacy_target is not None:
            text_row = conn.execute(
                "SELECT welcome_text, welcome_html FROM settings WHERE chat_id=?",
                (legacy_target,),
            ).fetchone()
        if not text_row:
            text_row = (DEFAULT_WELCOME, None)

        photo_row = None
        if legacy_target is not None:
            photo_row = conn.execute(
                "SELECT photo_file_id FROM settings "
                "WHERE chat_id=? AND photo_file_id IS NOT NULL",
                (legacy_target,),
            ).fetchone()
        if not photo_row:
            photo_row = conn.execute(
                "SELECT photo_file_id FROM settings "
                "WHERE chat_id != ? AND photo_file_id IS NOT NULL "
                "ORDER BY rowid DESC LIMIT 1",
                (GLOBAL_SETTINGS_ID,),
            ).fetchone()
        photo = photo_row[0] if photo_row else None

        conn.execute(
            "INSERT INTO settings"
            "(chat_id, welcome_text, welcome_html, photo_file_id) VALUES(?,?,?,?)",
            (GLOBAL_SETTINGS_ID, text_row[0], text_row[1], photo),
        )
        conn.commit()
        return text_row[0], text_row[1], photo

def save_settings(
    chat_id=None, welcome_text=None, welcome_html=None,
    photo_file_id=None, set_photo=False
):
    text, formatted, photo = get_settings()
    if welcome_text is not None:
        text = welcome_text
        formatted = welcome_html
    if set_photo:
        photo = photo_file_id
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO settings(chat_id, welcome_text, welcome_html, photo_file_id) "
            "VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "welcome_text=excluded.welcome_text, "
            "welcome_html=excluded.welcome_html, "
            "photo_file_id=excluded.photo_file_id",
            (GLOBAL_SETTINGS_ID, text, formatted, photo),
        )
        conn.commit()

def get_links(chat_id=None):
    """Return the one official-link set shared by every group."""
    with connect_db() as conn:
        row = conn.execute(
            "SELECT community_url, channel_url, x_url, website_url "
            "FROM links WHERE chat_id=?",
            (GLOBAL_SETTINGS_ID,),
        ).fetchone()
        if row:
            return row

        legacy_target = _legacy_target_id(conn)
        source = None
        if legacy_target is not None:
            source = conn.execute(
                "SELECT community_url, channel_url, x_url, website_url "
                "FROM links WHERE chat_id=?",
                (legacy_target,),
            ).fetchone()
        if not source:
            source = conn.execute(
                "SELECT community_url, channel_url, x_url, website_url "
                "FROM links WHERE chat_id < 0 ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        values = source or (COMMUNITY_URL, CHANNEL_URL, X_URL, WEBSITE_URL)
        conn.execute(
            "INSERT INTO links(chat_id, community_url, channel_url, x_url, website_url) "
            "VALUES(?,?,?,?,?)",
            (GLOBAL_SETTINGS_ID, *values),
        )
        conn.commit()
        return values

def save_link(chat_id, link_name, url):
    columns = {
        "group": "community_url",
        "channel": "channel_url",
        "x": "x_url",
        "website": "website_url",
    }
    column = columns.get(link_name)
    if not column:
        raise ValueError("Unknown link name")
    get_links()
    with connect_db() as conn:
        conn.execute(
            f"UPDATE links SET {column}=? WHERE chat_id=?",
            (url, GLOBAL_SETTINGS_ID),
        )
        conn.commit()

def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in ADMIN_IDS)

def remember_group(chat_id, make_target=False, title=None):
    """Record groups without changing the global welcome configuration."""
    if not isinstance(chat_id, int) or chat_id >= 0:
        return
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO bot_config(config_key, config_value) VALUES(?, ?) "
            "ON CONFLICT(config_key) DO UPDATE SET config_value=excluded.config_value",
            (f"group:{chat_id}", str(chat_id)),
        )
        if title:
            conn.execute(
                "INSERT INTO bot_config(config_key,config_value) VALUES(?,?) "
                "ON CONFLICT(config_key) DO UPDATE SET config_value=excluded.config_value",
                (f"group_title:{chat_id}", title),
            )
        conn.commit()

def settings_chat_id(update: Update):
    """All admin edits apply to every group through the global settings row."""
    return GLOBAL_SETTINGS_ID

def make_keyboard(chat_id=None):
    if chat_id is None:
        community_url, channel_url, x_url, website_url = (
            COMMUNITY_URL, CHANNEL_URL, X_URL, WEBSITE_URL
        )
    else:
        community_url, channel_url, x_url, website_url = get_links(chat_id)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("X ↗", url=x_url),
         InlineKeyboardButton("Channel ↗", url=channel_url),
         InlineKeyboardButton("Group ↗", url=community_url)],
        [InlineKeyboardButton("Website ↗", url=website_url)],
    ])

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Preview welcome", callback_data="admin_preview")],
        [InlineKeyboardButton("✏️ Edit welcome template", callback_data="admin_edit_text")],
        [InlineKeyboardButton("🔗 Official links", callback_data="admin_links")],
        [InlineKeyboardButton("🖼 Welcome image", callback_data="admin_image")],
        [InlineKeyboardButton("⚡ Custom shortcuts", callback_data="admin_shortcuts")],
        [InlineKeyboardButton("🏆 Message leaderboard", callback_data="stats_admin")],
    ])

def admin_shortcuts_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add shortcut", callback_data="admin_shortcut_add")],
        [InlineKeyboardButton("🗑 Delete shortcut", callback_data="admin_shortcut_delete_menu")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
    ])

def shortcut_delete_keyboard(shortcuts):
    rows = [
        [InlineKeyboardButton(
            f"🗑 {trigger[:40]}", callback_data=f"admin_shortcut_delete:{shortcut_id}"
        )]
        for shortcut_id, trigger, _, _ in shortcuts[:40]
    ]
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_shortcuts")])
    return InlineKeyboardMarkup(rows)

def shortcut_panel_text():
    shortcuts = list_shortcuts()
    lines = [
        "⚡ Custom shortcuts",
        "",
        "Add an exact word or phrase and its reply. It will work in every group.",
        "",
    ]
    if not shortcuts:
        lines.append("No custom shortcuts yet.")
    else:
        lines.append(f"Configured: {len(shortcuts)}")
        for _, trigger, response_text, _ in shortcuts[:30]:
            preview = response_text.replace("\n", " ")
            if len(preview) > 55:
                preview = preview[:52] + "..."
            lines.append(f"• {trigger} → {preview}")
        if len(shortcuts) > 30:
            lines.append(f"…and {len(shortcuts) - 30} more")
    return "\n".join(lines)

def admin_links_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("𝕏 X", callback_data="admin_edit_link:x"),
         InlineKeyboardButton("📢 Channel", callback_data="admin_edit_link:channel")],
        [InlineKeyboardButton("👥 Group", callback_data="admin_edit_link:group"),
         InlineKeyboardButton("🌐 Website", callback_data="admin_edit_link:website")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
    ])

def admin_image_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Upload new image", callback_data="admin_image_set")],
        [InlineKeyboardButton("🗑 Remove image", callback_data="admin_image_clear")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
    ])

def admin_panel_text():
    return (
        "🔐 JOBBY ADMIN\n\n"
        "Bot status: ONLINE\n"
        "Welcome messages: ON\n"
        "Publishing: ALL GROUPS ✅\n"
        f"Custom shortcuts: {len(list_shortcuts())}\n"
        f"Authorized admins: {len(ADMIN_IDS)}\n\n"
        "Edit once here; every group will use the same welcome."
    )

def render_welcome(welcome_text, welcome_html, member):
    name = member.full_name or member.first_name or "there"
    username = ("@" + member.username) if member.username else name
    if welcome_html:
        message = welcome_html.replace("{name}", html.escape(name)).replace(
            "{username}", html.escape(username)
        )
        return message, "HTML"
    message = welcome_text.replace("{name}", name).replace("{username}", username)
    return message, None

async def send_welcome(chat_id, member, context):
    # Telegram can report the same join through both a service message and
    # a chat-member update. Suppress the second event for the same member.
    now = time.monotonic()
    dedupe = context.bot_data.setdefault("welcome_dedupe", {})
    dedupe_key = (chat_id, member.id)
    last_sent = dedupe.get(dedupe_key)
    if last_sent is not None and now - last_sent < 45:
        log.info("Skipped duplicate welcome for member %s in chat %s", member.id, chat_id)
        return
    if len(dedupe) > 1000:
        expired = [key for key, sent_at in dedupe.items() if now - sent_at > 3600]
        for key in expired:
            dedupe.pop(key, None)

    welcome_text, welcome_html, photo_id = get_settings(chat_id)
    message, parse_mode = render_welcome(welcome_text, welcome_html, member)
    keyboard = make_keyboard(chat_id)
    try:
        if photo_id:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo_id,
                caption=message,
                parse_mode=parse_mode,
                reply_markup=keyboard,
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        dedupe[dedupe_key] = time.monotonic()
    except Exception:
        log.exception("Could not send welcome message in chat %s", chat_id)

async def new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    remember_group(message.chat_id, title=message.chat.title)
    for member in message.new_chat_members or []:
        if context.bot and member.id == context.bot.id:
            continue
        await send_welcome(message.chat_id, member, context)

async def member_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    change = update.chat_member
    chat = update.effective_chat
    if not change or not chat:
        return
    remember_group(chat.id, title=chat.title)
    old = change.old_chat_member
    new = change.new_chat_member
    if old.status in ("left", "kicked") and new.status in (
        "member", "administrator", "creator"
    ):
        if context.bot and new.user.id != context.bot.id:
            await send_welcome(chat.id, new.user, context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    if is_admin(update):
        context.user_data.pop("admin_action", None)
        await message.reply_text(admin_panel_text(), reply_markup=admin_keyboard())
        return
    await message.reply_text(
        "Welcome to JOBBY Welcome Bot.\n\n"
        "Use the buttons below to open our community and official pages.",
        reply_markup=make_keyboard(),
        disable_web_page_preview=True,
    )

async def send_admin_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = settings_chat_id(update)
    if chat_id is None:
        await query.message.reply_text(
            "Global welcome settings are ready for all groups."
        )
        return
    welcome_text, welcome_html, photo_id = get_settings(chat_id)
    preview, parse_mode = render_welcome(
        welcome_text, welcome_html, query.from_user
    )
    keyboard = make_keyboard(chat_id)
    if photo_id:
        await query.message.reply_photo(
            photo=photo_id,
            caption=preview,
            parse_mode=parse_mode,
            reply_markup=keyboard,
        )
    else:
        await query.message.reply_text(
            preview,
            parse_mode=parse_mode,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""

    if data == "show_myid":
        await query.message.reply_text(f"Your Telegram user ID: {query.from_user.id}")
        return

    if not data.startswith("admin_"):
        return
    if query.from_user.id not in ADMIN_IDS:
        await query.message.reply_text(
            "These controls are available to configured bot admins only. Use /myid to check your ID."
        )
        return

    if data in ("admin_controls", "admin_back"):
        context.user_data.pop("admin_action", None)
        await query.message.reply_text(admin_panel_text(), reply_markup=admin_keyboard())
    elif data == "admin_preview":
        await send_admin_preview(update, context)
    elif data == "admin_shortcuts":
        context.user_data.pop("admin_action", None)
        await query.message.reply_text(
            shortcut_panel_text(), reply_markup=admin_shortcuts_keyboard()
        )
    elif data == "admin_shortcut_add":
        context.user_data["admin_action"] = "shortcut_trigger"
        context.user_data.pop("new_shortcut_trigger", None)
        await query.message.reply_text(
            "➕ Add shortcut\n\n"
            "Send the exact word or phrase members will type in a group.\n"
            "Example: discord\n\nSend /cancel to stop."
        )
    elif data == "admin_shortcut_delete_menu":
        shortcuts = list_shortcuts()
        if not shortcuts:
            await query.message.reply_text(
                "No custom shortcuts to delete.", reply_markup=admin_shortcuts_keyboard()
            )
        else:
            await query.message.reply_text(
                "Choose a shortcut to delete:",
                reply_markup=shortcut_delete_keyboard(shortcuts),
            )
    elif data.startswith("admin_shortcut_delete:"):
        try:
            shortcut_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        deleted = delete_shortcut(shortcut_id)
        await query.message.reply_text(
            "Shortcut deleted ✅" if deleted else "Shortcut was already deleted.",
            reply_markup=admin_shortcuts_keyboard(),
        )
    elif data == "admin_edit_text":
        context.user_data["admin_action"] = "edit_welcome"
        await query.message.reply_text(
            "✏️ Edit the full welcome template\n\n"
            "Send the complete message exactly as you want it to appear.\n"
            "Use {name} for the member name and {username} for their username.\n\n"
            "Send /cancel to stop."
        )
    elif data == "admin_links":
        chat_id = settings_chat_id(update)
        if chat_id is None:
            await query.message.reply_text("Global welcome settings are ready for all groups.")
            return
        group_url, channel_url, x_url, website_url = get_links(chat_id)
        await query.message.reply_text(
            "🔗 Official links\n\n"
            f"X: {x_url}\n"
            f"Channel: {channel_url}\n"
            f"Group: {group_url}\n"
            f"Website: {website_url}\n\n"
            "Choose the link you want to edit:",
            reply_markup=admin_links_keyboard(),
            disable_web_page_preview=True,
        )
    elif data.startswith("admin_edit_link:"):
        link_name = data.split(":", 1)[1]
        labels = {
            "x": "X",
            "channel": "Channel",
            "group": "Group",
            "website": "Website",
        }
        if link_name not in labels:
            return
        context.user_data["admin_action"] = f"edit_link:{link_name}"
        await query.message.reply_text(
            f"Send the new {labels[link_name]} URL.\n\nSend /cancel to stop."
        )
    elif data == "admin_image":
        chat_id = settings_chat_id(update)
        if chat_id is None:
            await query.message.reply_text("Global welcome settings are ready for all groups.")
            return
        _, _, photo_id = get_settings(chat_id)
        status = "SET ✅" if photo_id else "NOT SET"
        await query.message.reply_text(
            f"🖼 Welcome image\n\nCurrent status: {status}",
            reply_markup=admin_image_keyboard(),
        )
    elif data == "admin_image_set":
        context.user_data["admin_action"] = "set_image"
        await query.message.reply_text(
            "📤 Send the new welcome image now.\n\nSend /cancel to stop."
        )
    elif data == "admin_image_clear":
        chat_id = settings_chat_id(update)
        if chat_id is None:
            await query.message.reply_text("Global welcome settings are ready for all groups.")
            return
        save_settings(chat_id, photo_file_id=None, set_photo=True)
        context.user_data.pop("admin_action", None)
        await query.message.reply_text(
            "Welcome image removed.", reply_markup=admin_keyboard()
        )

async def cancel_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
    context.user_data.pop("admin_action", None)
    await update.effective_message.reply_text(
        "Action cancelled.", reply_markup=admin_keyboard() if is_admin(update) else None
    )

async def admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not is_admin(update):
        return
    action = context.user_data.get("admin_action")
    if not action:
        return
    value = (message.text or "").strip()
    if action == "shortcut_trigger":
        trigger = normalize_trigger(value)
        if not trigger or "\n" in trigger:
            await message.reply_text("Send one word or phrase on a single line.")
            return
        if len(trigger) > 60:
            await message.reply_text("Please keep the shortcut under 60 characters.")
            return
        context.user_data["new_shortcut_trigger"] = trigger
        context.user_data["admin_action"] = "shortcut_response"
        await message.reply_text(
            f"Shortcut: {trigger}\n\n"
            "Now send the reply exactly as you want it to appear. "
            "Formatting and animated emoji are supported.\n\nSend /cancel to stop."
        )
    elif action == "shortcut_response":
        trigger = context.user_data.get("new_shortcut_trigger")
        if not trigger:
            context.user_data.pop("admin_action", None)
            await message.reply_text(
                "The shortcut setup expired. Start again from Custom shortcuts.",
                reply_markup=admin_keyboard(),
            )
            return
        if not value:
            await message.reply_text("The reply cannot be empty.")
            return
        if len(value) > 1000:
            await message.reply_text("Please keep the reply under 1000 characters.")
            return
        formatted = (message.text_html or value).strip()
        save_shortcut(trigger, value, formatted)
        context.user_data.pop("admin_action", None)
        context.user_data.pop("new_shortcut_trigger", None)
        await message.reply_text(
            f"Shortcut “{trigger}” saved for all groups ✅",
            reply_markup=admin_shortcuts_keyboard(),
        )
    elif action == "edit_welcome":
        if len(value) > 1000:
            await message.reply_text("Please keep the welcome message under 1000 characters.")
            return
        chat_id = settings_chat_id(update)
        if chat_id is None:
            await message.reply_text("Global welcome settings are ready for all groups.")
            return
        formatted = (message.text_html or value).strip()
        save_settings(chat_id, welcome_text=value, welcome_html=formatted)
        context.user_data.pop("admin_action", None)
        await message.reply_text(
            "Welcome template saved ✅", reply_markup=admin_keyboard()
        )
    elif action.startswith("edit_link:"):
        if not value.startswith(("https://", "http://")):
            await message.reply_text("Please send a complete URL beginning with https://")
            return
        chat_id = settings_chat_id(update)
        if chat_id is None:
            await message.reply_text("Global welcome settings are ready for all groups.")
            return
        link_name = action.split(":", 1)[1]
        save_link(chat_id, link_name, value)
        context.user_data.pop("admin_action", None)
        await message.reply_text(
            "Official link saved ✅", reply_markup=admin_keyboard()
        )

async def admin_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not is_admin(update):
        return
    if context.user_data.get("admin_action") != "set_image":
        return
    chat_id = settings_chat_id(update)
    if chat_id is None:
        await message.reply_text("Global welcome settings are ready for all groups.")
        return
    save_settings(chat_id, photo_file_id=message.photo[-1].file_id, set_photo=True)
    context.user_data.pop("admin_action", None)
    await message.reply_text(
        "Welcome image saved ✅", reply_markup=admin_keyboard()
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
        await update.effective_message.reply_text("Global welcome settings are ready for all groups.")
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
        await update.effective_message.reply_text("Global welcome settings are ready for all groups.")
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
        await update.effective_message.reply_text("Global welcome settings are ready for all groups.")
        return
    save_settings(chat_id, photo_file_id=None, set_photo=True)
    await update.effective_message.reply_text("Welcome image removed for this group.")

async def quick_text_triggers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to exact social-link shortcuts in group chats."""
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or chat.type not in ("group", "supergroup"):
        return
    remember_group(chat.id, title=chat.title)
    raw = (message.text or "").strip()
    if not raw:
        return
    key = normalize_trigger(raw)
    custom_shortcut = get_shortcut(key)
    if custom_shortcut:
        response_text, response_html = custom_shortcut
        await message.reply_text(
            response_html or response_text,
            parse_mode="HTML" if response_html else None,
            disable_web_page_preview=True,
        )
        return

    COMMUNITY_URL, CHANNEL_URL, X_URL, WEBSITE_URL = get_links(chat.id)
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
    app.add_handler(CommandHandler("cancel", cancel_admin_action))
    message_stats_ui.register(app, connect_db, remember_group, ADMIN_IDS)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.PHOTO, admin_photo_input
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, admin_text_input
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quick_text_triggers))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, new_members))
    app.add_handler(ChatMemberHandler(member_status, ChatMemberHandler.CHAT_MEMBER))
    log.info("JOBBY welcome bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)

if __name__ == "__main__":
    main()
