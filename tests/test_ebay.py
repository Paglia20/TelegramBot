import asyncio
import unittest
from datetime import datetime, timedelta, timezone

import httpx

import config
from matching import normalize
from models import Keyword
from sources import QuotaExceeded, SourceError
from sources.ebay import EbayAuth, EbaySource, build_queries
from tests.helpers import FakeEbay, ebay_item, patch_config


def kws(*terms):
    return [Keyword(i, t, normalize(t), 0.0) for i, t in enumerate(terms, 1)]


class BuildQueriesTest(unittest.TestCase):
    def test_single_keyword_has_no_parentheses(self):
        with patch_config():
            self.assertEqual(build_queries(kws("RTX 4090")), ["RTX 4090"])

    def test_keywords_are_combined_with_or(self):
        with patch_config():
            self.assertEqual(build_queries(kws("RTX 4090", "Game Boy")), ["(RTX 4090, Game Boy)"])

    def test_groups_respect_count_and_length_limits(self):
        with patch_config():
            queries = build_queries(kws(*[f"parola lunga numero {i}" for i in range(12)]))
            self.assertGreaterEqual(len(queries), 3)
            for q in queries:
                self.assertLessEqual(len(q), config.EBAY_MAX_QUERY_CHARS)
                self.assertLessEqual(q.count(",") + 1, config.EBAY_MAX_KEYWORDS_PER_QUERY)

    def test_special_characters_are_removed(self):
        with patch_config():
            self.assertEqual(build_queries(kws("a,b (c)*")), ["a b c"])

    def test_single_mode(self):
        with patch_config(EBAY_QUERY_MODE="single"):
            self.assertEqual(build_queries(kws("RTX 4090", "Game Boy")), ["RTX 4090", "Game Boy"])


class EbaySourceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patcher = patch_config()
        self.patcher.start()
        self.fake = FakeEbay()
        self.client = self.fake.client()
        self.auth = EbayAuth(self.client, "id", "secret")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.patcher.stop()

    async def test_request_parameters(self):
        self.fake.catalog["EBAY_DE"] = [ebay_item(1, "RTX 4090", country="DE")]
        await EbaySource("EBAY_DE", self.auth, self.client).fetch(kws("RTX 4090"))
        market, params = self.fake.searches[0]
        self.assertEqual(market, "EBAY_DE")
        self.assertEqual(params["q"], "RTX 4090")
        self.assertEqual(params["sort"], "newlyListed")
        self.assertEqual(params["limit"], str(config.EBAY_PAGE_SIZE))
        self.assertEqual(params["filter"], "itemLocationCountry:DE")

    async def test_no_location_filter_when_disabled(self):
        with patch_config(EBAY_ONLY_LOCAL_ITEMS=False):
            await EbaySource("EBAY_IT", self.auth, self.client).fetch(kws("x"))
        self.assertNotIn("filter", self.fake.searches[0][1])

    async def test_one_token_for_parallel_markets(self):
        sources = [EbaySource(m, self.auth, self.client) for m in config.EBAY_MARKETS]
        await asyncio.gather(*(s.fetch(kws("x")) for s in sources))
        self.assertEqual(self.fake.token_calls, 1)
        self.assertEqual(len(self.fake.searches), len(config.EBAY_MARKETS))

    async def test_listing_conversion(self):
        self.fake.catalog["EBAY_DE"] = [ebay_item(111, "Nvidia RTX 4090", price="1499.90", origin="EBAY_IT")]
        result = await EbaySource("EBAY_DE", self.auth, self.client).fetch(kws("RTX 4090"))
        item = result.listings[0]
        self.assertEqual(item.item_key, "ebay:111")
        self.assertEqual(item.url, "https://www.ebay.it/itm/111")
        self.assertEqual(item.price, 1499.90)
        self.assertEqual(item.currency, "EUR")
        self.assertEqual(item.source, "EBAY_DE")
        self.assertEqual(item.origin, "EBAY_IT")
        self.assertEqual(item.origin_name, "eBay Italia")
        self.assertEqual(item.seller, "mario")
        self.assertEqual(len(item.images), 2)
        self.assertTrue(all("/s-l1600." in u for u in item.images))
        self.assertIsNotNone(item.created_at)
        self.assertEqual(result.requests, 1)

    async def test_auction_uses_current_bid(self):
        self.fake.catalog["EBAY_IT"] = [ebay_item(5, "Game Boy", price=60, auction=True)]
        item = (await EbaySource("EBAY_IT", self.auth, self.client).fetch(kws("Game Boy"))).listings[0]
        self.assertTrue(item.is_auction)
        self.assertEqual(item.price, 60)
        self.assertIn("asta, 3 offerte", item.price_text)

    async def test_items_without_id_or_title_are_skipped(self):
        broken = ebay_item(7, "ok")
        del broken["legacyItemId"]
        broken["itemId"] = ""
        self.fake.catalog["EBAY_IT"] = [broken, ebay_item(8, "")]
        result = await EbaySource("EBAY_IT", self.auth, self.client).fetch(kws("x"))
        self.assertEqual(result.listings, [])

    async def test_expired_token_is_renewed_once(self):
        calls = {"search": 0}

        def handler(request):
            if request.url.path.endswith("/oauth2/token"):
                return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
            calls["search"] += 1
            if calls["search"] == 1:
                return httpx.Response(401)
            return httpx.Response(200, json={"itemSummaries": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            src = EbaySource("EBAY_IT", EbayAuth(client, "a", "b"), client)
            await src.fetch(kws("x"))
        self.assertEqual(calls["search"], 2)

    async def test_429_raises_quota_exceeded(self):
        self.fake.status_override["EBAY_IT"] = httpx.Response(429, headers={"Retry-After": "120"})
        with self.assertRaises(QuotaExceeded) as ctx:
            await EbaySource("EBAY_IT", self.auth, self.client).fetch(kws("x"))
        self.assertEqual(ctx.exception.retry_after, 120)

    async def test_server_error_raises_source_error(self):
        self.fake.status_override["EBAY_IT"] = httpx.Response(500, text="boom")
        with self.assertRaises(SourceError):
            await EbaySource("EBAY_IT", self.auth, self.client).fetch(kws("x"))

    async def test_rejected_credentials_give_a_hint(self):
        def handler(request):
            return httpx.Response(401, json={"error": "invalid_client"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch_config(EBAY_SANDBOX=False):
                with self.assertRaises(SourceError) as ctx:
                    await EbayAuth(client, "Gio-x-SBX-1", "s").token()
        self.assertIn("EBAY_ENV=sandbox", str(ctx.exception))


class AdaptivePagingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patcher = patch_config()
        self.patcher.start()
        self.items = []
        self.limits = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        self.source = EbaySource("EBAY_DE", EbayAuth(self.client, "a", "b"), self.client)
        self.add(300, past=True)

    async def asyncTearDown(self):
        await self.client.aclose()
        self.patcher.stop()

    def add(self, n, past=False):
        base = datetime.now(timezone.utc) - (timedelta(days=1) if past else timedelta(0))
        for i in range(n):
            k = len(self.items)
            created = (base - timedelta(microseconds=i)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            self.items.append({"legacyItemId": str(k), "title": f"Ferrari {k}", "itemWebUrl": "https://x/itm/1",
                               "price": {"value": "1", "currency": "EUR"}, "itemCreationDate": created})

    def handler(self, request):
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 7200})
        limit, offset = int(request.url.params["limit"]), int(request.url.params.get("offset", 0))
        self.limits.append(limit)
        ordered = sorted(self.items, key=lambda x: x["itemCreationDate"], reverse=True)
        return httpx.Response(200, json={"itemSummaries": ordered[offset:offset + limit]})

    async def cycle(self, new_items):
        self.add(new_items)
        self.limits.clear()
        await asyncio.sleep(0.01)
        result = await self.source.fetch(kws("Ferrari"))
        return result.requests, list(self.limits)

    async def test_first_cycle_reads_one_page(self):
        self.assertEqual(await self.cycle(0), (1, [50]))

    async def test_busy_search_switches_to_large_pages_and_back(self):
        await self.cycle(0)
        self.assertEqual(await self.cycle(120), (3, [50, 50, 50]))
        self.assertEqual(await self.cycle(120), (1, [200]))
        self.assertEqual(await self.cycle(10), (1, [200]))
        self.assertEqual(await self.cycle(10), (1, [50]))


if __name__ == "__main__":
    unittest.main()
