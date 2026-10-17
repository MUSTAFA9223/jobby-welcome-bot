"""Telegram commands and admin controls for per-group message leaderboards."""
import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

import message_stats


def periods(prefix="stats_lb", group_id=None):
    def payload(p):
        return f"{prefix}:{group_id}:{p}" if group_id is not None else f"{prefix}:{p}"
    rows = [[
        InlineKeyboardButton("📅 Today", callback_data=payload("today")),
        InlineKeyboardButton("📈 7 days", callback_data=payload("week")),
        InlineKeyboardButton("🏆 All time", callback_data=payload("all")),
    ]]
    if group_id is not None:
        rows.append([InlineKeyboardButton("⬅️ Groups", callback_data="stats_admin")])
    return InlineKeyboardMarkup(rows)


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

    def board(self, chat_id, period="all", title=None):
        with self.connect_db() as conn:
            records = message_stats.leaders(conn, chat_id, period)
        periods_text = {"all": "All time", "today": "Today (UTC)",
                        "week": "Last 7 days (UTC)"}
        suffix = f" — {html.escape(title)}" if title else ""
        lines = [f"🏆 JOBBY Message Leaderboard{suffix}",
                 f"📊 {periods_text.get(period, 'All time')}", ""]
        for position, (uid, name, username, n) in enumerate(records, 1):
            label = html.escape(name or username or str(uid))
            lines.append(
                f'{position}. <a href="tg://user?id={uid}">{label}</a> — {n:,} messages'
            )
        if not records:
            lines.append("No messages counted for this period yet.")
        lines += ["", "Counting began when this feature was activated."]
        return "\n".join(lines)

    async def count(self, update, context):
        # update.message is absent on edited messages, so edits are not counted.
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
        await message.reply_text(
            self.board(chat.id), parse_mode="HTML", reply_markup=periods(),
            disable_web_page_preview=True,
        )

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
            period = action.split(":", 1)[1]
            chat = query.message.chat if query.message else None
            if period not in ("all", "today", "week") or not chat:
                return
            if chat.type not in ("group", "supergroup"):
                return
            try:
                await query.edit_message_text(
                    self.board(chat.id, period), parse_mode="HTML",
                    reply_markup=periods(), disable_web_page_preview=True,
                )
            except BadRequest as exc:
                if "not modified" not in str(exc).lower():
                    raise
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
                 callback_data=f"stats_group:{gid}:all")]
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
            try:
                chat_id, period = action[len("stats_group:"):].split(":", 1)
                chat_id = int(chat_id)
            except (ValueError, TypeError):
                return
            if period not in ("all", "today", "week"):
                return
            groups = dict(self.known_groups())
            if chat_id not in groups:
                await query.message.reply_text("Group is not registered.")
                return
            with self.connect_db() as conn:
                members, total = message_stats.summary(conn, chat_id)
            await query.message.reply_text(
                f"📊 {members:,} members · {total:,} messages\n\n"
                + self.board(chat_id, period, groups[chat_id]),
                parse_mode="HTML",
                reply_markup=periods("stats_group", chat_id),
                disable_web_page_preview=True,
            )


def register(app, connect_db, remember_group, admin_ids):
    handlers = StatsUI(connect_db, remember_group, admin_ids)
    app.add_handler(CommandHandler(["top", "rank", "leaderboard"], handlers.top))
    app.add_handler(CommandHandler(["stats", "mystats"], handlers.personal))
    app.add_handler(CallbackQueryHandler(
        handlers.callbacks, pattern=r"^(stats_lb:|stats_admin$|stats_group:)"
    ))
    # Independent group ensures shortcut reply handlers still run.
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND, handlers.count
    ), group=-1)
