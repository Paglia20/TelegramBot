import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import config
from storage import Storage
from tests.helpers import make_listing, patch_config, temp_storage


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.store = temp_storage()

    def test_defaults(self):
        self.assertEqual(self.store.interval, config.DEFAULT_INTERVAL_SECONDS)
        self.assertFalse(self.store.paused)
        self.assertEqual(self.store.enabled_markets, list(config.EBAY_MARKETS))

    def test_values_are_saved(self):
        self.store.interval = 300
        self.store.paused = True
        self.assertEqual(self.store.interval, 300)
        self.assertTrue(self.store.paused)

    def test_values_survive_reopening(self):
        path = Path(tempfile.mkdtemp()) / "x.db"
        first = Storage(path)
        first.interval = 180
        first.close()
        self.assertEqual(Storage(path).interval, 180)

    def test_toggle_market(self):
        self.assertFalse(self.store.toggle_market("EBAY_FR"))
        self.assertNotIn("EBAY_FR", self.store.enabled_markets)
        self.assertTrue(self.store.toggle_market("EBAY_FR"))
        self.assertIn("EBAY_FR", self.store.enabled_markets)

    def test_enabled_markets_keeps_config_order(self):
        self.store.toggle_market("EBAY_IT")
        self.store.toggle_market("EBAY_IT")
        self.assertEqual(self.store.enabled_markets, list(config.EBAY_MARKETS))


class TermsTest(unittest.TestCase):
    def setUp(self):
        self.store = temp_storage()

    def test_add_and_list(self):
        self.assertTrue(self.store.add_keyword("RTX 4090"))
        self.assertTrue(self.store.add_keyword("Game  Boy"))
        self.assertEqual([k.term for k in self.store.keywords()], ["RTX 4090", "Game Boy"])

    def test_duplicates_are_recognised_after_normalisation(self):
        self.store.add_keyword("RTX 4090")
        self.assertFalse(self.store.add_keyword("rtx4090"))
        self.assertFalse(self.store.add_keyword("RTX-4090"))
        self.assertEqual(len(self.store.keywords()), 1)

    def test_empty_term_rejected(self):
        self.assertFalse(self.store.add_keyword("  --  "))

    def test_remove_by_text_and_by_id(self):
        self.store.add_keyword("RTX 4090")
        self.store.add_keyword("Game Boy")
        self.assertEqual(self.store.remove_keyword("rtx4090"), "RTX 4090")
        game_boy_id = self.store.keywords()[0].id
        self.assertEqual(self.store.remove_keyword(game_boy_id), "Game Boy")
        self.assertIsNone(self.store.remove_keyword("inesistente"))
        self.assertEqual(self.store.keywords(), [])

    def test_keyword_records_creation_time(self):
        before = time.time()
        self.store.add_keyword("Steam Deck")
        self.assertGreaterEqual(self.store.keywords()[0].created_at, before)

    def test_excludes_are_separate_from_keywords(self):
        self.store.add_exclude("cover")
        self.assertEqual([t for _, t in self.store.excludes()], ["cover"])
        self.assertEqual(self.store.keywords(), [])
        self.assertEqual(self.store.remove_exclude("COVER"), "cover")


