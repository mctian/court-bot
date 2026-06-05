"""
Pure logic layer — no Discord imports, safe to unit-test directly.
"""

import hashlib
import json
import random
import re
import sqlite3
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────
SLOT_DURATION_MINS        = 45
NOTIFY_WINDOW_SECS        = 150
LOOP_INTERVAL_SECS        = 20
PACIFIC                   = ZoneInfo("America/Los_Angeles")
MIDNIGHT_PT               = dt_time(hour=0, minute=0, second=0, tzinfo=PACIFIC)

MAX_ACTIVE_RESERVATIONS   = 50
MAX_PASSWORDS             = 50
MAX_GROUPS_IN_FRONT       = 20
MAX_COURT_NUMBER          = 99
MAX_CREDENTIAL_LEN        = 100
REGISTER_COOLDOWN_SECS    = 1
WRITE_COOLDOWN_SECS       = 1
MAX_USERS_PER_RESERVATION = 4

DB_PATH = "court_bot.db"

# ─────────────────────────────────────────────────────────────
# Pet Constants
# ─────────────────────────────────────────────────────────────
# Ordered from most common/simple → rarest/legendary.
# Tier N requires cumulative XP = 10 * N * (N+1): 0, 20, 60, 120, 200, 300 …
PET_TIERS = [
    # ── Tier 0-9: tiny, common, abundant ─────────────────────────────
    "🐛",  # 0  Caterpillar   — very common, small, low cuteness
    "🐜",  # 1  Ant           — extremely common, tiny
    "🐌",  # 2  Snail         — common, small, mildly cute
    "🐞",  # 3  Ladybug       — common, small, a bit cute
    "🐝",  # 4  Bee           — common, small, moderately cute
    "🌱",  # 5  Seedling      — abundant, tiny, surprisingly cute
    "🦗",  # 6  Cricket       — common, small, low cuteness
    "🐟",  # 7  Fish          — very common, small, mild cuteness
    "🐠",  # 8  Tropical Fish — common, small, colorful/cute
    "🐡",  # 9  Blowfish      — common, small, goofy-cute
    # ── Tier 10-19: small, somewhat rare, cuter ──────────────────────
    "🐭",  # 10 Mouse         — common, small, cute
    "🐹",  # 11 Hamster       — common, small, very cute
    "🐸",  # 12 Frog          — common, small, cute
    "🐔",  # 13 Chicken       — common, medium-small, mild cuteness
    "🐦",  # 14 Bird          — common, small, moderate cuteness
    "🦆",  # 15 Duck          — common, small-medium, cute
    "🦔",  # 16 Hedgehog      — somewhat rare, small, very cute
    "🐿️", # 17 Chipmunk      — somewhat rare, small, very cute
    "🐇",  # 18 Rabbit        — common, small-medium, very cute
    "🐧",  # 19 Penguin       — moderately rare, small, very cute
    # ── Tier 20-29: medium, rarer, cute & charismatic ────────────────
    "🐱",  # 20 Cat           — common, medium, very cute
    "🐶",  # 21 Dog           — common, medium, very cute
    "🦊",  # 22 Fox           — moderately rare, medium, very cute
    "🦝",  # 23 Raccoon       — moderately rare, medium, cute
    "🐨",  # 24 Koala         — rare, medium, extremely cute
    "🐼",  # 25 Panda         — rare, medium-large, extremely cute
    "🦦",  # 26 Otter         — rare, small-medium, extremely cute
    "🦥",  # 27 Sloth         — rare, medium, oddly very cute
    "🦘",  # 28 Kangaroo      — rare, large, cute
    "🐮",  # 29 Cow           — common, large, mild cuteness
    # ── Tier 30-39: large, rarer, impressive ─────────────────────────
    "🦁",  # 30 Lion          — rare, large, majestic
    "🐯",  # 31 Tiger         — rare, large, majestic + cute
    "🐺",  # 32 Wolf          — rare, large, majestic
    "🐻",  # 33 Bear          — rare, large, somewhat cute
    "🦅",  # 34 Eagle         — rare, large, majestic
    "🦉",  # 35 Owl           — rare, medium, very cute + wise
    "🦚",  # 36 Peacock       — rare, large, spectacular
    "🦩",  # 37 Flamingo      — rare, large, elegant + cute
    "🦜",  # 38 Parrot        — rare, medium, very cute + colorful
    "🦋",  # 39 Butterfly     — rare, small, ethereally beautiful
    # ── Tier 40-47: exotic, very rare, legendary ─────────────────────
    "🐬",  # 40 Dolphin       — rare, large, very cute + intelligent
    "🐙",  # 41 Octopus       — rare, medium, alien-cute
    "🐋",  # 42 Whale         — very rare, enormous, majestic
    "🦈",  # 43 Shark         — very rare, large, fierce
    "🦕",  # 44 Sauropod      — extremely rare (extinct), huge, gentle-cute
    "🦖",  # 45 T-Rex         — extremely rare (extinct), huge, fierce
    "🦄",  # 46 Unicorn       — legendary, medium, magical + very cute
    "🐉",  # 47 Dragon        — legendary, enormous, ultimate
]

