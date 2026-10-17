"""Persistent, per-group Telegram message statistics (UTC)."""
from datetime import datetime, timedelta, timezone


def ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_counts (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            display_name TEXT NOT NULL,
            username TEXT,
            total INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_counts_daily (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            day_utc TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id, day_utc)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS counted_message_ids (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        )
    """)


def start_day(period):
    today = datetime.now(timezone.utc).date()
    if period == "today":
        return today.isoformat()
    if period == "week":
        return (today - timedelta(days=6)).isoformat()
    if period == "month":
        return today.replace(day=1).isoformat()
    return None


def record(conn, chat_id, message):
    """Count one original user-authored message, with restart-safe deduplication."""
    user = message.from_user
    if not user or user.is_bot or message.sender_chat is not None:
        return False
    # Exclude joins, pins and other Telegram service messages.
    if not any((
        message.text, message.photo, message.video, message.animation,
        message.sticker, message.document, message.audio, message.voice,
        message.video_note, message.contact, message.location, message.venue,
        message.poll, message.dice, message.game
    )):
        return False
    inserted = conn.execute(
        "INSERT OR IGNORE INTO counted_message_ids(chat_id,message_id) VALUES (?,?)",
        (chat_id, message.message_id),
    )
    if inserted.rowcount != 1:
        return False
    day = message.date.astimezone(timezone.utc).date().isoformat()
    conn.execute(
        "INSERT INTO message_counts(chat_id,user_id,display_name,username,total) "
        "VALUES(?,?,?,?,1) ON CONFLICT(chat_id,user_id) DO UPDATE SET "
        "display_name=excluded.display_name,username=excluded.username,"
        "total=message_counts.total+1",
        (chat_id, user.id, user.full_name or user.first_name or str(user.id),
         user.username),
    )
    conn.execute(
        "INSERT INTO message_counts_daily(chat_id,user_id,day_utc,count) "
        "VALUES(?,?,?,1) ON CONFLICT(chat_id,user_id,day_utc) DO UPDATE SET "
        "count=message_counts_daily.count+1",
        (chat_id, user.id, day),
    )
    return True


def leaders(conn, chat_id, period="all", limit=None):
    """Return every tracked sender by default; optionally restrict row count."""
    since = start_day(period)
    if since is None:
        query = (
            "SELECT user_id,display_name,username,total FROM message_counts "
            "WHERE chat_id=? AND total>0 ORDER BY total DESC,user_id"
        )
        params = [chat_id]
    else:
        query = (
            "SELECT c.user_id,c.display_name,c.username,SUM(d.count) AS n "
            "FROM message_counts_daily d JOIN message_counts c "
            "ON c.chat_id=d.chat_id AND c.user_id=d.user_id "
            "WHERE d.chat_id=? AND d.day_utc>=? "
            "GROUP BY c.user_id,c.display_name,c.username "
            "ORDER BY n DESC,c.user_id"
        )
        params = [chat_id, since]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return conn.execute(query, params).fetchall()


def personal(conn, chat_id, user_id):
    row = conn.execute(
        "SELECT display_name,total FROM message_counts "
        "WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    if row is None:
        return None
    day = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN day_utc=? THEN count ELSE 0 END),0),"
        "COALESCE(SUM(CASE WHEN day_utc>=? THEN count ELSE 0 END),0),"
        "COALESCE(SUM(CASE WHEN day_utc>=? THEN count ELSE 0 END),0) "
        "FROM message_counts_daily WHERE chat_id=? AND user_id=?",
        (start_day("today"), start_day("week"), start_day("month"),
         chat_id, user_id),
    ).fetchone()
    rank = conn.execute(
        "SELECT COUNT(*)+1 FROM message_counts WHERE chat_id=? AND total>?",
        (chat_id, row[1]),
    ).fetchone()[0]
    return (row[0], row[1], *day, rank)


def summary(conn, chat_id):
    return conn.execute(
        "SELECT COUNT(*),COALESCE(SUM(total),0) "
        "FROM message_counts WHERE chat_id=?",
        (chat_id,),
    ).fetchone()
