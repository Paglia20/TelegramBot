import asyncio
import base64
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

import config
from models import FetchResult, Listing
from sources.base import QuotaExceeded, Source, SourceError

log = logging.getLogger(__name__)

API_ROOT = "https://api.sandbox.ebay.com" if config.EBAY_SANDBOX else "https://api.ebay.com"
TOKEN_URL = f"{API_ROOT}/identity/v1/oauth2/token"
SEARCH_URL = f"{API_ROOT}/buy/browse/v1/item_summary/search"
RATE_LIMIT_URL = f"{API_ROOT}/developer/analytics/v1_beta/rate_limit/"
SCOPE = "https://api.ebay.com/oauth/api_scope"

_IMAGE_SIZE = re.compile(r"/s-l\d+\.")
_UNSAFE_QUERY_CHARS = re.compile(r"[(),*]")


class EbayAuth:
    def __init__(self, client: httpx.AsyncClient, client_id: str, client_secret: str):
        self.client = client
        self._client_id = client_id
        raw = f"{client_id}:{client_secret}".encode()
        self._basic = base64.b64encode(raw).decode()
        self._token: Optional[str] = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self):
        self._token = None
        self._expires_at = 0.0

    async def token(self) -> str:
        if self._token and time.time() < self._expires_at - 300:
            return self._token
        async with self._lock:
            if self._token and time.time() < self._expires_at - 300:
                return self._token
            resp = await self.client.post(
                TOKEN_URL,
                headers={
                    "Authorization": f"Basic {self._basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials", "scope": SCOPE},
            )
            if resp.status_code != 200:
                hint = ""
                is_sbx = "SBX" in self._client_id
                if is_sbx != config.EBAY_SANDBOX:
                    wanted = "sandbox" if is_sbx else "production"
                    hint = f" Le chiavi sembrano {wanted.upper()}: metti EBAY_ENV={wanted} nel file .env."
                raise SourceError(f"token eBay rifiutato ({resp.status_code}): {resp.text[:200]}.{hint}")
            payload = resp.json()
            self._token = payload["access_token"]
            self._expires_at = time.time() + int(payload.get("expires_in", 7200))
            log.info("Nuovo token eBay, scade tra %s s", payload.get("expires_in"))
            return self._token


def _clean_term(term: str) -> str:
    return " ".join(_UNSAFE_QUERY_CHARS.sub(" ", term).split())


def build_queries(keywords: list) -> list:
    terms = [_clean_term(k.term) for k in keywords]
    terms = [t for t in terms if t]
    if config.EBAY_QUERY_MODE != "combined":
        return terms
    queries, group = [], []

    def render(items):
        return items[0] if len(items) == 1 else "(" + ", ".join(items) + ")"

    for term in terms:
        candidate = group + [term]
        too_long = len(render(candidate)) > config.EBAY_MAX_QUERY_CHARS
        too_many = len(candidate) > config.EBAY_MAX_KEYWORDS_PER_QUERY
        if group and (too_long or too_many):
            queries.append(render(group))
            group = [term]
        else:
            group = candidate
    if group:
        queries.append(render(group))
    return queries


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _amount(obj: Optional[dict]):
    if not obj or "value" not in obj:
        return None, ""
    try:
        return float(obj["value"]), obj.get("currency", "")
    except (TypeError, ValueError):
        return None, obj.get("currency", "")


def _image(url: str) -> str:
    if config.EBAY_UPSCALE_IMAGES:
        return _IMAGE_SIZE.sub("/s-l1600.", url, count=1)
    return url


def _legacy_id(item: dict) -> Optional[str]:
    if item.get("legacyItemId"):
        return str(item["legacyItemId"])
    parts = str(item.get("itemId", "")).split("|")
    return parts[1] if len(parts) >= 2 and parts[1] else None


