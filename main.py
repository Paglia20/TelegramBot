import asyncio
import logging
import sys

import httpx
from telegram import Update
from telegram.ext import AIORateLimiter, Application, ApplicationBuilder

import config
import telegram_ui
from dashboard import Dashboard, EventLog
from monitor import Monitor
from notifier import Notifier
from sources.ebay import EbayAuth, EbaySource
from storage import Storage

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("main")
EVENTS = EventLog()
logging.getLogger().addHandler(EVENTS)


async def on_startup(app: Application):
    store: Storage = app.bot_data["store"]
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(config.HTTP_TIMEOUT_SECONDS),
        limits=httpx.Limits(max_connections=config.MAX_PARALLEL_REQUESTS, max_keepalive_connections=config.MAX_PARALLEL_REQUESTS),
        headers={"User-Agent": "annunci-monitor/2.0"},
    )
    app.bot_data["http"] = client

    missing = not (config.EBAY_CLIENT_ID and config.EBAY_CLIENT_SECRET)
    sources = {}
    if not missing:
        auth = EbayAuth(client, config.EBAY_CLIENT_ID, config.EBAY_CLIENT_SECRET)
        app.bot_data["ebay_auth"] = auth
        sources = {mid: EbaySource(mid, auth, client) for mid in config.EBAY_MARKETS}
    else:
        log.warning("EBAY_CLIENT_ID o EBAY_CLIENT_SECRET mancanti: il bot parte, ma non cerca annunci")

    notifier = Notifier(app.bot, lambda: telegram_ui.recipients(store))
    monitor = Monitor(store, notifier, sources, missing_credentials=missing)
    app.bot_data["monitor"] = monitor
    await app.bot.set_my_commands(telegram_ui.COMMANDS)
    dashboard_line = ""
    if config.DASHBOARD_ENABLED:
        dashboard = Dashboard(store, monitor, EVENTS, app.bot_data.get("ebay_auth"))
        try:
            await dashboard.start()
            app.bot_data["dashboard"] = dashboard
            dashboard_line = ("\nDashboard: scrivi /dashboard per il link" if dashboard.is_public
                              else f"\nDashboard: {dashboard.url}")
        except OSError as exc:
            log.error("Dashboard non avviata (porta %s occupata?): %s", config.DASHBOARD_PORT, exc)
    if config.TELEGRAM_CHAT_ID:
        app.bot_data["monitor_task"] = asyncio.create_task(monitor.run_forever())
        mode = " in modalità SANDBOX (annunci finti di prova)" if config.EBAY_SANDBOX else ""
        await notifier.alert(f"monitor avviato{mode}. Scrivi /menu per il pannello di controllo.{dashboard_line}")


async def on_shutdown(app: Application):
    dashboard = app.bot_data.get("dashboard")
    if dashboard:
        await dashboard.stop()
    task = app.bot_data.get("monitor_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    client = app.bot_data.get("http")
    if client:
        await client.aclose()
    app.bot_data["store"].close()


def main():
    if not config.TELEGRAM_TOKEN:
        sys.exit("Manca TELEGRAM_TOKEN nel file .env")
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_TOKEN)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )
    app.bot_data["store"] = Storage(config.DB_PATH)
    telegram_ui.register(app)
    log.info("Bot avviato. eBay: %s. Database: %s", config.EBAY_ENV, config.DB_PATH)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