class SeenTest(unittest.TestCase):
    def setUp(self):
        self.store = temp_storage()

    def test_insert_and_get(self):
        item = make_listing("1", images=["https://i.ebayimg.com/a.jpg"])
        self.store.insert_seen(item, "fp1", "RTX 4090")
        row = self.store.get_seen("ebay:1")
        self.assertEqual(row["title"], item.title)
        self.assertEqual(row["image"], "https://i.ebayimg.com/a.jpg")
        self.assertIsNone(row["dup_of"])
        self.assertIsNone(self.store.get_seen("ebay:2"))

    def test_second_insert_is_ignored(self):
        self.store.insert_seen(make_listing("1", price=100), "fp1", "k")
        self.store.insert_seen(make_listing("1", price=50), "fp1", "k")
        self.assertEqual(self.store.get_seen("ebay:1")["price"], 100)
        self.assertEqual(self.store.seen_count(), 1)

    def test_find_fingerprint_respects_window(self):
        self.store.insert_seen(make_listing("1"), "fp1", "k")
        self.assertEqual(self.store.find_fingerprint("fp1", time.time() - 60), "ebay:1")
        self.assertIsNone(self.store.find_fingerprint("fp1", time.time() + 60))
        self.assertIsNone(self.store.find_fingerprint("altro", 0))

    def test_touch_updates_markets_and_price(self):
        self.store.insert_seen(make_listing("1", source="EBAY_IT", price=100), "fp1", "k")
        row = self.store.get_seen("ebay:1")
        self.store.touch_seen(row, make_listing("1", source="EBAY_DE"), new_price=80)
        row = self.store.get_seen("ebay:1")
        self.assertEqual(row["markets"], "EBAY_DE,EBAY_IT")
        self.assertEqual(row["price"], 80)

    def test_prune_removes_only_old_rows(self):
        self.store.insert_seen(make_listing("old"), "a", "k")
        self.store.insert_seen(make_listing("new"), "b", "k")
        self.store.db.execute("UPDATE seen SET last_seen=? WHERE item_key='ebay:old'", (time.time() - 50 * 86400,))
        self.assertEqual(self.store.prune_seen(45), 1)
        self.assertIsNone(self.store.get_seen("ebay:old"))
        self.assertIsNotNone(self.store.get_seen("ebay:new"))

    def test_statistics(self):
        self.store.insert_seen(make_listing("1"), "a", "RTX 4090")
        self.store.insert_seen(make_listing("2"), "b", "RTX 4090")
        self.store.insert_seen(make_listing("3"), "c", "Game Boy")
        self.store.insert_seen(make_listing("4"), "a", "RTX 4090", dup_of="ebay:1")
        now = time.time()
        for key, age in (("1", 20), ("2", 10), ("3", 3 * 86400)):
            self.store.db.execute("UPDATE seen SET first_seen=? WHERE item_key=?", (now - age, f"ebay:{key}"))
        stats = self.store.notification_stats(now - 86400)
        self.assertEqual(stats, {"notified_total": 3, "notified_today": 2, "duplicates_today": 1})
        self.assertEqual(self.store.keyword_counts(), {"RTX 4090": 2, "Game Boy": 1})
        self.assertEqual([r["url"] for r in self.store.recent_notified(2)],
                         ["https://www.ebay.it/itm/2", "https://www.ebay.it/itm/1"])


class UsageAndSessionsTest(unittest.TestCase):
    def setUp(self):
        self.store = temp_storage()

    def test_api_calls_are_summed_per_day(self):
        self.assertEqual(self.store.api_calls_today(), 0)
        self.store.add_api_calls(6)
        self.store.add_api_calls(4)
        self.store.add_api_calls(0)
        self.assertEqual(self.store.api_calls_today(), 10)

    def test_sessions(self):
        self.store.create_session("expired", time.time() - 1)
        self.assertFalse(self.store.session_valid("expired"))
        self.store.create_session("valid", time.time() + 60)
        self.assertTrue(self.store.session_valid("valid"))
        self.assertFalse(self.store.session_valid("sconosciuto"))
        self.assertEqual(self.store.clear_sessions(), 1, "la sessione scaduta va rimossa alla creazione successiva")
        self.assertFalse(self.store.session_valid("valid"))


class UsersAndInvitesTest(unittest.TestCase):
    def setUp(self):
        self.store = temp_storage()

    def test_users(self):
        self.assertTrue(self.store.add_user(99, "Luca", 42))
        self.assertFalse(self.store.add_user(99, "Luca", 42))
        self.assertTrue(self.store.is_user(99))
        self.assertEqual([u["name"] for u in self.store.users()], ["Luca"])
        self.assertEqual(self.store.remove_user(99), "Luca")
        self.assertFalse(self.store.is_user(99))
        self.assertIsNone(self.store.remove_user(99))

    def test_invite_is_single_use(self):
        self.store.create_invite("h", time.time() + 60, 42)
        self.assertEqual(self.store.use_invite("h"), 42)
        self.assertIsNone(self.store.use_invite("h"))
        self.assertIsNone(self.store.use_invite("sconosciuto"))

    def test_expired_invite(self):
        self.store.create_invite("h", time.time() - 1, 42)
        self.assertIsNone(self.store.use_invite("h"))


class MigrationTest(unittest.TestCase):
    def test_old_database_gets_image_column(self):
        path = Path(tempfile.mkdtemp()) / "old.db"
        db = sqlite3.connect(path)
        db.execute(
            "CREATE TABLE seen (item_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, source TEXT NOT NULL, "
            "origin TEXT, markets TEXT NOT NULL, title TEXT NOT NULL, price REAL, currency TEXT, url TEXT NOT NULL, "
            "keyword TEXT NOT NULL, dup_of TEXT, first_seen REAL NOT NULL, last_seen REAL NOT NULL)"
        )
        db.execute("INSERT INTO seen VALUES ('ebay:1','fp','EBAY_IT','EBAY_IT','EBAY_IT','Vecchio',1,'EUR','u','k',NULL,1,1)")
        db.commit()
        db.close()
        store = Storage(path)
        self.assertIsNone(store.get_seen("ebay:1")["image"])
        with patch_config():
            store.insert_seen(make_listing("2", images=["https://x/y.jpg"]), "fp2", "k")
        self.assertEqual(store.get_seen("ebay:2")["image"], "https://x/y.jpg")


if __name__ == "__main__":
    unittest.main()
