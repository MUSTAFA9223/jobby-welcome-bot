# JOBBY Welcome Bot

A Telegram group welcome bot for JOBBY. It greets new members by display name, optionally attaches a welcome image, and provides buttons for the community, channel, and X account.

## Deploy on Railway

1. Create a Railway service from this GitHub repository and deploy the `main` branch.
2. Add `TELEGRAM_BOT_TOKEN` in Railway Variables. Never commit the token to GitHub or send it in chat.
3. Set `ADMIN_IDS` to comma-separated numeric Telegram user IDs. Use `/myid` in a private chat with the bot to find an ID, then add it to Railway Variables and redeploy.
4. Add a Railway Volume mounted at `/data`, and keep `DB_PATH=/data/jobby-welcome-bot.sqlite3` so per-group settings survive redeploys.
5. Add the bot to the group and grant permission to send messages.
6. **For message counts**, allow the bot to read all group messages: make it a group administrator or disable Group Privacy through @BotFather (`/setprivacy`), then remove and re-add the bot if required. Otherwise it may only see commands and replies.
7. Message totals are recorded from the time this version starts receiving group messages; Telegram does not provide older group history to bots.

## Variables

- `TELEGRAM_BOT_TOKEN` (required)
- `ADMIN_IDS` (comma-separated Telegram numeric IDs)
- `DB_PATH` (defaults to `/data/jobby-welcome-bot.sqlite3`)
- `COMMUNITY_URL`, `CHANNEL_URL`, `X_URL` (defaults are JOBBY links)
- `WEBSITE_URL` (optional; leave empty until the official site URL is confirmed)

## Admin commands

- `/myid` — display your Telegram user ID.
- `/setwelcome Welcome, {name}! ...` — set the welcome message for this group. `{name}` and `{username}` placeholders are supported.
- Reply to a photo with `/setphoto` — set the welcome image for this group.
- `/clearphoto` — clear this group's welcome image.
- `/help` — list commands.

The bot uses long polling and Railway's restart-on-failure policy. This reduces downtime but cannot guarantee uninterrupted operation. Configure `ADMIN_IDS` before relying on admin commands.

## Message counts and leaderboard

- `/stats` (in a group) — see your messages today (UTC), over the last 7 days, this month, and all time, plus your position.
- Reply to a member's message with `/stats` to inspect that member's count.
- `/top` — on-demand leaderboard of **all recorded members** in this group, with names, usernames, message counts and ranking. It is **only posted when a member types `/top`**; it is never posted automatically and has no separate admin-panel trigger. Use the period and page buttons on that message to navigate.

Counts are **independent for each group**. The bot counts new text and media messages from human users; bot messages, Telegram service events, and message edits are excluded. Duplicate message deliveries are ignored through a persistent ID table. Time windows use UTC, and weekly totals cover today plus the six preceding calendar days. No retroactive counting is possible.

**Persistence:** the `message_counts`, `message_counts_daily` and `counted_message_ids` tables are in the same SQLite database as the welcome settings. Attach a persistent Railway volume to `/data` before deploying, otherwise a restart may erase counts. Existing welcome text, image and shortcuts are preserved.
