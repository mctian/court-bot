import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from logic import (
    _init_db,
    logic_register, logic_cancel, logic_adduser,
    logic_addpassword, logic_listpasswords, logic_usepassword, logic_delete,
    logic_subscribe, logic_unsubscribe, _pop_subscribers_for_court,
    logic_pet, logic_food, logic_feed, logic_whistle, logic_rename,
    delete_expired_passwords,
    _get_or_create_pet, _award_cmd_food,
    _pet_tier, _xp_for_tier, _xp_to_next_tier, _pet_hash,
    PET_TIERS, PACIFIC,
    SLOT_DURATION_MINS, MAX_ACTIVE_RESERVATIONS, MAX_PASSWORDS,
    MAX_USERS_PER_RESERVATION, MAX_COURT_NUMBER, MAX_GROUPS_IN_FRONT,
    MAX_CREDENTIAL_LEN, MAX_PET_NAME_LEN,
    FOOD_PER_RESERVATION, FOOD_PER_PASSWORD, CMD_FOOD_COOLDOWN_SECS,
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
        self.assertEqual(r["food_awarded"], FOOD_PER_RESERVATION)

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
        insert_pw(self.db, username="user1")
        r = logic_usepassword(self.db, res_id, "user1", 1)
        self.assertIsNone(r["error"])
        self.assertEqual(r["court_number"], 3)
        self.assertEqual(r["pw_username"], "user1")
        self.assertEqual(r["food_awarded"], FOOD_PER_PASSWORD)

    def test_password_id_persisted_to_reservation(self):
        res_id = insert_res(self.db, user_id=1)
        pw_id  = insert_pw(self.db, username="user1")
        logic_usepassword(self.db, res_id, "user1", 1)
        stored = self.db.execute(
            "SELECT password_id FROM reservations WHERE id=?", (res_id,)
        ).fetchone()[0]
        self.assertEqual(stored, pw_id)

    def test_error_reservation_not_found(self):
        insert_pw(self.db, username="user1")
        r = logic_usepassword(self.db, 999, "user1", 1)
        self.assertIsNotNone(r["error"])
        self.assertIn("not found", r["error"])

    def test_error_reservation_inactive(self):
        res_id = insert_res(self.db, user_id=1, active=False)
        insert_pw(self.db, username="user1")
        r = logic_usepassword(self.db, res_id, "user1", 1)
        self.assertIsNotNone(r["error"])
        self.assertIn("active", r["error"])

    def test_error_wrong_caller(self):
        res_id = insert_res(self.db, user_id=1)
        insert_pw(self.db, username="user1")
        r = logic_usepassword(self.db, res_id, "user1", 2)
        self.assertIsNotNone(r["error"])
        self.assertIn("registered", r["error"])

    def test_error_password_not_found(self):
        res_id = insert_res(self.db, user_id=1)
        r = logic_usepassword(self.db, res_id, "nonexistent", 1)
        self.assertIsNotNone(r["error"])
        self.assertIn("No password found", r["error"])

    def test_error_password_already_in_use_by_other_reservation(self):
        res_id1 = insert_res(self.db, user_id=1, court_number=1)
        res_id2 = insert_res(self.db, user_id=2, court_number=2)
        pw_id   = insert_pw(self.db, username="user1")
        self.db.execute("UPDATE reservations SET password_id=? WHERE id=?", (pw_id, res_id1))
        self.db.commit()
        r = logic_usepassword(self.db, res_id2, "user1", 2)
        self.assertIsNotNone(r["error"])
        self.assertIn("already in use", r["error"])

    def test_reassign_same_password_to_same_reservation_succeeds(self):
        res_id = insert_res(self.db, user_id=1)
        pw_id  = insert_pw(self.db, username="user1")
        self.db.execute("UPDATE reservations SET password_id=? WHERE id=?", (pw_id, res_id))
        self.db.commit()
        r = logic_usepassword(self.db, res_id, "user1", 1)
        self.assertIsNone(r["error"])


# ─────────────────────────────────────────────────────────────
# /delete
# ─────────────────────────────────────────────────────────────
class TestLogicDelete(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_success(self):
        res_id = insert_res(self.db, user_id=1)
        r = logic_delete(self.db, res_id)
        self.assertIsNone(r["error"])
        row = self.db.execute("SELECT id FROM reservations WHERE id=?", (res_id,)).fetchone()
        self.assertIsNone(row)

    def test_error_not_found(self):
        r = logic_delete(self.db, 999)
        self.assertIsNotNone(r["error"])
        self.assertIn("not found", r["error"])

    def test_can_delete_another_users_reservation(self):
        res_id = insert_res(self.db, user_id=1)
        r = logic_delete(self.db, res_id)
        self.assertIsNone(r["error"])

    def test_can_delete_inactive_reservation(self):
        res_id = insert_res(self.db, user_id=1, active=False)
        r = logic_delete(self.db, res_id)
        self.assertIsNone(r["error"])

    def test_can_delete_started_reservation(self):
        res_id = insert_res(self.db, user_id=1, start_offset_mins=-10)
        r = logic_delete(self.db, res_id)
        self.assertIsNone(r["error"])


# ─────────────────────────────────────────────────────────────
# /subscribe  /unsubscribe
# ─────────────────────────────────────────────────────────────
class TestLogicSubscribe(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_success_any_court(self):
        r = logic_subscribe(self.db, 1, 100)
        self.assertIsNone(r["error"])
        self.assertIsNone(r["court_number"])
        self.assertFalse(r["replaced"])

    def test_success_specific_court(self):
        r = logic_subscribe(self.db, 1, 100, court_number=3)
        self.assertIsNone(r["error"])
        self.assertEqual(r["court_number"], 3)

    def test_error_court_number_too_low(self):
        r = logic_subscribe(self.db, 1, 100, court_number=0)
        self.assertIsNotNone(r["error"])
        self.assertIn("court_number", r["error"])

    def test_error_court_number_too_high(self):
        r = logic_subscribe(self.db, 1, 100, court_number=MAX_COURT_NUMBER + 1)
        self.assertIsNotNone(r["error"])
        self.assertIn("court_number", r["error"])

    def test_replaces_existing_subscription(self):
        logic_subscribe(self.db, 1, 100, court_number=2)
        r = logic_subscribe(self.db, 1, 100, court_number=5)
        self.assertIsNone(r["error"])
        self.assertTrue(r["replaced"])
        self.assertEqual(r["court_number"], 5)
        count = self.db.execute("SELECT COUNT(*) FROM subscribers WHERE user_id=1").fetchone()[0]
        self.assertEqual(count, 1)

    def test_unsubscribe_success(self):
        logic_subscribe(self.db, 1, 100)
        r = logic_unsubscribe(self.db, 1)
        self.assertIsNone(r["error"])
        count = self.db.execute("SELECT COUNT(*) FROM subscribers WHERE user_id=1").fetchone()[0]
        self.assertEqual(count, 0)

    def test_unsubscribe_not_found(self):
        r = logic_unsubscribe(self.db, 99)
        self.assertIsNotNone(r["error"])

    def test_pop_returns_any_court_subscribers(self):
        logic_subscribe(self.db, 1, 100, court_number=None)
        subs = _pop_subscribers_for_court(self.db, 3)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["user_id"], 1)

    def test_pop_returns_matching_court_subscribers(self):
        logic_subscribe(self.db, 1, 100, court_number=3)
        logic_subscribe(self.db, 2, 100, court_number=5)
        subs = _pop_subscribers_for_court(self.db, 3)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["user_id"], 1)

    def test_pop_excludes_different_court_subscribers(self):
        logic_subscribe(self.db, 1, 100, court_number=5)
        subs = _pop_subscribers_for_court(self.db, 3)
        self.assertEqual(subs, [])

    def test_pop_deletes_notified_subscribers(self):
        logic_subscribe(self.db, 1, 100)
        _pop_subscribers_for_court(self.db, 3)
        count = self.db.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
        self.assertEqual(count, 0)

    def test_pop_does_not_delete_unmatched_subscribers(self):
        logic_subscribe(self.db, 1, 100, court_number=5)
        _pop_subscribers_for_court(self.db, 3)
        count = self.db.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
        self.assertEqual(count, 1)


# ─────────────────────────────────────────────────────────────
# Pet tier math
# ─────────────────────────────────────────────────────────────
class TestPetTierMath(unittest.TestCase):
    def test_tier_zero_at_xp_zero(self):
        self.assertEqual(_pet_tier(0), 0)

    def test_tier_zero_just_before_threshold(self):
        self.assertEqual(_pet_tier(19), 0)

    def test_tier_one_at_threshold(self):
        self.assertEqual(_pet_tier(20), 1)

    def test_tier_one_just_before_next(self):
        self.assertEqual(_pet_tier(59), 1)

    def test_tier_two_at_threshold(self):
        self.assertEqual(_pet_tier(60), 2)

    def test_tier_three_at_threshold(self):
        # cumulative: 20+40+60 = 120
        self.assertEqual(_pet_tier(120), 3)

    def test_tier_four_at_threshold(self):
        # cumulative: 20+40+60+80 = 200
        self.assertEqual(_pet_tier(200), 4)

    def test_xp_for_tier_zero(self):
        self.assertEqual(_xp_for_tier(0), 0)

    def test_xp_for_tier_one(self):
        self.assertEqual(_xp_for_tier(1), 20)

    def test_xp_for_tier_two(self):
        self.assertEqual(_xp_for_tier(2), 60)

    def test_xp_for_tier_three(self):
        self.assertEqual(_xp_for_tier(3), 120)

    def test_xp_to_next_from_zero(self):
        self.assertEqual(_xp_to_next_tier(0), 20)

    def test_xp_to_next_mid_tier(self):
        self.assertEqual(_xp_to_next_tier(10), 10)

    def test_xp_to_next_at_boundary(self):
        # Just reached tier 1 (xp=20), needs 40 more to reach tier 2 (xp=60)
        self.assertEqual(_xp_to_next_tier(20), 40)

    def test_xp_to_next_at_max_tier(self):
        # At or beyond max tier returns 0
        max_xp = _xp_for_tier(len(PET_TIERS) - 1) + 100
        self.assertEqual(_xp_to_next_tier(max_xp), 0)

    def test_tier_capped_at_max(self):
        self.assertEqual(_pet_tier(999999), len(PET_TIERS) - 1)

    def test_formula_is_consistent(self):
        # xp_for_tier(n) should always get you exactly to tier n
        for n in range(len(PET_TIERS)):
            self.assertEqual(_pet_tier(_xp_for_tier(n)), n)


# ─────────────────────────────────────────────────────────────
# Pet hash
# ─────────────────────────────────────────────────────────────
class TestPetHash(unittest.TestCase):
    def test_returns_eight_chars(self):
        self.assertEqual(len(_pet_hash(1, 5)), 8)

    def test_deterministic(self):
        self.assertEqual(_pet_hash(1, 5), _pet_hash(1, 5))

    def test_changes_with_user_id(self):
        self.assertNotEqual(_pet_hash(1, 5), _pet_hash(2, 5))

    def test_changes_with_tier(self):
        self.assertNotEqual(_pet_hash(1, 5), _pet_hash(1, 6))

    def test_hex_only(self):
        self.assertTrue(all(c in "0123456789abcdef" for c in _pet_hash(1, 0)))

    def test_stable_regardless_of_food_or_partial_xp(self):
        # Hash only depends on user_id and tier, not food or XP within the tier
        self.assertEqual(_pet_hash(42, 3), _pet_hash(42, 3))


# ─────────────────────────────────────────────────────────────
# _get_or_create_pet
# ─────────────────────────────────────────────────────────────
class TestGetOrCreatePet(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_creates_pet_if_missing(self):
        p = _get_or_create_pet(self.db, 1, "Alice")
        self.assertEqual(p["user_id"], 1)
        self.assertEqual(p["pet_name"], "Pet")
        self.assertEqual(p["experience"], 0)
        self.assertEqual(p["food"], 0)
        self.assertIsNone(p["last_cmd_food"])

    def test_returns_existing_pet(self):
        _get_or_create_pet(self.db, 1, "Alice")
        self.db.execute("UPDATE pets SET food=99 WHERE user_id=1")
        self.db.commit()
        p = _get_or_create_pet(self.db, 1, "Alice")
        self.assertEqual(p["food"], 99)

    def test_idempotent(self):
        _get_or_create_pet(self.db, 1, "Alice")
        _get_or_create_pet(self.db, 1, "Alice")
        count = self.db.execute("SELECT COUNT(*) FROM pets WHERE user_id=1").fetchone()[0]
        self.assertEqual(count, 1)


# ─────────────────────────────────────────────────────────────
# _award_cmd_food
# ─────────────────────────────────────────────────────────────
class TestAwardCmdFood(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_first_award_succeeds(self):
        got = _award_cmd_food(self.db, 1, "Alice")
        self.assertTrue(got)
        p = _get_or_create_pet(self.db, 1, "Alice")
        self.assertEqual(p["food"], 1)

    def test_second_award_within_cooldown_blocked(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        _award_cmd_food(self.db, 1, "Alice", now=now)
        soon = now + timedelta(seconds=CMD_FOOD_COOLDOWN_SECS - 1)
        got = _award_cmd_food(self.db, 1, "Alice", now=soon)
        self.assertFalse(got)
        p = _get_or_create_pet(self.db, 1, "Alice")
        self.assertEqual(p["food"], 1)

    def test_award_after_cooldown_succeeds(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        _award_cmd_food(self.db, 1, "Alice", now=now)
        later = now + timedelta(seconds=CMD_FOOD_COOLDOWN_SECS)
        got = _award_cmd_food(self.db, 1, "Alice", now=later)
        self.assertTrue(got)
        p = _get_or_create_pet(self.db, 1, "Alice")
        self.assertEqual(p["food"], 2)

    def test_different_users_independent(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        _award_cmd_food(self.db, 1, "Alice", now=now)
        got = _award_cmd_food(self.db, 2, "Bob", now=now)
        self.assertTrue(got)


# ─────────────────────────────────────────────────────────────
# logic_pet
# ─────────────────────────────────────────────────────────────
class TestLogicPet(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_creates_pet_on_first_call(self):
        r = logic_pet(self.db, 1, "Alice")
        self.assertIsNone(r["error"])
        self.assertEqual(r["pet_name"], "Pet")
        self.assertEqual(r["tier"], 0)
        self.assertEqual(r["xp"], 0)
        self.assertEqual(r["food"], 0)
        self.assertFalse(r["at_max"])

    def test_emoji_matches_tier(self):
        r = logic_pet(self.db, 1, "Alice")
        self.assertEqual(r["emoji"], PET_TIERS[r["tier"]])

    def test_hash_is_eight_chars(self):
        r = logic_pet(self.db, 1, "Alice")
        self.assertEqual(len(r["hash"]), 8)

    def test_hash_matches_pet_hash_function(self):
        r = logic_pet(self.db, 1, "Alice")
        self.assertEqual(r["hash"], _pet_hash(1, r["tier"]))

    def test_xp_to_next_at_zero(self):
        r = logic_pet(self.db, 1, "Alice")
        self.assertEqual(r["xp_to_next"], 20)

    def test_reflects_current_xp(self):
        _get_or_create_pet(self.db, 1, "Alice")
        self.db.execute("UPDATE pets SET experience=60 WHERE user_id=1")
        self.db.commit()
        r = logic_pet(self.db, 1, "Alice")
        self.assertEqual(r["tier"], 2)
        self.assertEqual(r["emoji"], PET_TIERS[2])


# ─────────────────────────────────────────────────────────────
# logic_food
# ─────────────────────────────────────────────────────────────
class TestLogicFood(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_zero_on_new_user(self):
        r = logic_food(self.db, 1, "Alice")
        self.assertIsNone(r["error"])
        self.assertEqual(r["food"], 0)

    def test_reflects_stored_food(self):
        _get_or_create_pet(self.db, 1, "Alice")
        self.db.execute("UPDATE pets SET food=42 WHERE user_id=1")
        self.db.commit()
        r = logic_food(self.db, 1, "Alice")
        self.assertEqual(r["food"], 42)


# ─────────────────────────────────────────────────────────────
# logic_feed
# ─────────────────────────────────────────────────────────────
class TestLogicFeed(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        _get_or_create_pet(self.db, 1, "Alice")

    def _set_food(self, amount):
        self.db.execute("UPDATE pets SET food=? WHERE user_id=1", (amount,))
        self.db.commit()

    def _set_xp(self, xp):
        self.db.execute("UPDATE pets SET experience=? WHERE user_id=1", (xp,))
        self.db.commit()

    def test_error_no_food(self):
        r = logic_feed(self.db, 1, "Alice", 1)
        self.assertEqual(r["error"], "no_food")

    def test_error_zero_amount(self):
        self._set_food(10)
        r = logic_feed(self.db, 1, "Alice", 0)
        self.assertIsNotNone(r["error"])

    def test_error_negative_amount(self):
        self._set_food(10)
        r = logic_feed(self.db, 1, "Alice", -1)
        self.assertIsNotNone(r["error"])

    def test_feed_one(self):
        self._set_food(5)
        r = logic_feed(self.db, 1, "Alice", 1)
        self.assertIsNone(r["error"])
        self.assertEqual(r["fed"], 1)
        self.assertEqual(r["food_left"], 4)
        self.assertEqual(r["new_xp"], 1)

    def test_feed_all_when_amount_none(self):
        self._set_food(10)
        r = logic_feed(self.db, 1, "Alice", None)
        self.assertIsNone(r["error"])
        self.assertEqual(r["fed"], 10)
        self.assertEqual(r["food_left"], 0)

    def test_feed_capped_at_available_food(self):
        self._set_food(3)
        r = logic_feed(self.db, 1, "Alice", 100)
        self.assertIsNone(r["error"])
        self.assertEqual(r["fed"], 3)

    def test_food_deducted_from_db(self):
        self._set_food(10)
        logic_feed(self.db, 1, "Alice", 4)
        food = self.db.execute("SELECT food FROM pets WHERE user_id=1").fetchone()[0]
        self.assertEqual(food, 6)

    def test_xp_added_to_db(self):
        self._set_food(10)
        logic_feed(self.db, 1, "Alice", 5)
        xp = self.db.execute("SELECT experience FROM pets WHERE user_id=1").fetchone()[0]
        self.assertEqual(xp, 5)

    def test_grew_flag_false_when_no_tier_change(self):
        self._set_food(5)
        r = logic_feed(self.db, 1, "Alice", 5)
        self.assertFalse(r["grew"])

    def test_grew_flag_true_when_tier_increases(self):
        self._set_food(20)
        r = logic_feed(self.db, 1, "Alice", 20)
        self.assertTrue(r["grew"])
        self.assertEqual(r["new_tier"], 1)

    def test_food_emoji_in_result(self):
        self._set_food(5)
        r = logic_feed(self.db, 1, "Alice", 1)
        self.assertIn("food_emoji", r)
        self.assertTrue(len(r["food_emoji"]) > 0)

    def test_at_max_when_legendary(self):
        self._set_food(10000)
        self.db.execute("UPDATE pets SET experience=99999 WHERE user_id=1")
        self.db.commit()
        r = logic_feed(self.db, 1, "Alice", 1)
        self.assertTrue(r["at_max"])


# ─────────────────────────────────────────────────────────────
# logic_whistle
# ─────────────────────────────────────────────────────────────
class TestLogicWhistle(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def _make_pet_at_tier(self, db, user_id, tier, name="Pet"):
        _get_or_create_pet(db, user_id, "Alice")
        xp = _xp_for_tier(tier)
        db.execute("UPDATE pets SET experience=?, pet_name=? WHERE user_id=?", (xp, name, user_id))
        db.commit()

    def test_valid_code_and_type_recovers(self):
        self._make_pet_at_tier(self.db, 1, 3)
        code = _pet_hash(1, 3)
        r = logic_whistle(self.db, 1, "Alice", code, PET_TIERS[3])
        self.assertIsNone(r["error"])
        self.assertEqual(r["tier"], 3)

    def test_wrong_code_returns_not_found(self):
        r = logic_whistle(self.db, 1, "Alice", "00000000", PET_TIERS[0])
        self.assertEqual(r["error"], "not_found")

    def test_wrong_pet_type_returns_not_found(self):
        code = _pet_hash(1, 3)
        r = logic_whistle(self.db, 1, "Alice", code, PET_TIERS[2])  # wrong tier
        self.assertEqual(r["error"], "not_found")

    def test_unknown_pet_type_returns_error(self):
        r = logic_whistle(self.db, 1, "Alice", "00000000", "🤖")
        self.assertIsNotNone(r["error"])
        self.assertNotEqual(r["error"], "not_found")

    def test_recovery_sets_xp_to_tier_floor(self):
        self._make_pet_at_tier(self.db, 1, 5)
        # Give extra XP within the tier
        self.db.execute("UPDATE pets SET experience=experience+10 WHERE user_id=1")
        self.db.commit()
        code = _pet_hash(1, 5)
        logic_whistle(self.db, 1, "Alice", code, PET_TIERS[5])
        xp = self.db.execute("SELECT experience FROM pets WHERE user_id=1").fetchone()[0]
        self.assertEqual(xp, _xp_for_tier(5))

    def test_recovery_sets_food_to_zero(self):
        self._make_pet_at_tier(self.db, 1, 2)
        self.db.execute("UPDATE pets SET food=50 WHERE user_id=1")
        self.db.commit()
        code = _pet_hash(1, 2)
        logic_whistle(self.db, 1, "Alice", code, PET_TIERS[2])
        food = self.db.execute("SELECT food FROM pets WHERE user_id=1").fetchone()[0]
        self.assertEqual(food, 0)

    def test_code_stable_across_food_and_partial_xp_changes(self):
        # Code should be the same regardless of food or XP within the same tier
        self._make_pet_at_tier(self.db, 1, 4)
        code_before = _pet_hash(1, 4)
        self.db.execute("UPDATE pets SET food=99, experience=experience+5 WHERE user_id=1")
        self.db.commit()
        # Hash is still valid because tier hasn't changed
        r = logic_whistle(self.db, 1, "Alice", code_before, PET_TIERS[4])
        self.assertIsNone(r["error"])

    def test_cross_database_recovery(self):
        """A pet created on db_a can be recovered on a fresh db_b using only the hash."""
        db_a = make_db()
        db_b = make_db()

        # User builds up a tier-3 pet on db_a with lots of food and partial XP
        self._make_pet_at_tier(db_a, 42, 3, name="Sparky")
        db_a.execute("UPDATE pets SET food=100, experience=experience+15 WHERE user_id=42")
        db_a.commit()

        # User notes their recovery code from /pet on db_a
        code = _pet_hash(42, 3)
        emoji = PET_TIERS[3]

        # db_b is a fresh host — user has no record there
        self.assertIsNone(db_b.execute("SELECT * FROM pets WHERE user_id=42").fetchone())

        # User whistles on db_b
        r = logic_whistle(db_b, 42, "Alice", code, emoji)
        self.assertIsNone(r["error"])
        self.assertEqual(r["tier"], 3)

        # Pet exists on db_b at tier floor — no food, no partial XP
        row = db_b.execute("SELECT experience, food FROM pets WHERE user_id=42").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], _xp_for_tier(3))  # floor XP, no partial
        self.assertEqual(row[1], 0)                 # no food recovery


# ─────────────────────────────────────────────────────────────
# logic_rename
# ─────────────────────────────────────────────────────────────
class TestLogicRename(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_success(self):
        r = logic_rename(self.db, 1, "Alice", "Fluffy")
        self.assertIsNone(r["error"])
        self.assertEqual(r["pet_name"], "Fluffy")

    def test_name_persisted(self):
        logic_rename(self.db, 1, "Alice", "Sparky")
        name = self.db.execute("SELECT pet_name FROM pets WHERE user_id=1").fetchone()[0]
        self.assertEqual(name, "Sparky")

    def test_error_empty_name(self):
        r = logic_rename(self.db, 1, "Alice", "   ")
        self.assertIsNotNone(r["error"])

    def test_error_name_too_long(self):
        r = logic_rename(self.db, 1, "Alice", "x" * (MAX_PET_NAME_LEN + 1))
        self.assertIsNotNone(r["error"])

    def test_max_length_name_accepted(self):
        r = logic_rename(self.db, 1, "Alice", "x" * MAX_PET_NAME_LEN)
        self.assertIsNone(r["error"])

    def test_creates_pet_if_missing(self):
        logic_rename(self.db, 99, "New", "Buddy")
        row = self.db.execute("SELECT pet_name FROM pets WHERE user_id=99").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Buddy")


# ─────────────────────────────────────────────────────────────
# Registration gate (MIN_PASSWORDS_FOR_NEW_RES)
# ─────────────────────────────────────────────────────────────
class TestRegisterGate(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_first_reservation_allowed(self):
        r = logic_register(self.db, 1, "Alice", 100, 1, 0, 0.0)
        self.assertIsNone(r["error"])

    def test_second_reservation_blocked_when_no_passwords(self):
        logic_register(self.db, 1, "Alice", 100, 1, 0, 0.0)
        r = logic_register(self.db, 1, "Alice", 100, 1, 0, 0.0)
        self.assertEqual(r["error"], "REMIND")

    def test_second_reservation_blocked_with_one_password(self):
        r1 = logic_register(self.db, 1, "Alice", 100, 1, 0, 0.0)
        self.db.execute(
            "UPDATE reservations SET passwords_used=1 WHERE id=?", (r1["res_id"],)
        )
        self.db.commit()
        r = logic_register(self.db, 1, "Alice", 100, 1, 0, 0.0)
        self.assertEqual(r["error"], "REMIND")

    def test_second_reservation_allowed_with_two_passwords(self):
        r1 = logic_register(self.db, 1, "Alice", 100, 1, 0, 0.0)
        self.db.execute(
            "UPDATE reservations SET passwords_used=2 WHERE id=?", (r1["res_id"],)
        )
        self.db.commit()
        r = logic_register(self.db, 1, "Alice", 100, 1, 0, 0.0)
        self.assertIsNone(r["error"])

    def test_different_users_independent(self):
        logic_register(self.db, 1, "Alice", 100, 1, 0, 0.0)
        r = logic_register(self.db, 2, "Bob", 100, 1, 0, 0.0)
        self.assertIsNone(r["error"])

    def test_food_awarded_on_success(self):
        r = logic_register(self.db, 1, "Alice", 100, 1, 0, 0.0)
        self.assertIsNone(r["error"])
        self.assertEqual(r["food_awarded"], FOOD_PER_RESERVATION)

    def test_pet_food_updated_in_db(self):
        logic_register(self.db, 1, "Alice", 100, 1, 0, 0.0)
        food = self.db.execute("SELECT food FROM pets WHERE user_id=1").fetchone()[0]
        self.assertEqual(food, FOOD_PER_RESERVATION)


# ─────────────────────────────────────────────────────────────
# usepassword: passwords_used increment and food award
# ─────────────────────────────────────────────────────────────
class TestUsePasswordExtras(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_passwords_used_incremented(self):
        res_id = insert_res(self.db, user_id=1)
        insert_pw(self.db, username="u1")
        logic_usepassword(self.db, res_id, "u1", 1)
        used = self.db.execute(
            "SELECT passwords_used FROM reservations WHERE id=?", (res_id,)
        ).fetchone()[0]
        self.assertEqual(used, 1)

    def test_passwords_used_increments_twice(self):
        res_id = insert_res(self.db, user_id=1)
        insert_pw(self.db, username="u1")
        insert_pw(self.db, username="u2")
        logic_usepassword(self.db, res_id, "u1", 1)
        logic_usepassword(self.db, res_id, "u2", 1)
        used = self.db.execute(
            "SELECT passwords_used FROM reservations WHERE id=?", (res_id,)
        ).fetchone()[0]
        self.assertEqual(used, 2)

    def test_food_awarded_to_caller(self):
        res_id = insert_res(self.db, user_id=1)
        insert_pw(self.db, username="u1")
        logic_usepassword(self.db, res_id, "u1", 1)
        food = self.db.execute("SELECT food FROM pets WHERE user_id=1").fetchone()[0]
        self.assertEqual(food, FOOD_PER_PASSWORD)

    def test_food_stacks_with_multiple_calls(self):
        res_id = insert_res(self.db, user_id=1)
        insert_pw(self.db, username="u1")
        insert_pw(self.db, username="u2")
        logic_usepassword(self.db, res_id, "u1", 1)
        logic_usepassword(self.db, res_id, "u2", 1)
        food = self.db.execute("SELECT food FROM pets WHERE user_id=1").fetchone()[0]
        self.assertEqual(food, FOOD_PER_PASSWORD * 2)


# ─────────────────────────────────────────────────────────────
# delete_expired_passwords (startup cleanup)
# ─────────────────────────────────────────────────────────────
class TestDeleteExpiredPasswords(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def _insert_pw_at(self, added_at: datetime, username="u1") -> int:
        cur = self.db.execute(
            "INSERT INTO passwords (username, password, added_by, added_at) VALUES (?, ?, ?, ?)",
            (username, "pass", "Bot", added_at.isoformat()),
        )
        self.db.commit()
        return cur.lastrowid

    def _noon_pt(self, days_ago=0) -> datetime:
        today = datetime.now(PACIFIC).date()
        d = today - timedelta(days=days_ago)
        return datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=PACIFIC)

    def test_deletes_password_from_yesterday(self):
        self._insert_pw_at(self._noon_pt(days_ago=1).astimezone().replace(tzinfo=None))
        now_pt = datetime.now(PACIFIC)
        deleted = delete_expired_passwords(self.db, now_pt=now_pt)
        self.assertEqual(deleted, 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM passwords").fetchone()[0], 0)

    def test_keeps_password_from_today(self):
        self._insert_pw_at(self._noon_pt(days_ago=0).astimezone().replace(tzinfo=None))
        now_pt = datetime.now(PACIFIC)
        deleted = delete_expired_passwords(self.db, now_pt=now_pt)
        self.assertEqual(deleted, 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM passwords").fetchone()[0], 1)

    def test_deletes_multiple_old_passwords(self):
        self._insert_pw_at(self._noon_pt(days_ago=2).astimezone().replace(tzinfo=None), "u1")
        self._insert_pw_at(self._noon_pt(days_ago=3).astimezone().replace(tzinfo=None), "u2")
        self._insert_pw_at(self._noon_pt(days_ago=0).astimezone().replace(tzinfo=None), "u3")
        deleted = delete_expired_passwords(self.db, now_pt=datetime.now(PACIFIC))
        self.assertEqual(deleted, 2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM passwords").fetchone()[0], 1)

    def test_no_passwords_returns_zero(self):
        deleted = delete_expired_passwords(self.db, now_pt=datetime.now(PACIFIC))
        self.assertEqual(deleted, 0)

    def test_idempotent(self):
        self._insert_pw_at(self._noon_pt(days_ago=1).astimezone().replace(tzinfo=None))
        now_pt = datetime.now(PACIFIC)
        delete_expired_passwords(self.db, now_pt=now_pt)
        deleted2 = delete_expired_passwords(self.db, now_pt=now_pt)
        self.assertEqual(deleted2, 0)


if __name__ == "__main__":
    unittest.main()
