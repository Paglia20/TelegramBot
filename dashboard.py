import asyncio
import contextlib
import hashlib
import html
import json
import logging
import secrets
import socket
import time
from collections import deque
from datetime import datetime
from urllib.parse import urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

import config
from matching import split_terms
from sources.ebay import get_rate_limits

log = logging.getLogger(__name__)

HTML_PATH = config.BASE_DIR / "web" / "dashboard.html"
QUOTA_REFRESH_SECONDS = 300
MAX_BODY_BYTES = 16_384
EVENT_LOGGERS = ("monitor", "sources", "notifier", "main", "dashboard", "telegram_ui")
COOKIE_NAME = "monitor_session"
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost"}

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src https: data:; connect-src 'self'; base-uri 'none'; form-action 'none'"
    ),
}

MESSAGE_PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Monitor annunci</title>
<style>:root{color-scheme:light dark}body{margin:0;min-height:100vh;display:grid;place-items:center;
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;background:#f4f4f2;color:#0b0b0b}
@media(prefers-color-scheme:dark){body{background:#111110;color:#fff}.card{background:#1a1a19!important;border-color:#2f2f2c!important}p{color:#c3c2b7!important}}
.card{max-width:420px;margin:16px;padding:28px;border-radius:12px;background:#fcfcfb;border:1px solid #e4e3de}
h1{font-size:18px;margin:0 0 8px}p{margin:0;color:#52514e}</style></head>
<body><div class="card"><h1>{title}</h1><p>{text}</p></div></body></html>"""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _message_page(title: str, text: str, status: int) -> HTMLResponse:
    body = MESSAGE_PAGE.replace("{title}", html.escape(title)).replace("{text}", html.escape(text))
    return HTMLResponse(body, status_code=status)


class EventLog(logging.Handler):
    def __init__(self, maxlen: int = 300):
        super().__init__(logging.INFO)
        self.records = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord):
        if not record.name.startswith(EVENT_LOGGERS):
            return
        try:
            self.records.append({"ts": record.created, "level": record.levelname, "msg": record.getMessage()})
        except Exception:
            pass


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class _EmbeddedServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


class _Guard(BaseHTTPMiddleware):
    def __init__(self, app, dashboard: "Dashboard"):
        super().__init__(app)
        self.dashboard = dashboard

    async def dispatch(self, request: Request, call_next):
        response = await self._check(request)
        if response is None:
            response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    async def _check(self, request: Request):
        d = self.dashboard
        if request.url.path == "/login":
            return None
        if not (d.is_local(request) or d.session_ok(request)):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"error": "accesso richiesto"}, status_code=401)
            return _message_page("Accesso richiesto", "Apri la dashboard dal link che ti manda il bot "
                                 "Telegram con il comando /dashboard.", 401)
        if request.method in ("POST", "DELETE"):
            if request.headers.get("x-dashboard") != "1" or \
                    not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"error": "richiesta non autorizzata"}, status_code=403)
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
                return JSONResponse({"error": "richiesta troppo grande"}, status_code=413)
        return None


class Dashboard:
    def __init__(self, store, monitor, events: EventLog, ebay_auth=None):
        self.store = store
        self.monitor = monitor
        self.events = events
        self.auth = ebay_auth
        self.public_url = config.DASHBOARD_PUBLIC_URL
        self._login_tokens: dict = {}
        self._quota = None
        self._quota_at = 0.0
        self._quota_local_used = 0
        self._quota_lock = asyncio.Lock()
        self._server = None
        self._task = None
        self.app = self._build_app()

    @property
    def url(self) -> str:
        return self.public_url or f"http://127.0.0.1:{config.DASHBOARD_PORT}"

    @property
    def is_public(self) -> bool:
        return bool(self.public_url)

    def _allowed_hosts(self) -> list:
        hosts = set(LOCAL_HOSTNAMES) | {h.split(":")[0].lower() for h in config.DASHBOARD_EXTRA_HOSTS}
        if self.public_url:
            hosts.add((urlsplit(self.public_url).hostname or "").lower())
        return sorted(h for h in hosts if h)

    def _build_app(self) -> Starlette:
        routes = [
            Route("/", self._page, methods=["GET"]),
            Route("/login", self._login, methods=["GET"]),
            Route("/api/status", self._status, methods=["GET"]),
            Route("/api/pause", self._pause, methods=["POST"]),
            Route("/api/run", self._run, methods=["POST"]),
            Route("/api/interval", self._interval, methods=["POST"]),
            Route("/api/keywords", self._add_keywords, methods=["POST"]),
            Route("/api/keywords/{item_id:int}", self._remove_keyword, methods=["DELETE"]),
            Route("/api/excludes", self._add_excludes, methods=["POST"]),
            Route("/api/excludes/{item_id:int}", self._remove_exclude, methods=["DELETE"]),
            Route("/api/markets/{market_id}", self._market, methods=["POST"]),
        ]
        middleware = [
            Middleware(TrustedHostMiddleware, allowed_hosts=self._allowed_hosts(), www_redirect=False),
            Middleware(_Guard, dashboard=self),
        ]
        handlers = {ApiError: self._api_error}
        return Starlette(routes=routes, middleware=middleware, exception_handlers=handlers)

    async def start(self):
        family = socket.AF_INET6 if ":" in config.DASHBOARD_HOST else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((config.DASHBOARD_HOST, config.DASHBOARD_PORT))
        except OSError:
            sock.close()
            raise
        sock.set_inheritable(True)
        server_config = uvicorn.Config(
            self.app, log_config=None, log_level="warning", access_log=False, lifespan="off",
            proxy_headers=False, server_header=False, date_header=False,
        )
        self._server = _EmbeddedServer(server_config)
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))
        for _ in range(100):
            if self._server.started or self._task.done():
                break
            await asyncio.sleep(0.05)
        if not self._server.started:
            raise OSError("il server della dashboard non si è avviato")
        log.info("Dashboard su %s", self.url)

    async def stop(self):
        if self._server and self._task:
            self._server.should_exit = True
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._task, timeout=5)

    def is_local(self, request: Request) -> bool:
        client = request.client.host if request.client else ""
        return client in LOOPBACK and (request.url.hostname or "") in LOCAL_HOSTNAMES

    def session_ok(self, request: Request) -> bool:
        value = request.cookies.get(COOKIE_NAME)
        return bool(value) and self.store.session_valid(_hash(value))

    def create_login_link(self) -> str:
        now = time.time()
        self._login_tokens = {h: exp for h, exp in self._login_tokens.items() if exp > now}
        token = secrets.token_urlsafe(32)
        self._login_tokens[_hash(token)] = now + config.DASHBOARD_LINK_MINUTES * 60
        return f"{self.public_url}/login?t={token}"

    def revoke_sessions(self) -> int:
        self._login_tokens.clear()
        return self.store.clear_sessions()

    async def _api_error(self, request: Request, exc: ApiError) -> Response:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @staticmethod
    async def _json(request: Request) -> dict:
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                raise ApiError(413, "richiesta troppo grande")
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            raise ApiError(400, "JSON non valido")
        if not isinstance(data, dict):
            raise ApiError(400, "JSON non valido")
        return data

    async def _page(self, request: Request) -> Response:
        return HTMLResponse(HTML_PATH.read_text(encoding="utf-8"))

    async def _login(self, request: Request) -> Response:
        if not self.is_public:
            return _message_page("Pagina non disponibile", "L'accesso da link è attivo solo sul server.", 404)
        token = request.query_params.get("t", "")
        expires = self._login_tokens.pop(_hash(token), 0) if token else 0
        if expires < time.time():
            return _message_page("Link non valido", "Il link è scaduto o è già stato usato. "
                                 "Chiedine uno nuovo con /dashboard nel bot Telegram.", 403)
        session = secrets.token_urlsafe(32)
        max_age = config.DASHBOARD_SESSION_DAYS * 86400
        self.store.create_session(_hash(session), time.time() + max_age)
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(COOKIE_NAME, session, max_age=max_age, path="/", httponly=True,
                            samesite="lax", secure=self.public_url.startswith("https://"))
        log.info("Dashboard: nuovo accesso da link Telegram")
        return response

    async def _status(self, request: Request) -> Response:
        return JSONResponse(await self.status())

    async def _pause(self, request: Request) -> Response:
        data = await self._json(request)
        self.store.paused = bool(data.get("paused"))
        if not self.store.paused:
            self.monitor.trigger()
        log.info("Dashboard: monitoraggio %s", "in pausa" if self.store.paused else "ripreso")
        return JSONResponse({"ok": True})

    async def _run(self, request: Request) -> Response:
        await self._json(request)
        self.monitor.trigger()
        return JSONResponse({"ok": True})

    async def _interval(self, request: Request) -> Response:
        data = await self._json(request)
        try:
            seconds = int(data.get("seconds"))
        except (TypeError, ValueError):
            raise ApiError(400, "secondi non validi")
        self.store.interval = max(config.MIN_INTERVAL_SECONDS, min(config.MAX_INTERVAL_SECONDS, seconds))
        self.monitor.trigger()
        return JSONResponse({"ok": True, "interval": self.store.interval})

    async def _add_terms(self, request: Request, add, is_keyword: bool) -> Response:
        data = await self._json(request)
        terms = split_terms(str(data.get("terms", "")))[:50]
        added = [t for t in terms if add(t)]
        if added and is_keyword:
            self.monitor.trigger()
            log.info("Dashboard: aggiunte parole %s", ", ".join(added))
        return JSONResponse({"ok": True, "added": added, "existing": [t for t in terms if t not in added]})

    async def _add_keywords(self, request: Request) -> Response:
        return await self._add_terms(request, self.store.add_keyword, True)

    async def _add_excludes(self, request: Request) -> Response:
        return await self._add_terms(request, self.store.add_exclude, False)

    async def _remove(self, request: Request, remove) -> Response:
        removed = remove(request.path_params["item_id"])
        if removed is None:
            raise ApiError(404, "non trovata")
        return JSONResponse({"ok": True, "removed": removed})

    async def _remove_keyword(self, request: Request) -> Response:
        return await self._remove(request, self.store.remove_keyword)

    async def _remove_exclude(self, request: Request) -> Response:
        return await self._remove(request, self.store.remove_exclude)

    async def _market(self, request: Request) -> Response:
        data = await self._json(request)
        market = request.path_params["market_id"]
        if market not in config.EBAY_MARKETS:
            raise ApiError(404, "mercato sconosciuto")
        wanted = bool(data.get("enabled"))
        if (market in self.store.enabled_markets) != wanted:
            self.store.toggle_market(market)
            self.monitor.trigger()
        return JSONResponse({"ok": True})

    def _quota_stale(self) -> bool:
        now = time.time()
        if now - self._quota_at > QUOTA_REFRESH_SECONDS:
            return True
        reset = self._quota.get("reset") if self._quota else None
        return bool(reset) and reset.timestamp() <= now and self._quota_at < reset.timestamp()

    async def _quota_info(self):
        if not self.auth:
            return None
        async with self._quota_lock:
            if self._quota_stale():
                fresh = await get_rate_limits(self.auth)
                self._quota_at = time.time()
                if fresh is not None:
                    self._quota = fresh
                    self._quota_local_used = self.store.api_calls_today()
        return self._quota

    async def status(self) -> dict:
        store, monitor = self.store, self.monitor
        now = time.time()
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        budget = monitor.budget()
        last = monitor.last
        enabled = set(store.enabled_markets)

        quota = await self._quota_info()
        local_used = store.api_calls_today()
        quota_out = {
            "limit": config.EBAY_DAILY_CALL_LIMIT,
            "remaining": max(config.EBAY_DAILY_CALL_LIMIT - local_used, 0),
            "used": local_used,
            "reset": None,
            "source": "stima locale",
            "fetched_at": None,
            "local_used": local_used,
            "paused_until": monitor.quota_pause_until.get("ebay", 0) or None,
        }
        if quota and quota.get("limit") is not None:
            since_fetch = max(0, local_used - self._quota_local_used)
            quota_out.update(
                limit=quota["limit"],
                remaining=max(0, (quota.get("remaining") or 0) - since_fetch),
                used=(quota.get("count") or 0) + since_fetch,
                reset=quota["reset"].timestamp() if quota.get("reset") else None,
                source="eBay",
                fetched_at=self._quota_at,
                since_fetch=since_fetch,
            )

        markets = []
        for mid, info in config.EBAY_MARKETS.items():
            h = monitor.health.get(mid)
            if mid not in enabled:
                state = "disabled"
            elif monitor.missing_credentials:
                state = "unavailable"
            elif monitor.quota_pause_until.get("ebay", 0) > now:
                state = "quota"
            elif h is None or (h.failures == 0 and not h.last_ok):
                state = "waiting"
            elif h.failures:
                state = "error"
            else:
                state = "ok"
            markets.append({
                "id": mid, "name": info["name"], "country": info["country"], "enabled": mid in enabled,
                "state": state, "failures": h.failures if h else 0, "last_ok": (h.last_ok or None) if h else None,
                "last_error": h.last_error if h else "", "last_requests": h.last_requests if h else 0,
                "last_matches": h.last_matches if h else 0, "next_try": (h.next_try or None) if h and h.failures else None,
            })

        counts = store.keyword_counts()
        names = {mid: info["name"] for mid, info in config.EBAY_MARKETS.items()}
        recent = store.recent_notified(20)
        for r in recent:
            r["source_name"] = names.get(r["source"], r["source"])
            r["origin_name"] = names.get(r["origin"], r["origin"])

        return {
            "now": now,
            "env": config.EBAY_ENV,
            "public": self.is_public,
            "sandbox": config.EBAY_SANDBOX,
            "paused": store.paused,
            "missing_credentials": monitor.missing_credentials,
            "started_at": monitor.started_at,
            "in_cycle": monitor.in_cycle,
            "next_cycle_at": monitor.next_cycle_at or None,
            "interval": {
                "requested": budget.requested,
                "effective": budget.effective,
                "min_sustainable": budget.min_interval,
                "calls_per_cycle": budget.calls_per_cycle,
                "daily_forecast": budget.daily_calls,
                "presets": config.TIMER_PRESETS,
                "min": config.MIN_INTERVAL_SECONDS,
                "max": config.MAX_INTERVAL_SECONDS,
            },
            "quota": quota_out,
            "last_cycle": {
                "started_at": last.started_at or None,
                "duration": round(last.duration, 2),
                "fetched": last.fetched,
                "matched": last.matched,
                "notified": last.notified,
                "duplicates": last.duplicates,
                "requests": last.requests,
                "skipped_reason": last.skipped_reason,
            },
            "markets": markets,
            "keywords": [{"id": k.id, "term": k.term, "created_at": k.created_at, "notified": counts.get(k.term, 0)}
                         for k in store.keywords()],
            "excludes": [{"id": i, "term": t} for i, t in store.excludes()],
            "stats": {**store.notification_stats(today_start), "seen_total": store.seen_count()},
            "recent": recent,
            "events": list(self.events.records)[-80:][::-1],
        }
