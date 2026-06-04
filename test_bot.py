import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from logic import (
    _init_db,
    logic_register, logic_cancel, logic_adduser,
    logic_addpassword, logic_listpasswords, logic_usepassword,
    SLOT_DURATION_MINS, MAX_ACTIVE_RESERVATIONS, MAX_PASSWORDS,
    MAX_USERS_PER_RESERVATION, MAX_COURT_NUMBER, MAX_GROUPS_IN_FRONT,
    MAX_CREDENTIAL_LEN, PACIFIC,
)


# ─────────────────────────────────────────────────────────────
# Test helpers
# ─────────────────────────────────────────────────────────────
def make_db() -> sqlite3.Connection:
    return _init_db(":memory:")


def insert_res(
    conn, user_id=1, user_name="Alice", channel_id=100, court_number=1,
    start_offset_mins=60, active=True, password_id=None,
) -> int:
    now   = datetime.now()
    start = now + timedelta(minutes=start_offset_mins)
    end   = start + timedelta(minutes=SLOT_DURATION_MINS)
    cur = conn.execute(
        """INSERT INTO reservations
           (user_id, user_name, channel_id, court_number, registered_at,
            start_time, end_time, users, active, password_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, user_name, channel_id, court_number, now.isoformat(),
         start.isoformat(), end.isoformat(), json.dumps([user_id]),
         1 if active else 0, password_id),
    )
    conn.commit()
    return cur.lastrowid


def insert_pw(conn, username="user1", password="pass1", added_by="Bot") -> int:
    cur = conn.execute(
        "INSERT INTO passwords (username, password, added_by, added_at) VALUES (?, ?, ?, ?)",
        (username, password, added_by, datetime.now().isoformat()),
    )
    conn.commit()
    return cur.lastrowid


# ─────────────────────────────────────────────────────────────
# /register
# ─────────────────────────────────────────────────────────────
class TestLogicRegister(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_success_no_queue(self):
        now = datetime(2024, 1, 1, 10, 0)
        r = logic_register(self.db, 1, "Alice", 100, 1, 0, 0.0, now=now)
        self.assertIsNone(r["error"])
        self.assertEqual(r["wait_mins"], 0.0)
        self.assertEqual(r["start_time"], now)
        self.assertEqual(r["end_time"], now + timedelta(minutes=SLOT_DURATION_MINS))
        self.assertIsNotNone(r["res_id"])

    def test_success_with_queue(self):
        now = datetime(2024, 1, 1, 10, 0)
        r = logic_register(self.db, 1, "Alice", 100, 1, 2, 10.0, now=now)
        self.assertIsNone(r["error"])
        expected_wait = 10.0 + 2 * SLOT_DURATION_MINS
        self.assertEqual(r["wait_mins"], expected_wait)
        self.assertEqual(r["start_time"], now + timedelta(minutes=expected_wait))

    def test_error_court_number_too_low(self):
        r = logic_register(self.db, 1, "Alice", 100, 0, 0, 0.0)
        self.assertIsNotNone(r["error"])
        self.assertIn("court_number", r["error"])

    def test_error_court_number_too_high(self):
        r = logic_register(self.db, 1, "Alice", 100, MAX_COURT_NUMBER + 1, 0, 0.0)
        self.assertIsNotNone(r["error"])
        self.assertIn("court_number", r["error"])

    def test_error_groups_in_front_negative(self):
        r = logic_register(self.db, 1, "Alice", 100, 1, -1, 0.0)
        self.assertIsNotNone(r["error"])
        self.assertIn("groups_in_front", r["error"])

    def test_error_groups_in_front_too_high(self):
        r = logic_register(self.db, 1, "Alice", 100, 1, MAX_GROUPS_IN_FRONT + 1, 0.0)
        self.assertIsNotNone(r["error"])
        self.assertIn("groups_in_front", r["error"])

    def test_error_time_remaining_negative(self):
        r = logic_register(self.db, 1, "Alice", 100, 1, 0, -1.0)
        self.assertIsNotNone(r["error"])
        self.assertIn("time_remaining", r["error"])

    def test_error_time_remaining_too_high(self):
        r = logic_register(self.db, 1, "Alice", 100, 1, 0, SLOT_DURATION_MINS + 1)
        self.assertIsNotNone(r["error"])
        self.assertIn("time_remaining", r["error"])

    def test_error_reservation_list_full(self):
        now = datetime(2024, 1, 1, 10, 0)
        for i in range(MAX_ACTIVE_RESERVATIONS):
            start = now + timedelta(minutes=i * 10)
            end   = start + timedelta(minutes=SLOT_DURATION_MINS)
            self.db.execute(
                """INSERT INTO reservations
                   (user_id, user_name, channel_id, court_number, registered_at,
                    start_time, end_time, users, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (i, f"User{i}", 100, 1, now.isoformat(),
                 start.isoformat(), end.isoformat(), json.dumps([i])),
            )
        self.db.commit()
        r = logic_register(self.db, 999, "Late", 100, 1, 0, 0.0)
        self.assertIsNotNone(r["error"])
        self.assertIn("full", r["error"])

    def test_row_written_to_db(self):
        r = logic_register(self.db, 42, "Bob", 200, 3, 1, 5.0)
        self.assertIsNone(r["error"])
        row = self.db.execute("SELECT user_id, court_number FROM reservations WHERE id=?", (r["res_id"],)).fetchone()
        self.assertEqual(row[0], 42)
        self.assertEqual(row[1], 3)


# ─────────────────────────────────────────────────────────────
# /cancel
# ─────────────────────────────────────────────────────────────
class TestLogicCancel(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_success(self):
        res_id = insert_res(self.db, user_id=1, start_offset_mins=60)
        r = logic_cancel(self.db, res_id, 1)
        self.assertIsNone(r["error"])
        active = self.db.execute("SELECT active FROM reservations WHERE id=?", (res_id,)).fetchone()[0]
        self.assertEqual(active, 0)

    def test_error_not_found(self):
        r = logic_cancel(self.db, 999, 1)
        self.assertIsNotNone(r["error"])
        self.assertIn("not found", r["error"])

    def test_error_wrong_user(self):
        res_id = insert_res(self.db, user_id=1)
        r = logic_cancel(self.db, res_id, 2)
        self.assertIsNotNone(r["error"])
        self.assertIn("own", r["error"])

    def test_error_already_inactive(self):
        res_id = insert_res(self.db, user_id=1, active=False)
        r = logic_cancel(self.db, res_id, 1)
        self.assertIsNotNone(r["error"])
        self.assertIn("inactive", r["error"])

    def test_error_slot_already_started(self):
        res_id = insert_res(self.db, user_id=1, start_offset_mins=-10)
        r = logic_cancel(self.db, res_id, 1)
        self.assertIsNotNone(r["error"])
        self.assertIn("started", r["error"])

    def test_injected_now_before_start_succeeds(self):
        res_id = insert_res(self.db, user_id=1, start_offset_mins=5)
        past   = datetime.now() - timedelta(minutes=10)
        r = logic_cancel(self.db, res_id, 1, now=past)
        self.assertIsNone(r["error"])

    def test_injected_now_after_start_fails(self):
        res_id = insert_res(self.db, user_id=1, start_offset_mins=5)
        future = datetime.now() + timedelta(minutes=10)
        r = logic_cancel(self.db, res_id, 1, now=future)
        self.assertIsNotNone(r["error"])
        self.assertIn("started", r["error"])


# ─────────────────────────────────────────────────────────────
# /adduser
# ─────────────────────────────────────────────────────────────
class TestLogicAddUser(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_success_add_one(self):
        res_id = insert_res(self.db, user_id=1)
        r = logic_adduser(self.db, res_id, 1, [(2, "Bob")])
        self.assertIsNone(r["error"])
        self.assertIn("Bob", r["added"])
        self.assertIn(2, r["users"])

    def test_success_add_multiple(self):
        res_id = insert_res(self.db, user_id=1)
        r = logic_adduser(self.db, res_id, 1, [(2, "Bob"), (3, "Charlie")])
        self.assertIsNone(r["error"])
        self.assertEqual(sorted(r["added"]), ["Bob", "Charlie"])
        self.assertIn(2, r["users"])
        self.assertIn(3, r["users"])

    def test_error_not_found(self):
        r = logic_adduser(self.db, 999, 1, [(2, "Bob")])
        self.assertIsNotNone(r["error"])
        self.assertIn("not found", r["error"])

    def test_error_not_active(self):
        res_id = insert_res(self.db, user_id=1, active=False)
        r = logic_adduser(self.db, res_id, 1, [(2, "Bob")])
        self.assertIsNotNone(r["error"])
        self.assertIn("active", r["error"])

    def test_error_wrong_caller(self):
        res_id = insert_res(self.db, user_id=1)
        r = logic_adduser(self.db, res_id, 2, [(3, "Charlie")])
        self.assertIsNotNone(r["error"])
        self.assertIn("registered", r["error"])

    def test_skip_duplicate_user(self):
        res_id = insert_res(self.db, user_id=1)
        r = logic_adduser(self.db, res_id, 1, [(1, "Alice")])
        self.assertIsNone(r["error"])
        self.assertEqual(r["added"], [])
        self.assertTrue(any("already" in s for s in r["skipped"]))

    def test_skip_when_reservation_full(self):
        res_id = insert_res(self.db, user_id=1)
        # Fill to max
        fill = [(i + 2, f"User{i}") for i in range(MAX_USERS_PER_RESERVATION - 1)]
        logic_adduser(self.db, res_id, 1, fill)
        # Now at MAX_USERS_PER_RESERVATION — one more should be skipped
        r = logic_adduser(self.db, res_id, 1, [(99, "Extra")])
        self.assertIsNone(r["error"])
        self.assertTrue(any("full" in s for s in r["skipped"]))

    def test_users_persisted_to_db(self):
        res_id = insert_res(self.db, user_id=1)
        logic_adduser(self.db, res_id, 1, [(2, "Bob")])
        stored = json.loads(
            self.db.execute("SELECT users FROM reservations WHERE id=?", (res_id,)).fetchone()[0]
        )
        self.assertIn(2, stored)


# ─────────────────────────────────────────────────────────────
# /addpassword
# ─────────────────────────────────────────────────────────────
class TestLogicAddPassword(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_success(self):
        r = logic_addpassword(self.db, "user1", "pass1", "Admin")
        self.assertIsNone(r["error"])
        self.assertIsNotNone(r["pw_id"])
        self.assertEqual(r["username"], "user1")
        self.assertEqual(r["password"], "pass1")

    def test_error_pool_full(self):
        for i in range(MAX_PASSWORDS):
            self.db.execute(
                "INSERT INTO passwords (username, password, added_by, added_at) VALUES (?, ?, ?, ?)",
                (f"u{i}", f"p{i}", "Bot", datetime.now().isoformat()),
            )
        self.db.commit()
        r = logic_addpassword(self.db, "new", "new", "Admin")
        self.assertIsNotNone(r["error"])
        self.assertIn("full", r["error"])

    def test_error_username_too_long(self):
        r = logic_addpassword(self.db, "x" * (MAX_CREDENTIAL_LEN + 1), "pass", "Admin")
        self.assertIsNotNone(r["error"])
        self.assertIn("characters", r["error"])

    def test_error_password_too_long(self):
        r = logic_addpassword(self.db, "user", "x" * (MAX_CREDENTIAL_LEN + 1), "Admin")
        self.assertIsNotNone(r["error"])
        self.assertIn("characters", r["error"])

    def test_mention_escaped_in_username(self):
        r = logic_addpassword(self.db, "@everyone", "pass", "Admin")
        self.assertIsNone(r["error"])
        self.assertNotIn("@everyone", r["username"])

    def test_mention_escaped_in_password(self):
        r = logic_addpassword(self.db, "user", "@here", "Admin")
        self.assertIsNone(r["error"])
        self.assertNotIn("@here", r["password"])

    def test_row_written_to_db(self):
        r = logic_addpassword(self.db, "admin", "secret", "Mason")
        row = self.db.execute("SELECT added_by FROM passwords WHERE id=?", (r["pw_id"],)).fetchone()
        self.assertEqual(row[0], "Mason")


# ─────────────────────────────────────────────────────────────
# /listpasswords
# ─────────────────────────────────────────────────────────────
class TestLogicListPasswords(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_empty_pool(self):
        r = logic_listpasswords(self.db)
        self.assertIsNone(r["error"])
        self.assertTrue(r["empty"])
        self.assertEqual(r["free"], [])
        self.assertEqual(r["in_use"], [])

    def test_all_free(self):
        insert_pw(self.db, "u1", "p1")
        insert_pw(self.db, "u2", "p2")
        r = logic_listpasswords(self.db)
        self.assertFalse(r["empty"])
        self.assertEqual(len(r["free"]), 2)
        self.assertEqual(len(r["in_use"]), 0)

    def test_password_in_use_when_linked_to_active_reservation(self):
        pw_id = insert_pw(self.db, "u1", "p1")
        # Active reservation that started 10 min ago, ends 35 min from now
        insert_res(self.db, user_id=1, password_id=pw_id, start_offset_mins=-10)
        r = logic_listpasswords(self.db)
        self.assertEqual(len(r["in_use"]), 1)
        self.assertEqual(len(r["free"]), 0)

    def test_password_free_when_no_reservation(self):
        insert_pw(self.db, "u1", "p1")
        r = logic_listpasswords(self.db)
        self.assertEqual(len(r["free"]), 1)
        self.assertEqual(len(r["in_use"]), 0)

    def test_next_expire_is_tomorrow_midnight_pt(self):
        now_pt      = datetime(2024, 1, 15, 10, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        r           = logic_listpasswords(self.db, now_pt=now_pt)
        next_expire = r["next_expire"]
        self.assertEqual(next_expire.day, 16)
        self.assertEqual(next_expire.hour, 0)
        self.assertEqual(next_expire.minute, 0)


# ─────────────────────────────────────────────────────────────
# /usepassword
# ─────────────────────────────────────────────────────────────
class TestLogicUsePassword(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_success(self):
        res_id = insert_res(self.db, user_id=1, court_number=3)
        pw_id  = insert_pw(self.db)
        r = logic_usepassword(self.db, res_id, pw_id, 1)
        self.assertIsNone(r["error"])
        self.assertEqual(r["court_number"], 3)
        self.assertEqual(r["pw_username"], "user1")

    def test_password_id_persisted_to_reservation(self):
        res_id = insert_res(self.db, user_id=1)
        pw_id  = insert_pw(self.db)
        logic_usepassword(self.db, res_id, pw_id, 1)
        stored = self.db.execute(
            "SELECT password_id FROM reservations WHERE id=?", (res_id,)
        ).fetchone()[0]
        self.assertEqual(stored, pw_id)

    def test_error_reservation_not_found(self):
        pw_id = insert_pw(self.db)
        r = logic_usepassword(self.db, 999, pw_id, 1)
        self.assertIsNotNone(r["error"])
        self.assertIn("not found", r["error"])

    def test_error_reservation_inactive(self):
        res_id = insert_res(self.db, user_id=1, active=False)
        pw_id  = insert_pw(self.db)
        r = logic_usepassword(self.db, res_id, pw_id, 1)
        self.assertIsNotNone(r["error"])
        self.assertIn("active", r["error"])

    def test_error_wrong_caller(self):
        res_id = insert_res(self.db, user_id=1)
        pw_id  = insert_pw(self.db)
        r = logic_usepassword(self.db, res_id, pw_id, 2)
        self.assertIsNotNone(r["error"])
        self.assertIn("registered", r["error"])

    def test_error_password_not_found(self):
        res_id = insert_res(self.db, user_id=1)
        r = logic_usepassword(self.db, res_id, 999, 1)
        self.assertIsNotNone(r["error"])
        self.assertIn("not found", r["error"])

    def test_error_password_already_in_use_by_other_reservation(self):
        res_id1 = insert_res(self.db, user_id=1, court_number=1)
        res_id2 = insert_res(self.db, user_id=2, court_number=2)
        pw_id   = insert_pw(self.db)
        self.db.execute("UPDATE reservations SET password_id=? WHERE id=?", (pw_id, res_id1))
        self.db.commit()
        r = logic_usepassword(self.db, res_id2, pw_id, 2)
        self.assertIsNotNone(r["error"])
        self.assertIn("already in use", r["error"])

    def test_reassign_same_password_to_same_reservation_succeeds(self):
        res_id = insert_res(self.db, user_id=1)
        pw_id  = insert_pw(self.db)
        self.db.execute("UPDATE reservations SET password_id=? WHERE id=?", (pw_id, res_id))
        self.db.commit()
        r = logic_usepassword(self.db, res_id, pw_id, 1)
        self.assertIsNone(r["error"])


if __name__ == "__main__":
    unittest.main()
