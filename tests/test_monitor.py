import asyncio
import math
import time
import unittest

import config
from monitor import Monitor
from sources import QuotaExceeded, SourceError
from sources.ebay import EbayAuth, EbaySource
from tests.helpers import (FakeEbay, FakeNotifier, FakeSource, add_old_keyword, ebay_item, make_listing,
                           patch_config, temp_storage)


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.patcher = patch_config()
        self.patcher.start()
        self.store = temp_storage()
        self.monitor = Monitor(self.store, FakeNotifier(), {m: FakeSource(m) for m in config.EBAY_MARKETS})

    def tearDown(self):
        self.patcher.stop()

    def test_no_keywords_no_calls(self):
        self.assertEqual(self.monitor.budget().calls_per_cycle, 0)

    def test_timer_is_raised_to_respect_the_quota(self):
        self.store.add_keyword("RTX 4090")
        self.store.interval = 60
        budget = self.monitor.budget()
        markets = len(config.EBAY_MARKETS)
        expected = math.ceil(markets * 86400 / (config.EBAY_DAILY_CALL_LIMIT * config.EBAY_BUDGET_SAFETY))
        self.assertEqual(budget.calls_per_cycle, markets)
        self.assertEqual(budget.min_interval, expected)
        self.assertEqual(budget.effective, expected)
        self.assertLessEqual(budget.daily_calls, config.EBAY_DAILY_CALL_LIMIT)

    def test_long_timer_is_kept(self):
        self.store.add_keyword("RTX 4090")
        self.store.interval = 600
        self.assertEqual(self.monitor.budget().effective, 600)

    def test_disabled_markets_do_not_count(self):
        self.store.add_keyword("RTX 4090")
        for m in list(config.EBAY_MARKETS)[1:]:
            self.store.toggle_market(m)
        self.assertEqual(self.monitor.budget().calls_per_cycle, 1)


class CycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patcher = patch_config()
        self.patcher.start()
        self.store = temp_storage()
        self.notifier = FakeNotifier()

    async def asyncTearDown(self):
        self.patcher.stop()

    def monitor(self, sources):
        return Monitor(self.store, self.notifier, {s.id: s for s in sources})

    def sent_keys(self):
        return [item.item_key for item, _, _ in self.notifier.sent]

    async def test_new_listing_is_notified_once(self):
        add_old_keyword(self.store, "RTX 4090")
        mon = self.monitor([FakeSource("EBAY_IT", [make_listing("1")])])
        await mon.run_cycle()
        await mon.run_cycle()
        self.assertEqual(self.sent_keys(), ["ebay:1"])
        self.assertIsNotNone(self.store.get_seen("ebay:1"))

    async def test_non_matching_and_excluded_listings_are_ignored(self):
        add_old_keyword(self.store, "RTX 4090")
        self.store.add_exclude("cover")
        listings = [make_listing("1", "Game Boy Color"), make_listing("2", "Cover per RTX 4090")]
        stats = await self.monitor([FakeSource("EBAY_IT", listings)]).run_cycle()
        self.assertEqual(self.notifier.sent, [])
        self.assertEqual(stats.matched, 0)

    async def test_listing_older_than_keyword_is_ignored(self):
        self.store.add_keyword("RTX 4090")
        old = make_listing("1", minutes_ago=config.NEW_KEYWORD_LOOKBACK_MINUTES + 30)
        recent = make_listing("2", minutes_ago=config.NEW_KEYWORD_LOOKBACK_MINUTES - 5)
        await self.monitor([FakeSource("EBAY_IT", [old, recent])]).run_cycle()
        self.assertEqual(self.sent_keys(), ["ebay:2"])

    async def test_old_listings_allowed_in_sandbox_mode(self):
        self.store.add_keyword("RTX 4090")
        with patch_config(NOTIFY_OLD_LISTINGS=True):
            await self.monitor([FakeSource("EBAY_IT", [make_listing("1", minutes_ago=10_000)])]).run_cycle()
        self.assertEqual(self.sent_keys(), ["ebay:1"])

    async def test_same_item_on_two_markets_prefers_origin(self):
        add_old_keyword(self.store, "RTX 4090")
        on_de = make_listing("1", source="EBAY_DE", origin="EBAY_IT")
        on_it = make_listing("1", source="EBAY_IT", origin="EBAY_IT")
        await self.monitor([FakeSource("EBAY_DE", [on_de]), FakeSource("EBAY_IT", [on_it])]).run_cycle()
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertEqual(self.notifier.sent[0][0].source, "EBAY_IT")

    async def test_same_seller_and_title_is_a_duplicate(self):
        add_old_keyword(self.store, "RTX 4090")
        a = make_listing("1", source="EBAY_IT", seller="mario")
        b = make_listing("2", source="EBAY_DE", seller="mario")
        stats = await self.monitor([FakeSource("EBAY_IT", [a]), FakeSource("EBAY_DE", [b])]).run_cycle()
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertEqual(stats.duplicates, 1)

    async def test_duplicate_found_in_a_later_cycle(self):
        add_old_keyword(self.store, "RTX 4090")
        src = FakeSource("EBAY_IT", [make_listing("1", seller="mario")])
        mon = self.monitor([src])
        await mon.run_cycle()
        src.listings = [make_listing("2", seller="mario")]
        await mon.run_cycle()
        self.assertEqual(self.sent_keys(), ["ebay:1"])
        self.assertEqual(self.store.get_seen("ebay:2")["dup_of"], "ebay:1")

    async def test_cross_market_dedup_can_be_disabled(self):
        add_old_keyword(self.store, "RTX 4090")
        with patch_config(CROSS_MARKET_DEDUP=False):
            await self.monitor([FakeSource("EBAY_IT", [make_listing("1"), make_listing("2")])]).run_cycle()
        self.assertEqual(sorted(self.sent_keys()), ["ebay:1", "ebay:2"])

    async def test_price_drop_is_notified_once(self):
        add_old_keyword(self.store, "RTX 4090")
        src = FakeSource("EBAY_IT", [make_listing("1", price=1500)])
        mon = self.monitor([src])
        await mon.run_cycle()
        src.listings = [make_listing("1", price=1350)]
        await mon.run_cycle()
        await mon.run_cycle()
        drops = [(item.price, old) for item, _, old in self.notifier.sent if old is not None]
        self.assertEqual(drops, [(1350, 1500)])

    async def test_price_drop_can_be_disabled(self):
        add_old_keyword(self.store, "RTX 4090")
        src = FakeSource("EBAY_IT", [make_listing("1", price=1500)])
        with patch_config(ON_LISTING_CHANGE="ignore"):
            mon = self.monitor([src])
            await mon.run_cycle()
            src.listings = [make_listing("1", price=1000)]
            await mon.run_cycle()
        self.assertEqual(len(self.notifier.sent), 1)

    async def test_failed_notification_is_retried_next_cycle(self):
        add_old_keyword(self.store, "RTX 4090")
        mon = self.monitor([FakeSource("EBAY_IT", [make_listing("1")])])
        self.notifier.fail = True
        await mon.run_cycle()
        self.assertIsNone(self.store.get_seen("ebay:1"))
        self.notifier.fail = False
        await mon.run_cycle()
        self.assertEqual(self.sent_keys(), ["ebay:1"])

    async def test_all_notifications_sent_without_limit(self):
        add_old_keyword(self.store, "Ferrari")
        listings = [make_listing(str(i), f"Ferrari {i}", seller=f"s{i}") for i in range(45)]
        await self.monitor([FakeSource("EBAY_IT", listings)]).run_cycle()
        self.assertEqual(len(self.notifier.sent), 45)

    async def test_limit_keeps_the_rest_for_next_cycle(self):
        add_old_keyword(self.store, "Ferrari")
        listings = [make_listing(str(i), f"Ferrari {i}", seller=f"s{i}") for i in range(8)]
        with patch_config(MAX_NOTIFICATIONS_PER_CYCLE=5):
            mon = self.monitor([FakeSource("EBAY_IT", listings)])
            await mon.run_cycle()
            self.assertEqual(len(self.notifier.sent), 5)
            await mon.run_cycle()
        self.assertEqual(len(self.notifier.sent), 8)
        self.assertEqual(len(set(self.sent_keys())), 8)

    async def test_failing_market_does_not_stop_the_others(self):
        add_old_keyword(self.store, "RTX 4090")
        bad = FakeSource("EBAY_FR", error=SourceError("HTTP 500"))
        good = FakeSource("EBAY_IT", [make_listing("1")])
        mon = self.monitor([bad, good])
        await mon.run_cycle()
        self.assertEqual(self.sent_keys(), ["ebay:1"])
        self.assertEqual(mon.health["EBAY_FR"].failures, 1)
        self.assertGreater(mon.health["EBAY_FR"].next_try, time.time())

    async def test_backoff_grows_and_alert_is_sent_once(self):
        add_old_keyword(self.store, "RTX 4090")
        bad = FakeSource("EBAY_FR", error=SourceError("HTTP 500"))
        mon = self.monitor([bad])
        waits = []
        for _ in range(config.FAILURES_BEFORE_ALERT + 2):
            mon.health["EBAY_FR"].next_try = 0
            before = time.time()
            await mon.run_cycle()
            waits.append(mon.health["EBAY_FR"].next_try - before)
        self.assertLess(waits[0], waits[1])
        self.assertLessEqual(max(waits), config.MAX_BACKOFF_SECONDS + 1)
        self.assertEqual(len([a for a in self.notifier.alerts if "eBay Francia" in a]), 1)

    async def test_recovery_sends_alert(self):
        add_old_keyword(self.store, "RTX 4090")
        src = FakeSource("EBAY_FR", error=SourceError("HTTP 500"))
        mon = self.monitor([src])
        for _ in range(config.FAILURES_BEFORE_ALERT):
            mon.health["EBAY_FR"].next_try = 0
            await mon.run_cycle()
        src.error = None
        mon.health["EBAY_FR"].next_try = 0
        await mon.run_cycle()
        self.assertTrue(any("di nuovo raggiungibile" in a for a in self.notifier.alerts))
        self.assertEqual(mon.health["EBAY_FR"].failures, 0)

    async def test_quota_exceeded_pauses_all_markets(self):
        add_old_keyword(self.store, "RTX 4090")
        src = FakeSource("EBAY_IT", error=QuotaExceeded("quota", retry_after=120))
        other = FakeSource("EBAY_DE")
        mon = self.monitor([src, other])
        await mon.run_cycle()
        self.assertGreater(mon.quota_pause_until["ebay"], time.time() + 100)
        other.calls = 0
        stats = await mon.run_cycle()
        self.assertEqual(other.calls, 0)
        self.assertNotEqual(stats.skipped_reason, "")
        self.assertEqual(len(self.notifier.alerts), 1)

    async def test_skip_reasons(self):
        mon = self.monitor([FakeSource("EBAY_IT")])
        self.assertEqual((await mon.run_cycle()).skipped_reason, "nessuna parola da monitorare")
        self.store.add_keyword("x")
        self.store.paused = True
        self.assertEqual((await mon.run_cycle()).skipped_reason, "in pausa")
        self.store.paused = False
        missing = Monitor(self.store, self.notifier, {}, missing_credentials=True)
        self.assertIn("credenziali", (await missing.run_cycle()).skipped_reason)

    async def test_disabled_market_is_not_called(self):
        add_old_keyword(self.store, "RTX 4090")
        src = FakeSource("EBAY_FR")
        self.store.toggle_market("EBAY_FR")
        await self.monitor([src, FakeSource("EBAY_IT")]).run_cycle()
        self.assertEqual(src.calls, 0)

    async def test_api_calls_are_counted(self):
        add_old_keyword(self.store, "RTX 4090")
        await self.monitor([FakeSource("EBAY_IT"), FakeSource("EBAY_DE")]).run_cycle()
        self.assertEqual(self.store.api_calls_today(), 2)

    async def test_run_forever_starts_and_stops(self):
        mon = self.monitor([FakeSource("EBAY_IT")])
        task = asyncio.create_task(mon.run_forever())
        await asyncio.sleep(0.2)
        self.assertGreater(mon.next_cycle_at, time.time())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


class PauseAndStopTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patcher = patch_config()
        self.patcher.start()
        self.store = temp_storage()
        add_old_keyword(self.store, "Ferrari")

    async def asyncTearDown(self):
        self.patcher.stop()

    def listings(self, n):
        return [make_listing(str(i), f"Ferrari {i}", seller=f"s{i}") for i in range(n)]

    async def test_pause_during_fetch_sends_nothing(self):
        store = self.store

        class PausingSource(FakeSource):
            async def fetch(self, keywords):
                store.paused = True
                return await super().fetch(keywords)

        notifier = FakeNotifier()
        mon = Monitor(store, notifier, {"EBAY_IT": PausingSource("EBAY_IT", self.listings(3))})
        stats = await mon.run_cycle()
        self.assertEqual(notifier.sent, [])
        self.assertIn("pausa", stats.skipped_reason)
        self.assertEqual(store.api_calls_today(), 1)

    async def test_pause_while_sending_stops_and_resumes_later(self):
        store = self.store

        class PausingNotifier(FakeNotifier):
            async def send_listing(self, item, keyword, old_price=None):
                ok = await super().send_listing(item, keyword, old_price)
                if len(self.sent) == 2:
                    store.paused = True
                return ok

        notifier = PausingNotifier()
        src = FakeSource("EBAY_IT", self.listings(5))
        mon = Monitor(store, notifier, {"EBAY_IT": src})
        await mon.run_cycle()
        self.assertEqual(len(notifier.sent), 2)
        self.assertEqual(len(mon.backlog), 3)
        src.listings = []
        self.assertEqual((await mon.run_cycle()).skipped_reason, "in pausa")
        self.assertEqual(len(notifier.sent), 2)
        store.paused = False
        await mon.run_cycle()
        self.assertEqual(len(notifier.sent), 5)
        self.assertEqual(len({i.item_key for i, _, _ in notifier.sent}), 5)

    async def test_request_stop_ends_run_forever_cleanly(self):
        mon = Monitor(self.store, FakeNotifier(), {"EBAY_IT": FakeSource("EBAY_IT")})
        task = asyncio.create_task(mon.run_forever())
        await asyncio.sleep(0.1)
        mon.request_stop()
        await asyncio.wait_for(task, timeout=2)
        self.assertTrue(task.done())
        self.assertFalse(task.cancelled())

    async def test_stop_during_sending_keeps_remaining_unsent(self):
        holder = {}

        class StoppingNotifier(FakeNotifier):
            async def send_listing(self, item, keyword, old_price=None):
                ok = await super().send_listing(item, keyword, old_price)
                holder["mon"].request_stop()
                return ok

        notifier = StoppingNotifier()
        mon = Monitor(self.store, notifier, {"EBAY_IT": FakeSource("EBAY_IT", self.listings(4))})
        holder["mon"] = mon
        await mon.run_cycle()
        self.assertEqual(len(notifier.sent), 1)
        self.assertEqual(self.store.seen_count(), 1)

    async def test_matches_are_counted_per_market(self):
        it = [make_listing("1", "Ferrari F40", seller="a"), make_listing("2", "Game Boy", seller="b")]
        de = [make_listing("1", "Ferrari F40", source="EBAY_DE", origin="EBAY_IT", seller="a"),
              make_listing("3", "Ferrari 360", source="EBAY_DE", seller="c"),
              make_listing("4", "Ferrari 458", source="EBAY_DE", seller="d")]
        mon = Monitor(self.store, FakeNotifier(), {"EBAY_IT": FakeSource("EBAY_IT", it),
                                                   "EBAY_DE": FakeSource("EBAY_DE", de),
                                                   "EBAY_FR": FakeSource("EBAY_FR", error=SourceError("x"))})
        await mon.run_cycle()
        self.assertEqual(mon.health["EBAY_IT"].last_matches, 1)
        self.assertEqual(mon.health["EBAY_DE"].last_matches, 3)
        self.assertEqual(mon.health["EBAY_FR"].last_matches, 0)


class EndToEndTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_ebay_source_with_fake_api(self):
        with patch_config():
            store = temp_storage()
            add_old_keyword(store, "RTX 4090")
            fake = FakeEbay({
                "EBAY_IT": [ebay_item(111, "Nvidia RTX 4090 Founders", seller="mario")],
                "EBAY_DE": [ebay_item(111, "Nvidia RTX 4090 Founders", seller="mario"),
                            ebay_item(333, "Nvidia RTX 4090 Founders", origin="EBAY_DE", seller="mario", country="DE")],
                "EBAY_CH": [ebay_item(666, "RTX 4090 Gaming", currency="CHF", origin="EBAY_CH", seller="urs",
                                      country="CH")],
            })
            async with fake.client() as client:
                auth = EbayAuth(client, "id", "secret")
                notifier = FakeNotifier()
                mon = Monitor(store, notifier, {m: EbaySource(m, auth, client) for m in config.EBAY_MARKETS})
                stats = await mon.run_cycle()
            keys = sorted(item.item_key for item, _, _ in notifier.sent)
            self.assertEqual(keys, ["ebay:111", "ebay:666"])
            self.assertEqual(stats.duplicates, 1)
            self.assertEqual(fake.token_calls, 1)
            self.assertEqual(store.api_calls_today(), len(config.EBAY_MARKETS))


if __name__ == "__main__":
    unittest.main()
