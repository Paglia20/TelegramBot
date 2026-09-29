import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import httpx

import config
from models import FetchResult, Listing
from storage import Storage

BASE_CONFIG = {
    "NOTIFY_OLD_LISTINGS": False,
    "EBAY_ONLY_LOCAL_ITEMS": True,
    "EBAY_QUERY_MODE": "combined",
    "MAX_NOTIFICATIONS_PER_CYCLE": None,
    "DASHBOARD_PUBLIC_URL": "",
    "DASHBOARD_EXTRA_HOSTS": [],
    "DASHBOARD_PORT": 8765,
}


def patch_config(**overrides):
    return mock.patch.multiple(config, **{**BASE_CONFIG, **overrides})


def temp_storage() -> Storage:
    return Storage(Path(tempfile.mkdtemp()) / "test.db")


def make_listing(key="1", title="Nvidia RTX 4090", source="EBAY_IT", origin=None, seller="mario",
                 price=100.0, currency="EUR", minutes_ago=1, **extra) -> Listing:
    return Listing(
        source=source,
        source_name=config.EBAY_MARKETS.get(source, {}).get("name", source),
        item_key=f"ebay:{key}",
        title=title,
        url=f"https://www.ebay.it/itm/{key}",
        price=price,
        currency=currency,
        seller=seller,
        origin=origin or source,
        origin_name=config.EBAY_MARKETS.get(origin or source, {}).get("name", origin or source),
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        **extra,
    )


def add_old_keyword(store: Storage, term: str, hours_ago: float = 1.0):
    store.add_keyword(term)
    store.db.execute("UPDATE keywords SET created_at=? WHERE term=?", (time.time() - hours_ago * 3600, term))


def ebay_item(legacy, title, price=100, currency="EUR", origin="EBAY_IT", seller="mario", minutes_ago=1,
              auction=False, country="IT"):
    created = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    item = {
        "itemId": f"v1|{legacy}|0",
        "legacyItemId": str(legacy),
        "title": title,
        "itemWebUrl": f"https://www.ebay.it/itm/{legacy}?hash=abc&_trkparms=x",
        "buyingOptions": ["AUCTION"] if auction else ["FIXED_PRICE"],
        "image": {"imageUrl": f"https://i.ebayimg.com/images/g/{legacy}/s-l225.jpg"},
        "additionalImages": [{"imageUrl": f"https://i.ebayimg.com/images/g/{legacy}b/s-l140.jpg"}],
        "listingMarketplaceId": origin,
        "seller": {"username": seller},
        "itemLocation": {"country": country},
        "itemCreationDate": created,
    }
    price_obj = {"value": str(price), "currency": currency}
    if auction:
        item["currentBidPrice"] = price_obj
        item["bidCount"] = 3
    else:
        item["price"] = price_obj
    return item


class FakeEbay:
    def __init__(self, catalog=None):
        self.catalog = catalog or {}
        self.token_calls = 0
        self.searches = []
        self.status_override = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            self.token_calls += 1
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        if request.url.path.endswith("/item_summary/search"):
            market = request.headers["X-EBAY-C-MARKETPLACE-ID"]
            self.searches.append((market, dict(request.url.params)))
            if market in self.status_override:
                return self.status_override[market]
            items = self.catalog.get(market, [])
            offset = int(request.url.params.get("offset", 0))
            limit = int(request.url.params.get("limit", 50))
            return httpx.Response(200, json={"itemSummaries": items[offset:offset + limit]})
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


class FakeNotifier:
    def __init__(self, fail=False):
        self.sent = []
        self.alerts = []
        self.fail = fail

    async def send_listing(self, item, keyword, old_price=None):
        if self.fail:
            return False
        self.sent.append((item, keyword, old_price))
        return True

    async def alert(self, text):
        self.alerts.append(text)


class FakeSource:
    quota_group = "ebay"

    def __init__(self, market_id, listings=None, error=None):
        self.id = market_id
        self.name = config.EBAY_MARKETS[market_id]["name"]
        self.listings = listings or []
        self.error = error
        self.calls = 0

    def requests_per_cycle(self, keywords):
        return 1 if keywords else 0

    async def fetch(self, keywords):
        self.calls += 1
        if self.error:
            raise self.error
        return FetchResult(list(self.listings), 1)
