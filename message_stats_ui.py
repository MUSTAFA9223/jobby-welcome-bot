"""Telegram commands and admin controls for complete, formatted message leaderboards."""
import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

import message_stats

# Telegram text messages have a 4096-character limit. Keep a safe margin for
# HTML tags, display names and optional animated emoji entities.
MAX_BOARD_CHARS = 3400

# Public Telegram custom-emoji IDs; ordinary emoji are always usable as fallback.
# Animated custom emoji appears only when Telegram allows this bot to send it.
TOP_MEDALS = (
    ("🏆", "5409008750893734809"),
    ("🥈", "5447203607294265305"),
    ("🥉", "5453902265922376865"),
)


def medal(position, animated=True):
    if position > 3:
        return f"{position}."
    fallback, custom_id = TOP_MEDALS[position - 1]
    if animated:
        return f'<tg-emoji emoji-id="{custom_id}">{fallback}</tg-emoji>'
    return fallback


def periods(prefix="stats_lb", group_id=None, period="all", page=0, page_count=1):
    def payload(p, index=0):
        if group_id is None:
            return f"{prefix}:{p}:{index}"
        return f"{prefix}:{group_id}:{p}:{index}"

    rows = [[
        InlineKeyboardButton("📅 Today", callback_data=payload("today")),
        InlineKeyboardButton("📈 7 days", callback_data=payload("week")),
        InlineKeyboardButton("🏆 All time", callback_data=payload("all")),
    ]]
    if page_count > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=payload(period, page - 1)))
        nav.append(InlineKeyboardButton(f"{page + 1}/{page_count}", callback_data=payload(period, page)))
        if page < page_count - 1:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=payload(period, page + 1)))
        rows.append(nav)
    if group_id is not None:
        rows.append([InlineKeyboardButton("⬅️ Groups", callback_data="stats_admin")])
    return InlineKeyboardMarkup(rows)


def build_pages(records, period="all", title=None, summary=None, animated=True):
    periods_text = {
        "all": "All time", "today": "Today (UTC)",
        "week": "Last 7 days (UTC)"
    }
    group_name = f" — {html.escape(title)}" if title else ""
    heading = (
        f"<b>🏆 JOBBY MESSAGE LEADERBOARD{group_name}</b>\n"
        f"<b>📊 {periods_text.get(period, 'All time')}</b>"
    )
    if summary:
        heading += f"\n<b>{html.escape(summary)}</b>"
    footer = "<i>Counting began when message tracking was enabled.</i>"
    rows = []
    for position, (uid, name, username, count) in enumerate(records, 1):
        safe_name = html.escape(name or username or str(uid))
        handle = f" (@{html.escape(username)})" if username else ""
        rows.append(
            f'<b>{medal(position, animated)} '
            f'<a href="tg://user?id={uid}">{safe_name}</a>'
            f'{handle} — {count:,} messages</b>'
        )
    if not rows:
        rows = ["<b>No messages recorded for this period yet.</b>"]

    pages = []
    current = []
    length = len(heading) + len(footer) + 90
    for row in rows:
        if current and length + len(row) + 2 > MAX_BOARD_CHARS:
            pages.append(current)
            current = []
            length = len(heading) + len(footer) + 90
        current.append(row)
        length += len(row) + 2
    if current:
        pages.append(current)

    result = []
    for idx, batch in enumerate(pages):
        page_label = f"\n<b>Page {idx + 1} of {len(pages)}</b>" if len(pages) > 1 else ""
        result.append(f"{heading}{page_label}\n\n" + "\n".join(batch) + f"\n\n{footer}")
    return result