FOOD_EMOJIS = [
    "🍎", "🍊", "🍋", "🍇", "🍓", "🫐", "🍑", "🥭", "🍍", "🥥",
    "🍆", "🥦", "🥕", "🌽", "🥩", "🍗", "🍖", "🦐", "🦀", "🌮",
    "🍜", "🍣", "🍕", "🍰", "🎂", "🍩", "🍪", "🍫", "🧁", "🥐",
    "🧀", "🥚", "🍳", "🥞", "🍞", "🥗", "🍲", "🍛", "🍱", "🍦",
    "🍧", "🍨", "🍡", "🥜", "🍿", "🥝", "🍒", "🍈", "🥑", "🌶️",
]

FOOD_PER_RESERVATION     = 20
FOOD_PER_PASSWORD        = 5
FOOD_PER_CMD             = 1
CMD_FOOD_COOLDOWN_SECS   = 60
MIN_PASSWORDS_FOR_NEW_RES = 2
MAX_PET_NAME_LEN         = 32

# ─────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────
def _init_db(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reservations (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            user_name      TEXT    NOT NULL,
            channel_id     INTEGER NOT NULL,
            court_number   INTEGER NOT NULL,
            registered_at  TEXT    NOT NULL,
            start_time     TEXT    NOT NULL,
            end_time       TEXT    NOT NULL,
            users          TEXT    NOT NULL DEFAULT '[]',
            password_id    INTEGER,
            notified_2min  INTEGER NOT NULL DEFAULT 0,
            active         INTEGER NOT NULL DEFAULT 1,
            passwords_used INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS passwords (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            added_by TEXT NOT NULL,
            added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subscribers (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL UNIQUE,
            channel_id   INTEGER NOT NULL,
            court_number INTEGER,
            created_at   TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pets (
            user_id       INTEGER PRIMARY KEY,
            user_name     TEXT    NOT NULL,
            pet_name      TEXT    NOT NULL DEFAULT 'Pet',
            experience    INTEGER NOT NULL DEFAULT 0,
            food          INTEGER NOT NULL DEFAULT 0,
            last_cmd_food TEXT
        );
    """)
    # Migration: add passwords_used to existing databases that lack it
    try:
        conn.execute("ALTER TABLE reservations ADD COLUMN passwords_used INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn

# ─────────────────────────────────────────────────────────────
# Row Converters
# ─────────────────────────────────────────────────────────────
def _row_to_res(row) -> dict:
    return {
        "id":             row[0],
        "user_id":        row[1],
        "user_name":      row[2],
        "channel_id":     row[3],
        "court_number":   row[4],
        "registered_at":  datetime.fromisoformat(row[5]),
        "start_time":     datetime.fromisoformat(row[6]),
        "end_time":       datetime.fromisoformat(row[7]),
        "users":          json.loads(row[8]),
        "password_id":    row[9],
        "notified_2min":  bool(row[10]),
        "active":         bool(row[11]),
        "passwords_used": row[12] if len(row) > 12 else 0,
    }

def _row_to_pw(row) -> dict:
    return {
        "id":       row[0],
        "username": row[1],
        "password": row[2],
        "added_by": row[3],
        "added_at": datetime.fromisoformat(row[4]),
    }

# ─────────────────────────────────────────────────────────────
# DB Query Helpers
# ─────────────────────────────────────────────────────────────
def _get_reservation(conn: sqlite3.Connection, res_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM reservations WHERE id=?", (res_id,)).fetchone()
    return _row_to_res(row) if row else None

def _get_password(conn: sqlite3.Connection, pw_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM passwords WHERE id=?", (pw_id,)).fetchone()
    return _row_to_pw(row) if row else None

def _get_all_passwords(conn: sqlite3.Connection) -> list[dict]:
    return [_row_to_pw(r) for r in conn.execute("SELECT * FROM passwords ORDER BY id").fetchall()]

def _reservation_for_password(conn: sqlite3.Connection, pw_id: int) -> dict | None:
    now = datetime.now()
    row = conn.execute(
        "SELECT * FROM reservations WHERE password_id=? AND active=1 AND end_time>?",
        (pw_id, now.isoformat())
    ).fetchone()
    return _row_to_res(row) if row else None

def _visible_reservations(conn: sqlite3.Connection) -> list[dict]:
    now = datetime.now()
    rows = conn.execute(
        "SELECT * FROM reservations WHERE active=1 OR end_time>? ORDER BY court_number, start_time",
        (now.isoformat(),)
    ).fetchall()
    return [_row_to_res(r) for r in rows]

def _my_reservations(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    now = datetime.now()
    rows = conn.execute(
        "SELECT * FROM reservations WHERE active=1 OR end_time>? ORDER BY start_time",
        (now.isoformat(),)
    ).fetchall()
    return [_row_to_res(r) for r in rows if r[1] == user_id or user_id in json.loads(r[8])]

# ─────────────────────────────────────────────────────────────
# Security Helpers
# ─────────────────────────────────────────────────────────────
_CTRL    = re.compile(r"[\x00-\x1f\x7f]")
_MENTION = re.compile(r"@(everyone|here|&[0-9]{17,20})")

def _safe(s: str) -> str:
    return _CTRL.sub("", str(s))

def _clean(s: str, max_len: int = MAX_CREDENTIAL_LEN) -> str:
    return _MENTION.sub("@​\\1", s[:max_len])

# ─────────────────────────────────────────────────────────────
# Command Logic Functions
# ─────────────────────────────────────────────────────────────

def logic_register(
    conn: sqlite3.Connection,
    user_id: int,
    user_name: str,
    channel_id: int,
    court_number: int,
    groups_in_front: int,
    time_remaining: float,
    now: datetime | None = None,
) -> dict:
    """
    Returns {"error": str} on failure, or
    {"error": None, "res_id", "start_time", "end_time", "wait_mins"} on success.
    """
    if not (1 <= court_number <= MAX_COURT_NUMBER):
        return {"error": f"❌  `court_number` must be between 1 and {MAX_COURT_NUMBER}."}
    if not (0 <= groups_in_front <= MAX_GROUPS_IN_FRONT):
        return {"error": f"❌  `groups_in_front` must be between 0 and {MAX_GROUPS_IN_FRONT}."}
    if not (0 <= time_remaining <= SLOT_DURATION_MINS):
        return {"error": f"❌  `time_remaining` must be between 0 and {SLOT_DURATION_MINS} minutes."}

    active_count = conn.execute("SELECT COUNT(*) FROM reservations WHERE active=1").fetchone()[0]
    if active_count >= MAX_ACTIVE_RESERVATIONS:
        return {"error": f"❌  Reservation list is full ({MAX_ACTIVE_RESERVATIONS} active). Wait for slots to expire."}

    last_res = conn.execute(
        "SELECT passwords_used FROM reservations WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if last_res is not None and last_res[0] < MIN_PASSWORDS_FOR_NEW_RES:
        return {"error": "REMIND"}

    if now is None:
        now = datetime.now()
    wait_mins  = time_remaining + groups_in_front * SLOT_DURATION_MINS
    start_time = now + timedelta(minutes=wait_mins)
    end_time   = start_time + timedelta(minutes=SLOT_DURATION_MINS)

    cur = conn.execute(
        """INSERT INTO reservations (user_id, user_name, channel_id, court_number,
                                     registered_at, start_time, end_time, users)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, user_name, channel_id, court_number, now.isoformat(),
         start_time.isoformat(), end_time.isoformat(), json.dumps([]))
    )
    _get_or_create_pet(conn, user_id, user_name)
    conn.execute("UPDATE pets SET food = food + ? WHERE user_id = ?", (FOOD_PER_RESERVATION, user_id))
    conn.commit()
    return {
        "error":        None,
        "res_id":       cur.lastrowid,
        "start_time":   start_time,
        "end_time":     end_time,
        "wait_mins":    wait_mins,
        "food_awarded": FOOD_PER_RESERVATION,
    }


def logic_cancel(
    conn: sqlite3.Connection,
    reservation_id: int,
    user_id: int,
    now: datetime | None = None,
) -> dict:
    """Returns {"error": str} on failure, or {"error": None} on success."""
    r = _get_reservation(conn, reservation_id)
    if not r:
        return {"error": f"❌  Reservation **#{reservation_id}** not found."}
    if r["user_id"] != user_id:
        return {"error": "❌  You can only cancel your own reservations."}
    if not r["active"]:
        return {"error": "❌  That reservation is already inactive."}
    if (now if now is not None else datetime.now()) >= r["start_time"]:
        return {"error": "❌  That slot has already started — it cannot be cancelled."}

    conn.execute("UPDATE reservations SET active=0 WHERE id=?", (reservation_id,))
    conn.commit()
    return {"error": None}


def logic_adduser(
    conn: sqlite3.Connection,
    reservation_id: int,
    caller_user_id: int,
    users_to_add: list[tuple[int, str]],
) -> dict:
    """
    users_to_add: list of (user_id, display_name) tuples.
    Returns {"error": str} on failure, or
    {"error": None, "added": list[str], "skipped": list[str], "users": list[int]} on success.
    """
    r = _get_reservation(conn, reservation_id)
    if not r:
        return {"error": f"❌  Reservation **#{reservation_id}** not found."}
    if not r["active"]:
        return {"error": "❌  That reservation is no longer active."}
    if caller_user_id != r["user_id"]:
        return {"error": "❌  Only the person who registered this reservation can add players."}

    users = list(r["users"])
    added, skipped = [], []
    for uid, name in users_to_add:
        if uid in users:
            skipped.append(f"{name} (already in reservation)")
        elif len(users) >= MAX_USERS_PER_RESERVATION:
            skipped.append(f"{name} (reservation full — max {MAX_USERS_PER_RESERVATION})")
        else:
            users.append(uid)
            added.append(name)

    conn.execute("UPDATE reservations SET users=? WHERE id=?", (json.dumps(users), reservation_id))
    conn.commit()
    return {"error": None, "added": added, "skipped": skipped, "users": users}


def logic_addpassword(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    added_by: str,
    now: datetime | None = None,
) -> dict:
    """
    Returns {"error": str} on failure, or
    {"error": None, "pw_id": int, "username": str, "password": str} on success.
    """
    if conn.execute("SELECT COUNT(*) FROM passwords").fetchone()[0] >= MAX_PASSWORDS:
        return {"error": f"❌  Password pool is full ({MAX_PASSWORDS} entries)."}
    if len(username) > MAX_CREDENTIAL_LEN or len(password) > MAX_CREDENTIAL_LEN:
        return {"error": f"❌  Username and password must each be {MAX_CREDENTIAL_LEN} characters or fewer."}

    username = _clean(username)
    password = _clean(password)
    if now is None:
        now = datetime.now()

    cur = conn.execute(
        "INSERT INTO passwords (username, password, added_by, added_at) VALUES (?, ?, ?, ?)",
        (username, password, added_by, now.isoformat())
    )
    conn.commit()
    return {"error": None, "pw_id": cur.lastrowid, "username": username, "password": password}


def logic_listpasswords(
    conn: sqlite3.Connection,
    now_pt: datetime | None = None,
) -> dict:
    """
    Returns {"error": None, "free", "in_use", "next_expire", "empty"}.
    """
    all_pws = _get_all_passwords(conn)
    if now_pt is None:
        now_pt = datetime.now(PACIFIC)
    next_expire = (now_pt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    free, in_use = [], []
    for pw in all_pws:
        (in_use if _reservation_for_password(conn, pw["id"]) else free).append(pw)

    return {
        "error": None,
        "free": free,
        "in_use": in_use,
        "next_expire": next_expire,
        "empty": not all_pws,
    }


def delete_expired_passwords(
    conn: sqlite3.Connection,
    now_pt: datetime | None = None,
) -> int:
    """
    Deletes passwords added before the most recent midnight PT.
    Returns the number of rows deleted.
    Called at bot startup to handle missed midnight resets.
    """
    if now_pt is None:
        now_pt = datetime.now(PACIFIC)
    last_midnight_pt = now_pt.replace(hour=0, minute=0, second=0, microsecond=0)
    # added_at is stored as naive local time via datetime.now().isoformat();
    # convert last midnight PT to naive local for a like-for-like comparison.
    last_midnight_local = last_midnight_pt.astimezone().replace(tzinfo=None)
    deleted = conn.execute(
        "DELETE FROM passwords WHERE added_at < ?",
        (last_midnight_local.isoformat(),),
    ).rowcount
    if deleted:
        conn.execute(
            "UPDATE reservations SET password_id=NULL WHERE password_id IS NOT NULL "
            "AND active=1 AND id NOT IN (SELECT id FROM reservations WHERE password_id IN "
            "(SELECT id FROM passwords))"
        )
    conn.commit()
    return deleted


def logic_usepassword(
    conn: sqlite3.Connection,
    reservation_id: int,
    username: str,
    caller_user_id: int,
) -> dict:
    """
    Looks up the password pool entry by username, then assigns it to the reservation.
    Returns {"error": str} on failure, or
    {"error": None, "pw_username": str, "court_number": int} on success.
    """
    r = _get_reservation(conn, reservation_id)
    if not r:
        return {"error": f"❌  Reservation **#{reservation_id}** not found."}
    if not r["active"]:
        return {"error": "❌  That reservation is no longer active."}
    if caller_user_id != r["user_id"]:
        return {"error": "❌  Only the person who registered this reservation can assign a password."}

    row = conn.execute("SELECT * FROM passwords WHERE username=?", (username,)).fetchone()
    if not row:
        return {"error": f"❌  No password found for username `{username}`. Use `/listpasswords` to see available usernames."}
    pw = _row_to_pw(row)

    existing_res = _reservation_for_password(conn, pw["id"])
    if existing_res and existing_res["id"] != reservation_id:
        return {
            "error": f"❌  `{username}` is already in use by Reservation "
                     f"**#{existing_res['id']}** (Court {existing_res['court_number']}, "
                     f"frees at {existing_res['end_time'].strftime('%-I:%M %p')})."
        }

    conn.execute("UPDATE reservations SET password_id=? WHERE id=?", (pw["id"], reservation_id))
    conn.execute(
        "UPDATE reservations SET passwords_used = passwords_used + 1 WHERE id=?",
        (reservation_id,),
    )
    _get_or_create_pet(conn, caller_user_id, "")
    conn.execute("UPDATE pets SET food = food + ? WHERE user_id = ?", (FOOD_PER_PASSWORD, caller_user_id))
    conn.commit()
    return {
        "error":        None,
        "pw_username":  pw["username"],
        "court_number": r["court_number"],
        "food_awarded": FOOD_PER_PASSWORD,
    }


def logic_delete(
    conn: sqlite3.Connection,
    reservation_id: int,
) -> dict:
    """Hard-deletes a reservation regardless of owner or state. Returns {"error": str} or {"error": None}."""
    r = _get_reservation(conn, reservation_id)
    if not r:
        return {"error": f"❌  Reservation **#{reservation_id}** not found."}
    conn.execute("DELETE FROM reservations WHERE id=?", (reservation_id,))
    conn.commit()
    return {"error": None}


def logic_subscribe(
    conn: sqlite3.Connection,
    user_id: int,
    channel_id: int,
    court_number: int | None = None,
    now: datetime | None = None,
) -> dict:
    """
    Registers a one-shot subscription: the user gets a DM 2 min before the
    next slot starts (on the specified court, or any court if None).
    Replaces any existing subscription for this user.
    Returns {"error": None, "court_number": int | None, "replaced": bool}.
    """
    if court_number is not None and not (1 <= court_number <= MAX_COURT_NUMBER):
        return {"error": f"❌  `court_number` must be between 1 and {MAX_COURT_NUMBER}."}

    if now is None:
        now = datetime.now()

    existing = conn.execute(
        "SELECT id FROM subscribers WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.execute("DELETE FROM subscribers WHERE user_id=?", (user_id,))
    conn.execute(
        "INSERT INTO subscribers (user_id, channel_id, court_number, created_at) VALUES (?, ?, ?, ?)",
        (user_id, channel_id, court_number, now.isoformat()),
    )
    conn.commit()
    return {"error": None, "court_number": court_number, "replaced": existing is not None}


def logic_unsubscribe(
    conn: sqlite3.Connection,
    user_id: int,
) -> dict:
    """Returns {"error": str} if no subscription existed, {"error": None} on success."""
    deleted = conn.execute("DELETE FROM subscribers WHERE user_id=?", (user_id,)).rowcount
    conn.commit()
    if deleted == 0:
        return {"error": "❌  You don't have an active subscription."}
    return {"error": None}


def _pop_subscribers_for_court(
    conn: sqlite3.Connection,
    court_number: int,
) -> list[dict]:
    """
    Returns all subscribers that should be notified for this court, then
    deletes them (one-shot delivery).
    """
    rows = conn.execute(
        "SELECT id, user_id FROM subscribers WHERE court_number=? OR court_number IS NULL",
        (court_number,),
    ).fetchall()
    if rows:
        placeholders = ",".join("?" * len(rows))
        conn.execute(f"DELETE FROM subscribers WHERE id IN ({placeholders})", [r[0] for r in rows])
        conn.commit()
    return [{"user_id": r[1]} for r in rows]


# ─────────────────────────────────────────────────────────────
# Pet Helpers
# ─────────────────────────────────────────────────────────────

def _xp_for_tier(n: int) -> int:
    """Cumulative XP required to reach tier n.
    Cost per step = (tier // 10 + 1) * 20, so tiers 0-9 cost 20 each,
    tiers 10-19 cost 40 each, 20-29 cost 60, etc.
    Closed form: 100 * d * (d+1) + p * (d+1) * 20  where d = n//10, p = n%10.
    """
    d, p = n // 10, n % 10
    return 100 * d * (d + 1) + p * (d + 1) * 20


def _pet_tier(xp: int) -> int:
    """Returns the tier index (0–47) for the given cumulative XP."""
    tier = 0
    while tier + 1 < len(PET_TIERS) and xp >= _xp_for_tier(tier + 1):
        tier += 1
    return tier


def _xp_to_next_tier(xp: int) -> int:
    """XP still needed to reach the next tier (0 if already at max)."""
    tier = _pet_tier(xp)
    if tier >= len(PET_TIERS) - 1:
        return 0
    return _xp_for_tier(tier + 1) - xp


def _pet_hash(user_id: int, tier: int) -> str:
    """8-char hex recovery code. Encodes user_id + tier so it's stable within a tier
    and valid across database instances (Discord user IDs are global)."""
    return hashlib.sha256(f"{user_id}:{tier}".encode()).hexdigest()[:8]


def _get_or_create_pet(conn: sqlite3.Connection, user_id: int, user_name: str) -> dict:
    row = conn.execute("SELECT * FROM pets WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO pets (user_id, user_name, pet_name, experience, food, last_cmd_food) "
            "VALUES (?, ?, 'Pet', 0, 0, NULL)",
            (user_id, user_name),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM pets WHERE user_id=?", (user_id,)).fetchone()
    return {
        "user_id":       row[0],
        "user_name":     row[1],
        "pet_name":      row[2],
        "experience":    row[3],
        "food":          row[4],
        "last_cmd_food": datetime.fromisoformat(row[5]) if row[5] else None,
    }


def _award_cmd_food(
    conn: sqlite3.Connection,
    user_id: int,
    user_name: str,
    now: datetime | None = None,
) -> bool:
    """Award 1 food if the per-minute cooldown has elapsed. Returns True if food was given."""
    if now is None:
        now = datetime.now()
    pet = _get_or_create_pet(conn, user_id, user_name)
    if pet["last_cmd_food"] is not None:
        if (now - pet["last_cmd_food"]).total_seconds() < CMD_FOOD_COOLDOWN_SECS:
            return False
    conn.execute(
        "UPDATE pets SET food = food + 1, last_cmd_food = ? WHERE user_id = ?",
        (now.isoformat(), user_id),
    )
    conn.commit()
    return True


# ─────────────────────────────────────────────────────────────
# Pet Command Logic
# ─────────────────────────────────────────────────────────────

def logic_pet(conn: sqlite3.Connection, user_id: int, user_name: str) -> dict:
    pet   = _get_or_create_pet(conn, user_id, user_name)
    xp    = pet["experience"]
    tier  = _pet_tier(xp)
    emoji = PET_TIERS[tier]
    h     = _pet_hash(user_id, tier)
    return {
        "error":      None,
        "pet_name":   pet["pet_name"],
        "emoji":      emoji,
        "tier":       tier,
        "xp":         xp,
        "food":       pet["food"],
        "xp_to_next": _xp_to_next_tier(xp),
        "hash":       h,
        "at_max":     tier >= len(PET_TIERS) - 1,
    }


def logic_food(conn: sqlite3.Connection, user_id: int, user_name: str) -> dict:
    pet = _get_or_create_pet(conn, user_id, user_name)
    return {"error": None, "food": pet["food"]}


def logic_feed(
    conn: sqlite3.Connection,
    user_id: int,
    user_name: str,
    amount: int | None = None,
) -> dict:
    pet = _get_or_create_pet(conn, user_id, user_name)
    if pet["food"] <= 0:
        return {"error": "no_food"}
    if amount is not None and amount <= 0:
        return {"error": "❌  Amount must be at least 1."}

    actual    = pet["food"] if amount is None else min(amount, pet["food"])
    old_xp    = pet["experience"]
    old_tier  = _pet_tier(old_xp)
    new_xp    = old_xp + actual
    new_tier  = _pet_tier(new_xp)

    conn.execute(
        "UPDATE pets SET food = food - ?, experience = experience + ? WHERE user_id = ?",
        (actual, actual, user_id),
    )
    conn.commit()

    return {
        "error":      None,
        "fed":        actual,
        "food_emoji": random.choice(FOOD_EMOJIS),
        "old_tier":   old_tier,
        "new_tier":   new_tier,
        "old_emoji":  PET_TIERS[old_tier],
        "new_emoji":  PET_TIERS[new_tier],
        "grew":       new_tier > old_tier,
        "new_xp":     new_xp,
        "food_left":  pet["food"] - actual,
        "xp_to_next": _xp_to_next_tier(new_xp),
        "pet_name":   pet["pet_name"],
        "at_max":     new_tier >= len(PET_TIERS) - 1,
    }


def logic_whistle(
    conn: sqlite3.Connection,
    user_id: int,
    user_name: str,
    code: str,
) -> dict:
    """
    Recovers a pet cross-database using only the recovery code. Linearly scans all
    48 tiers to find the one whose hash matches. Food and partial XP are not recovered.
    """
    for tier, emoji in enumerate(PET_TIERS):
        if _pet_hash(user_id, tier) == code:
            recovery_xp = _xp_for_tier(tier)
            existing = conn.execute("SELECT pet_name FROM pets WHERE user_id=?", (user_id,)).fetchone()
            pet_name = existing[0] if existing else "Pet"
            conn.execute(
                "INSERT INTO pets (user_id, user_name, pet_name, experience, food) "
                "VALUES (?, ?, ?, ?, 0) "
                "ON CONFLICT(user_id) DO UPDATE SET experience=excluded.experience, food=0",
                (user_id, user_name, pet_name, recovery_xp),
            )
            conn.commit()
            return {
                "error":    None,
                "emoji":    emoji,
                "tier":     tier,
                "pet_name": pet_name,
                "xp":       recovery_xp,
            }
    return {"error": "not_found"}


def logic_rename(
    conn: sqlite3.Connection,
    user_id: int,
    user_name: str,
    new_name: str,
) -> dict:
    new_name = new_name.strip()
    if not new_name:
        return {"error": "❌  Name cannot be empty."}
    if len(new_name) > MAX_PET_NAME_LEN:
        return {"error": f"❌  Name must be {MAX_PET_NAME_LEN} characters or fewer."}
    new_name = _clean(new_name, max_len=MAX_PET_NAME_LEN)
    pet = _get_or_create_pet(conn, user_id, user_name)
    conn.execute("UPDATE pets SET pet_name = ? WHERE user_id = ?", (new_name, user_id))
    conn.commit()
    emoji = PET_TIERS[_pet_tier(pet["experience"])]
    return {"error": None, "pet_name": new_name, "emoji": emoji}
