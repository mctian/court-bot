"""
Pure logic layer — no Discord imports, safe to unit-test directly.
"""

import json
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
# Database
# ─────────────────────────────────────────────────────────────
def _init_db(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reservations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            user_name     TEXT    NOT NULL,
            channel_id    INTEGER NOT NULL,
            court_number  INTEGER NOT NULL,
            registered_at TEXT    NOT NULL,
            start_time    TEXT    NOT NULL,
            end_time      TEXT    NOT NULL,
            users         TEXT    NOT NULL DEFAULT '[]',
            password_id   INTEGER,
            notified_2min INTEGER NOT NULL DEFAULT 0,
            active        INTEGER NOT NULL DEFAULT 1
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
    """)
    conn.commit()
    return conn

# ─────────────────────────────────────────────────────────────
# Row Converters
# ─────────────────────────────────────────────────────────────
def _row_to_res(row) -> dict:
    return {
        "id":            row[0],
        "user_id":       row[1],
        "user_name":     row[2],
        "channel_id":    row[3],
        "court_number":  row[4],
        "registered_at": datetime.fromisoformat(row[5]),
        "start_time":    datetime.fromisoformat(row[6]),
        "end_time":      datetime.fromisoformat(row[7]),
        "users":         json.loads(row[8]),
        "password_id":   row[9],
        "notified_2min": bool(row[10]),
        "active":        bool(row[11]),
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
    conn.commit()
    return {
        "error": None,
        "res_id": cur.lastrowid,
        "start_time": start_time,
        "end_time": end_time,
        "wait_mins": wait_mins,
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


def logic_usepassword(
    conn: sqlite3.Connection,
    reservation_id: int,
    password_id: int,
    caller_user_id: int,
) -> dict:
    """
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

    pw = _get_password(conn, password_id)
    if not pw:
        return {"error": f"❌  Password **#{password_id}** not found. Use `/listpasswords` to see available IDs."}

    existing_res = _reservation_for_password(conn, password_id)
    if existing_res and existing_res["id"] != reservation_id:
        return {
            "error": f"❌  Password **#{password_id}** is already in use by Reservation "
                     f"**#{existing_res['id']}** (Court {existing_res['court_number']}, "
                     f"frees at {existing_res['end_time'].strftime('%-I:%M %p')})."
        }

    conn.execute("UPDATE reservations SET password_id=? WHERE id=?", (password_id, reservation_id))
    conn.commit()
    return {"error": None, "pw_username": pw["username"], "court_number": r["court_number"]}


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