class StatsUI:
    def __init__(self, connect_db, remember_group, admin_ids):
        self.connect_db = connect_db
        self.remember_group = remember_group
        self.admin_ids = admin_ids

    def known_groups(self):
        with self.connect_db() as conn:
            rows = conn.execute(
                "SELECT config_key,config_value FROM bot_config "
                "WHERE config_key LIKE 'group:%' OR config_key LIKE 'group_title:%'"
            ).fetchall()
        titles = {key.split(":", 1)[1]: val for key, val in rows
                  if key.startswith("group_title:")}
        ids = sorted({val for key, val in rows if key.startswith("group:")})
        return [(int(gid), titles.get(gid, f"Group {gid}")) for gid in ids
                if gid.lstrip("-").isdigit()]

    def board(self, chat_id, period="all", page=0, title=None, admin=False, animated=True):
        with self.connect_db() as conn:
            records = message_stats.leaders(conn, chat_id, period)
            members, total = message_stats.summary(conn, chat_id) if admin else (0, 0)
        summary = f"{members:,} tracked members · {total:,} total messages" if admin else None
        pages = build_pages(records, period, title, summary, animated)
        page = max(0, min(page, len(pages) - 1))
        return pages[page], page, len(pages)

    async def send_board(self, message, chat_id, period="all", page=0,
                         group_id=None, title=None, admin=False):
        text, page, count = self.board(chat_id, period, page, title, admin)
        markup = periods("stats_group" if admin else "stats_lb", group_id,
                         period, page, count)
        try:
            await message.reply_text(
                text, parse_mode="HTML", reply_markup=markup,
                disable_web_page_preview=True,
            )
        except BadRequest:
            if "<tg-emoji" not in text:
                raise
            # Some Telegram bots cannot send custom emoji: retry with static medals.
            text, page, count = self.board(chat_id, period, page, title, admin, False)
            markup = periods("stats_group" if admin else "stats_lb", group_id,
                             period, page, count)
            await message.reply_text(
                text, parse_mode="HTML", reply_markup=markup,
                disable_web_page_preview=True,
            )

    async def edit_board(self, query, chat_id, period="all", page=0,
                         group_id=None, title=None, admin=False):
        text, page, count = self.board(chat_id, period, page, title, admin)
        markup = periods("stats_group" if admin else "stats_lb", group_id,
                         period, page, count)
        try:
            await query.edit_message_text(
                text, parse_mode="HTML", reply_markup=markup,
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return
            if "<tg-emoji" not in text:
                raise
            text, page, count = self.board(chat_id, period, page, title, admin, False)
            markup = periods("stats_group" if admin else "stats_lb", group_id,
                             period, page, count)
            try:
                await query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=markup,
                    disable_web_page_preview=True,
                )
            except BadRequest as retry_exc:
                if "not modified" not in str(retry_exc).lower():
                    raise

    async def count(self, update, context):
        # Edited messages are excluded: they don't contain update.message.
        message, chat = update.message, update.effective_chat
        if not message or not chat or chat.type not in ("group", "supergroup"):
            return
        self.remember_group(chat.id, title=chat.title)
        with self.connect_db() as conn:
            message_stats.record(conn, chat.id, message)

    async def top(self, update, context):
        message, chat = update.effective_message, update.effective_chat
        if not message or not chat or chat.type not in ("group", "supergroup"):
            return
        self.remember_group(chat.id, title=chat.title)
        await self.send_board(message, chat.id)

    async def personal(self, update, context):
        message, chat = update.effective_message, update.effective_chat
        if not message or not chat or chat.type not in ("group", "supergroup"):
            return
        replied = message.reply_to_message
        member = replied.from_user if replied and replied.from_user else update.effective_user
        if not member or member.is_bot:
            return
        with self.connect_db() as conn:
            result = message_stats.personal(conn, chat.id, member.id)
        if result is None:
            await message.reply_text("No messages have been recorded for this member yet.")
            return
        name, total, today, week, month, rank = result
        await message.reply_text(
            f"📊 Message statistics for {html.escape(name)}\n\n"
            f"💬 Total: {total:,}\n📅 Today (UTC): {today:,}\n"
            f"📈 Last 7 days: {week:,}\n🗓 Month (UTC): {month:,}\n"
            f"🏅 Group rank: #{rank}\n\n"
            "Only messages sent since activation are counted.",
            parse_mode="HTML",
        )

    async def callbacks(self, update, context):
        query = update.callback_query
        if not query:
            return
        await query.answer()
        action = query.data or ""
        if action.startswith("stats_lb:"):
            pieces = action.split(":")
            if len(pieces) not in (2, 3):
                return
            period = pieces[1]
            try:
                page = int(pieces[2]) if len(pieces) == 3 else 0
            except ValueError:
                return
            chat = query.message.chat if query.message else None
            if period not in ("all", "today", "week") or not chat:
                return
            if chat.type not in ("group", "supergroup"):
                return
            await self.edit_board(query, chat.id, period, page)
            return

        if query.from_user.id not in self.admin_ids:
            await query.message.reply_text("Admin access required.")
            return
        if action == "stats_admin":
            groups = self.known_groups()
            if not groups:
                await query.message.reply_text(
                    "No groups tracked yet. Add the bot and send messages in a group."
                )
                return
            buttons = [
                [InlineKeyboardButton(label[:48],
                 callback_data=f"stats_group:{gid}:all:0")]
                for gid, label in groups[:40]
            ]
            buttons.append(
                [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]
            )
            await query.message.reply_text(
                "🏆 Choose a group for its message leaderboard:",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return
        if action.startswith("stats_group:"):
            parts = action.split(":")
            if len(parts) not in (3, 4):
                return
            try:
                chat_id = int(parts[1])
                page = int(parts[3]) if len(parts) == 4 else 0
            except ValueError:
                return
            period = parts[2]
            if period not in ("all", "today", "week"):
                return
            groups = dict(self.known_groups())
            if chat_id not in groups:
                await query.message.reply_text("Group is not registered.")
                return
            await self.edit_board(
                query, chat_id, period, page, chat_id,
                groups[chat_id], True,
            )


def register(app, connect_db, remember_group, admin_ids):
    handlers = StatsUI(connect_db, remember_group, admin_ids)
    app.add_handler(CommandHandler("top", handlers.top))
    app.add_handler(CommandHandler(["stats", "mystats"], handlers.personal))
    app.add_handler(CallbackQueryHandler(
        handlers.callbacks, pattern=r"^stats_lb:"
    ))
    # Independent group ensures shortcut reply handlers still run.
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND, handlers.count
    ), group=-1)
