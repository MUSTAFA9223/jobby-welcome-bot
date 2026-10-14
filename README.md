# JOBBY Welcome Bot

A Telegram group welcome bot for JOBBY. It greets new members by display name, optionally attaches a welcome image, and provides buttons for the community, channel, and X account.

## Deploy on Railway

1. Create a Railway service from this GitHub repository and deploy the `main` branch.
2. Add `TELEGRAM_BOT_TOKEN` in Railway Variables. Never commit the token to GitHub or send it in chat.
3. Set `ADMIN_IDS` to comma-separated numeric Telegram user IDs. Use `/myid` in a private chat with the bot to find an ID, then add it to Railway Variables and redeploy.
4. Add a Railway Volume mounted at `/data`, and keep `DB_PATH=/data/jobby-welcome-bot.sqlite3` so per-group settings survive redeploys.
5. Add the bot to the group and grant permission to send messages.

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
