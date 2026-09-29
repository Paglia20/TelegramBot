import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config
from matching import normalize
from models import Keyword, Listing

LEGACY_MARKETS = ("EBAY_IT", "EBAY_DE", "EBAY_CH", "EBAY_FR", "EBAY_BE", "EBAY_NL", "EBAY_US")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    norm TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS excludes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    norm TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    item_key TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    source TEXT NOT NULL,
    origin TEXT,
    markets TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL,
    currency TEXT,
    url TEXT NOT NULL,
    keyword TEXT NOT NULL,
    dup_of TEXT,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_fp ON seen(fingerprint, first_seen);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    added_at REAL NOT NULL,
    invited_by INTEGER
);
CREATE TABLE IF NOT EXISTS invites (
    token_hash TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    created_by INTEGER
);
CREATE TABLE IF NOT EXISTS api_usage (
    day TEXT PRIMARY KEY,
    calls INTEGER NOT NULL
);
"""


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(seen)")}
        if "image" not in columns:
            self.db.execute("ALTER TABLE seen ADD COLUMN image TEXT")

    def close(self):
        self.db.close()

    def _get(self, key: str, default):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def _set(self, key: str, value):
        self.db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    @property
    def interval(self) -> int:
        return int(self._get("interval", config.DEFAULT_INTERVAL_SECONDS))

    @interval.setter
    def interval(self, seconds: int):
        self._set("interval", int(seconds))

    @property
    def paused(self) -> bool:
        return bool(self._get("paused", False))

    @paused.setter
    def paused(self, value: bool):
        self._set("paused", bool(value))

    def _disabled_markets(self) -> list:
        disabled = self._get("disabled_markets", None)
        if disabled is None:
            legacy = self._get("enabled_markets", None)
            disabled = [m for m in LEGACY_MARKETS if legacy is not None and m not in legacy]
            self._set("disabled_markets", disabled)
            self.db.execute("DELETE FROM settings WHERE key='enabled_markets'")
        return disabled

    @property
    def enabled_markets(self) -> list:
        disabled = set(self._disabled_markets())
        return [m for m in config.EBAY_MARKETS if m not in disabled]

    def toggle_market(self, market_id: str) -> bool:
        disabled = set(self._disabled_markets())
        if market_id in disabled:
            disabled.discard(market_id)
            enabled = True
        else:
            disabled.add(market_id)
            enabled = False
        self._set("disabled_markets", sorted(disabled))
        return enabled

    def _add_term(self, table: str, term: str) -> bool:
        term = " ".join(term.split())
        norm = normalize(term)
        if not norm:
            return False
        cur = self.db.execute(
            f"INSERT OR IGNORE INTO {table}(term, norm, created_at) VALUES(?, ?, ?)",
            (term, norm, time.time()),
        )
        return cur.rowcount > 0

    def _remove_term(self, table: str, term_or_id) -> Optional[str]:
        if isinstance(term_or_id, int):
            row = self.db.execute(f"SELECT term FROM {table} WHERE id=?", (term_or_id,)).fetchone()
            self.db.execute(f"DELETE FROM {table} WHERE id=?", (term_or_id,))
        else:
            norm = normalize(term_or_id)
            row = self.db.execute(f"SELECT term FROM {table} WHERE norm=?", (norm,)).fetchone()
            self.db.execute(f"DELETE FROM {table} WHERE norm=?", (norm,))
        return row["term"] if row else None

    def add_keyword(self, term: str) -> bool:
        return self._add_term("keywords", term)

    def remove_keyword(self, term_or_id) -> Optional[str]:
        return self._remove_term("keywords", term_or_id)

    def keywords(self) -> list:
        rows = self.db.execute("SELECT id, term, norm, created_at FROM keywords ORDER BY id").fetchall()
        return [Keyword(r["id"], r["term"], r["norm"], r["created_at"]) for r in rows]

    def add_exclude(self, term: str) -> bool:
        return self._add_term("excludes", term)

    def remove_exclude(self, term_or_id) -> Optional[str]:
        return self._remove_term("excludes", term_or_id)

    def excludes(self) -> list:
        return [(r["id"], r["term"]) for r in self.db.execute("SELECT id, term FROM excludes ORDER BY id")]

    def get_seen(self, item_key: str):
        return self.db.execute("SELECT * FROM seen WHERE item_key=?", (item_key,)).fetchone()

    def find_fingerprint(self, fp: str, since: float) -> Optional[str]:
        row = self.db.execute(
            "SELECT item_key FROM seen WHERE fingerprint=? AND first_seen>=? LIMIT 1", (fp, since)
        ).fetchone()
        return row["item_key"] if row else None

    def insert_seen(self, listing: Listing, fp: str, keyword: str, dup_of: Optional[str] = None):
        now = time.time()
        self.db.execute(
            "INSERT OR IGNORE INTO seen(item_key, fingerprint, source, origin, markets, title, price, "
            "currency, url, keyword, dup_of, first_seen, last_seen, image) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                listing.item_key, fp, listing.source, listing.origin, listing.source, listing.title,
                listing.price, listing.currency, listing.url, keyword, dup_of, now, now,
                listing.images[0] if listing.images else None,
            ),
        )

    def touch_seen(self, row, listing: Listing, new_price: Optional[float] = None):
        markets = set(row["markets"].split(","))
        markets.add(listing.source)
        price = new_price if new_price is not None else row["price"]
        self.db.execute(
            "UPDATE seen SET markets=?, last_seen=?, price=? WHERE item_key=?",
            (",".join(sorted(markets)), time.time(), price, row["item_key"]),
        )

    def prune_seen(self, days: int) -> int:
        cutoff = time.time() - days * 86400
        return self.db.execute("DELETE FROM seen WHERE last_seen<?", (cutoff,)).rowcount

    def seen_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    def recent_notified(self, limit: int = 20) -> list:
        rows = self.db.execute(
            "SELECT title, price, currency, url, source, origin, keyword, first_seen, image FROM seen "
            "WHERE dup_of IS NULL ORDER BY first_seen DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def notification_stats(self, since: float) -> dict:
        row = self.db.execute(
            "SELECT "
            "SUM(CASE WHEN dup_of IS NULL THEN 1 ELSE 0 END) AS notified_total, "
            "SUM(CASE WHEN dup_of IS NULL AND first_seen >= ? THEN 1 ELSE 0 END) AS notified_today, "
            "SUM(CASE WHEN dup_of IS NOT NULL AND first_seen >= ? THEN 1 ELSE 0 END) AS duplicates_today "
            "FROM seen",
            (since, since),
        ).fetchone()
        return {k: int(row[k] or 0) for k in ("notified_total", "notified_today", "duplicates_today")}

    def keyword_counts(self) -> dict:
        rows = self.db.execute(
            "SELECT keyword, COUNT(*) AS n FROM seen WHERE dup_of IS NULL GROUP BY keyword"
        ).fetchall()
        return {r["keyword"]: r["n"] for r in rows}

    def create_session(self, token_hash: str, expires_at: float):
        self.db.execute("DELETE FROM sessions WHERE expires_at<?", (time.time(),))
        self.db.execute("INSERT OR REPLACE INTO sessions(token_hash, created_at, expires_at) VALUES(?,?,?)",
                        (token_hash, time.time(), expires_at))

    def session_valid(self, token_hash: str) -> bool:
        row = self.db.execute("SELECT expires_at FROM sessions WHERE token_hash=?", (token_hash,)).fetchone()
        return bool(row) and row["expires_at"] > time.time()

    def clear_sessions(self) -> int:
        return self.db.execute("DELETE FROM sessions").rowcount

    def add_user(self, chat_id: int, name: str, invited_by: Optional[int] = None) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO users(chat_id, name, added_at, invited_by) VALUES(?,?,?,?)",
            (chat_id, name, time.time(), invited_by),
        )
        return cur.rowcount > 0

    def remove_user(self, chat_id: int) -> Optional[str]:
        row = self.db.execute("SELECT name FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        self.db.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
        return row["name"] if row else None

    def users(self) -> list:
        return [dict(r) for r in self.db.execute("SELECT chat_id, name, added_at FROM users ORDER BY added_at")]

    def is_user(self, chat_id: int) -> bool:
        return self.db.execute("SELECT 1 FROM users WHERE chat_id=?", (chat_id,)).fetchone() is not None

    def create_invite(self, token_hash: str, expires_at: float, created_by: int):
        self.db.execute("DELETE FROM invites WHERE expires_at<?", (time.time(),))
        self.db.execute("INSERT INTO invites(token_hash, created_at, expires_at, created_by) VALUES(?,?,?,?)",
                        (token_hash, time.time(), expires_at, created_by))

    def use_invite(self, token_hash: str) -> Optional[int]:
        row = self.db.execute("SELECT created_by, expires_at FROM invites WHERE token_hash=?", (token_hash,)).fetchone()
        if row is None:
            return None
        self.db.execute("DELETE FROM invites WHERE token_hash=?", (token_hash,))
        return row["created_by"] if row["expires_at"] > time.time() else None

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def add_api_calls(self, n: int):
        if n <= 0:
            return
        self.db.execute(
            "INSERT INTO api_usage(day, calls) VALUES(?, ?) "
            "ON CONFLICT(day) DO UPDATE SET calls=calls+excluded.calls",
            (self._today(), n),
        )

    def api_calls_today(self) -> int:
        row = self.db.execute("SELECT calls FROM api_usage WHERE day=?", (self._today(),)).fetchone()
        return row["calls"] if row else 0