class EbaySource(Source):
    quota_group = "ebay"

    def __init__(self, market_id: str, auth: EbayAuth, client: httpx.AsyncClient):
        self.id = market_id
        self.name = config.EBAY_MARKETS[market_id]["name"]
        self.country = config.EBAY_MARKETS[market_id]["country"]
        self.auth = auth
        self.client = client
        self._last_fetch: dict = {}
        self._page_size: dict = {}

    def requests_per_cycle(self, keywords: list) -> int:
        return len(build_queries(keywords))

    async def fetch(self, keywords: list) -> FetchResult:
        queries = build_queries(keywords)
        results = await asyncio.gather(*(self._fetch_query(q) for q in queries), return_exceptions=True)
        listings, requests, errors = [], 0, []
        for res in results:
            if isinstance(res, QuotaExceeded):
                raise res
            if isinstance(res, Exception):
                errors.append(res)
                continue
            items, n = res
            listings.extend(items)
            requests += n
        if errors and not listings:
            raise errors[0]
        for err in errors:
            log.warning("%s: query parziale fallita: %s", self.name, err)
        return FetchResult(listings=listings, requests=requests)

    async def _fetch_query(self, query: str):
        since = self._last_fetch.get(query)
        started = datetime.now(timezone.utc)
        size = self._page_size.get(query, config.EBAY_PAGE_SIZE)
        listings, requests = [], 0
        for page in range(config.EBAY_MAX_PAGES):
            items = await self._search(query, offset=page * size, limit=size)
            requests += 1
            parsed = [x for x in (self._to_listing(i) for i in items) if x]
            listings.extend(parsed)
            if since is None or len(items) < size:
                break
            oldest = min((x.created_at for x in parsed if x.created_at), default=None)
            if oldest is None or oldest <= since:
                break
            log.info("%s: pagina %d piena di annunci nuovi per %r, leggo la successiva", self.name, page + 1, query)
        if since is not None:
            fresh = sum(1 for x in listings if x.created_at and x.created_at > since)
            busy = fresh >= config.EBAY_PAGE_SIZE * 0.8
            new_size = config.EBAY_PAGE_SIZE_LARGE if busy else config.EBAY_PAGE_SIZE
            if new_size != size:
                log.info("%s: %d annunci nuovi per %r, prossima ricerca da %d risultati", self.name, fresh, query, new_size)
            self._page_size[query] = new_size
        self._last_fetch[query] = started
        return listings, requests

    async def _search(self, query: str, offset: int, limit: int = 0, retry_auth: bool = True) -> list:
        limit = limit or config.EBAY_PAGE_SIZE
        params = {"q": query, "sort": "newlyListed", "limit": str(limit)}
        if offset:
            params["offset"] = str(offset)
        if config.EBAY_ONLY_LOCAL_ITEMS:
            params["filter"] = f"itemLocationCountry:{self.country}"
        token = await self.auth.token()
        resp = await self.client.get(
            SEARCH_URL,
            params=params,
            headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": self.id},
        )
        if resp.status_code == 401 and retry_auth:
            self.auth.invalidate()
            return await self._search(query, offset, limit, retry_auth=False)
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", 0) or 0)
            raise QuotaExceeded(f"{self.name}: limite chiamate eBay raggiunto", retry_after)
        if resp.status_code >= 400:
            raise SourceError(f"{self.name}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json().get("itemSummaries", [])

    def _to_listing(self, item: dict) -> Optional[Listing]:
        key = _legacy_id(item)
        title = item.get("title")
        url = item.get("itemWebUrl")
        if not key or not title or not url:
            return None
        options = item.get("buyingOptions") or []
        is_auction = "AUCTION" in options and "FIXED_PRICE" not in options
        price, currency = _amount(item.get("currentBidPrice") if is_auction else item.get("price"))
        if price is None:
            price, currency = _amount(item.get("price") or item.get("currentBidPrice"))
        images = []
        if item.get("image", {}).get("imageUrl"):
            images.append(_image(item["image"]["imageUrl"]))
        for extra in item.get("additionalImages") or []:
            if extra.get("imageUrl") and len(images) < config.MAX_PHOTOS:
                images.append(_image(extra["imageUrl"]))
        origin = item.get("listingMarketplaceId") or self.id
        return Listing(
            source=self.id,
            source_name=self.name,
            item_key=f"ebay:{key}",
            title=title,
            url=url.split("?")[0],
            price=price,
            currency=currency,
            is_auction=is_auction,
            bid_count=int(item.get("bidCount") or 0),
            images=images,
            seller=(item.get("seller") or {}).get("username", ""),
            origin=origin,
            origin_name=config.EBAY_MARKETS.get(origin, {}).get("name", origin),
            location_country=(item.get("itemLocation") or {}).get("country", ""),
            created_at=_parse_date(item.get("itemCreationDate")),
        )


async def get_rate_limits(auth: EbayAuth) -> Optional[dict]:
    try:
        token = await auth.token()
        resp = await auth.client.get(
            RATE_LIMIT_URL,
            params={"api_context": "buy", "api_name": "Browse"},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            return None
        for api in resp.json().get("rateLimits", []):
            for resource in api.get("resources", []):
                if "search" not in resource.get("name", "") and "browse" not in resource.get("name", ""):
                    continue
                for rate in resource.get("rates", []):
                    return {
                        "limit": rate.get("limit"),
                        "remaining": rate.get("remaining"),
                        "count": rate.get("count"),
                        "reset": _parse_date(rate.get("reset")),
                    }
    except (httpx.HTTPError, SourceError, ValueError) as exc:
        log.warning("Lettura quota eBay fallita: %s", exc)
    return None
