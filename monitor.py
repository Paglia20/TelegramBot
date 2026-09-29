import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import config
from matching import Matcher, fingerprint
from sources import QuotaExceeded, Source

log = logging.getLogger(__name__)

QUOTA_LIMITS = {"ebay": config.EBAY_DAILY_CALL_LIMIT}
MIN_GAP_BETWEEN_CYCLES = 10
TOUCH_EVERY_SECONDS = 3600
PRUNE_EVERY_SECONDS = 6 * 3600


@dataclass
class SourceHealth:
    failures: int = 0
    next_try: float = 0.0
    alerted: bool = False
    last_error: str = ""
    last_ok: float = 0.0
    last_requests: int = 0


@dataclass
class CycleStats:
    started_at: float = 0.0
    duration: float = 0.0
    fetched: int = 0
    matched: int = 0
    notified: int = 0
    duplicates: int = 0
    requests: int = 0
    skipped_reason: str = ""


@dataclass
class Budget:
    calls_per_cycle: int
    min_interval: int
    requested: int
    effective: int
    daily_calls: int = field(default=0)


class Monitor:
    def __init__(self, store, notifier, sources: dict, missing_credentials: bool = False):
        self.store = store
        self.notifier = notifier
        self.sources: dict = sources
        self.missing_credentials = missing_credentials
        self.health = {sid: SourceHealth() for sid in sources}
        self.quota_pause_until: dict = {}
        self.last = CycleStats()
        self._wake = asyncio.Event()
        self._last_prune = 0.0
        self._last_start = 0.0
        self.started_at = time.time()
        self.next_cycle_at = 0.0
        self.in_cycle = False
        self.backlog: list = []

    def trigger(self):
        self._wake.set()

    def active_sources(self) -> list:
        return [self.sources[m] for m in self.store.enabled_markets if m in self.sources]

    def budget(self) -> Budget:
        keywords = self.store.keywords()
        requested = self.store.interval
        per_group: dict = {}
        total = 0
        for src in self.active_sources():
            n = src.requests_per_cycle(keywords) if keywords else 0
            total += n
            if src.quota_group:
                per_group[src.quota_group] = per_group.get(src.quota_group, 0) + n
        min_interval = 0
        for group, calls in per_group.items():
            limit = QUOTA_LIMITS.get(group)
            if limit and calls:
                min_interval = max(min_interval, math.ceil(calls * 86400 / (limit * config.EBAY_BUDGET_SAFETY)))
        effective = max(requested, min_interval, config.MIN_INTERVAL_SECONDS)
        return Budget(total, min_interval, requested, effective, total * 86400 // max(effective, 1))

    async def run_forever(self):
        log.info("Monitor avviato")
        while True:
            gap = self._last_start + MIN_GAP_BETWEEN_CYCLES - time.monotonic()
            if gap > 0:
                await asyncio.sleep(gap)
            self._last_start = time.monotonic()
            self.next_cycle_at = 0.0
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Errore inatteso nel ciclo")
            elapsed = time.monotonic() - self._last_start
            wait = max(1.0, self.budget().effective + random.uniform(0, config.CYCLE_JITTER_SECONDS) - elapsed)
            self.next_cycle_at = time.time() + wait
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def run_cycle(self) -> CycleStats:
        stats = CycleStats(started_at=time.time())
        self.last = stats
        t0 = time.monotonic()
        self.in_cycle = True
        try:
            await self._cycle(stats)
        finally:
            self.in_cycle = False
            stats.duration = time.monotonic() - t0
        return stats

    async def _cycle(self, stats: CycleStats):
        if self.store.paused:
            stats.skipped_reason = "in pausa"
            return
        if self.missing_credentials:
            stats.skipped_reason = "credenziali eBay mancanti nel file .env"
            return
        keywords = self.store.keywords()
        if not keywords:
            stats.skipped_reason = "nessuna parola da monitorare"
            return
        now = time.time()
        ready = []
        for src in self.active_sources():
            if self.quota_pause_until.get(src.quota_group, 0) > now:
                continue
            if self.health[src.id].next_try > now:
                continue
            ready.append(src)
        if not ready:
            stats.skipped_reason = "nessun mercato disponibile in questo momento"
            return

        results = await asyncio.gather(*(self._fetch(src, keywords) for src in ready))
        listings = [item for batch in results for item in batch]
        stats.fetched = len(listings)
        self.store.add_api_calls(stats.requests)

        await self._process(listings, keywords, stats)

        if now - self._last_prune > PRUNE_EVERY_SECONDS:
            removed = self.store.prune_seen(config.SEEN_RETENTION_DAYS)
            self._last_prune = now
            if removed:
                log.info("Storico: rimossi %d annunci vecchi", removed)
        log.info(
            "Ciclo: %d mercati, %d annunci letti, %d corrispondenze, %d notificati, %d doppioni, %d chiamate",
            len(ready), stats.fetched, stats.matched, stats.notified, stats.duplicates, stats.requests,
        )

    async def _fetch(self, src: Source, keywords: list) -> list:
        health = self.health[src.id]
        timeout = config.HTTP_TIMEOUT_SECONDS * (config.EBAY_MAX_PAGES + 2)
        try:
            result = await asyncio.wait_for(src.fetch(keywords), timeout=timeout)
        except QuotaExceeded as exc:
            self.last.requests += src.requests_per_cycle(keywords)
            await self._on_quota(src, exc)
            return []
        except Exception as exc:
            self.last.requests += src.requests_per_cycle(keywords)
            await self._on_failure(src, exc)
            return []
        self.last.requests += result.requests
        if health.alerted:
            await self.notifier.alert(f"{src.name} di nuovo raggiungibile.")
        self.health[src.id] = SourceHealth(last_ok=time.time(), last_requests=result.requests)
        return result.listings

    async def _on_failure(self, src: Source, exc: Exception):
        health = self.health[src.id]
        health.failures += 1
        health.last_error = f"{type(exc).__name__}: {exc}"[:300]
        backoff = min(self.budget().effective * 2 ** (health.failures - 1), config.MAX_BACKOFF_SECONDS)
        health.next_try = time.time() + backoff
        log.warning("%s fallito (%d di fila), riprovo tra %d s: %s", src.name, health.failures, backoff, health.last_error)
        if health.failures >= config.FAILURES_BEFORE_ALERT and not health.alerted:
            health.alerted = True
            await self.notifier.alert(f"{src.name} non risponde da {health.failures} tentativi.\n{health.last_error}")

    async def _on_quota(self, src: Source, exc: QuotaExceeded):
        pause = exc.retry_after if exc.retry_after > 0 else 3600
        group = src.quota_group or src.id
        already = self.quota_pause_until.get(group, 0) > time.time()
        self.quota_pause_until[group] = time.time() + pause
        log.warning("Quota %s esaurita, pausa di %d s", group, pause)
        if not already:
            await self.notifier.alert(
                f"Limite giornaliero di chiamate {group} raggiunto. Riprendo tra circa {int(pause // 60)} minuti."
            )

    async def _process(self, listings: list, keywords: list, stats: CycleStats):
        matcher = Matcher(keywords, [term for _, term in self.store.excludes()])
        best: dict = {}
        for item in listings:
            current = best.get(item.item_key)
            if current is None or (item.source == item.origin and current.source != current.origin):
                best[item.item_key] = item
        ordered = sorted(best.values(), key=lambda x: x.created_at.timestamp() if x.created_at else float("inf"))

        now = time.time()
        lookback = config.NEW_KEYWORD_LOOKBACK_MINUTES * 60
        window_start = now - config.CROSS_MARKET_WINDOW_DAYS * 86400
        backlog = []
        for kind, item, _, extra in self.backlog:
            kw = matcher.match(item.title)
            if kw is None or (kind == "new" and self.store.get_seen(item.item_key) is not None):
                continue
            backlog.append((kind, item, kw, extra))
        queued = {entry[1].item_key for entry in backlog}
        cycle_fps: set = {entry[3] for entry in backlog if entry[0] == "new"}
        outbox = []

        for item in ordered:
            kw = matcher.match(item.title)
            if kw is None:
                continue
            stats.matched += 1
            if item.item_key in queued:
                continue
            row = self.store.get_seen(item.item_key)
            if row is not None:
                if self._is_price_drop(row, item):
                    outbox.append(("drop", item, kw, row))
                elif now - row["last_seen"] > TOUCH_EVERY_SECONDS:
                    self.store.touch_seen(row, item)
                continue
            if (not config.NOTIFY_OLD_LISTINGS and item.created_at
                    and item.created_at.timestamp() < kw.created_at - lookback):
                continue
            fp = fingerprint(item.seller, item.title)
            if config.CROSS_MARKET_DEDUP and item.seller:
                if fp in cycle_fps:
                    stats.duplicates += 1
                    continue
                original = self.store.find_fingerprint(fp, window_start)
                if original:
                    self.store.insert_seen(item, fp, kw.term, dup_of=original)
                    stats.duplicates += 1
                    log.info("Doppione di %s ignorato: %s (%s)", original, item.title, item.source_name)
                    continue
            cycle_fps.add(fp)
            outbox.append(("new", item, kw, fp))

        outbox = backlog + outbox
        limit = config.MAX_NOTIFICATIONS_PER_CYCLE
        if limit:
            self.backlog = outbox[limit:][: config.MAX_BACKLOG]
            outbox = outbox[:limit]
        else:
            self.backlog = []
        if self.backlog:
            log.warning("%d annunci in attesa: ne invio %d ora, gli altri %d al prossimo ciclo. "
                        "Se succede spesso, una parola è troppo generica.",
                        len(outbox) + len(self.backlog), len(outbox), len(self.backlog))

        for kind, item, kw, extra in outbox:
            if kind == "new":
                if await self.notifier.send_listing(item, kw.term):
                    self.store.insert_seen(item, extra, kw.term)
                    stats.notified += 1
            else:
                if await self.notifier.send_listing(item, kw.term, old_price=extra["price"]):
                    self.store.touch_seen(extra, item, new_price=item.price)
                    stats.notified += 1

    @staticmethod
    def _is_price_drop(row, item) -> bool:
        if config.ON_LISTING_CHANGE != "price_drop":
            return False
        if item.is_auction or item.price is None or row["price"] is None or row["dup_of"]:
            return False
        if item.source != row["source"] or item.currency != row["currency"]:
            return False
        return item.price < row["price"] - 0.009
