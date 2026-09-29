import html
from datetime import datetime

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import config
from matching import split_terms
from sources.ebay import get_rate_limits

BTN = InlineKeyboardButton
BACK = [BTN("Indietro", callback_data="m")]

COMMANDS = [
    BotCommand("menu", "Apri il pannello di controllo"),
    BotCommand("aggiungi", "Aggiungi parole da monitorare (separate da virgola)"),
    BotCommand("rimuovi", "Rimuovi una parola monitorata"),
    BotCommand("escludi", "Aggiungi parole da escludere"),
    BotCommand("includi", "Togli una parola dalle esclusioni"),
    BotCommand("timer", "Imposta la frequenza di controllo in secondi"),
    BotCommand("mercati", "Attiva o disattiva i mercati eBay"),
    BotCommand("stato", "Stato del monitor e consumo quota eBay"),
    BotCommand("dashboard", "Link alla dashboard"),
    BotCommand("revoca_dashboard", "Scollega tutti i browser dalla dashboard"),
    BotCommand("pausa", "Metti in pausa il monitoraggio"),
    BotCommand("riprendi", "Riprendi il monitoraggio"),
    BotCommand("annulla", "Annulla l'inserimento in corso"),
]


def _store(ctx):
    return ctx.application.bot_data["store"]


def _monitor(ctx):
    return ctx.application.bot_data["monitor"]


def _fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M:%S") if ts else "mai"


def _fmt_seconds(s: int) -> str:
    return f"{s} s" if s < 120 else f"{s // 60} min {s % 60} s" if s % 60 else f"{s // 60} min"


def view_main(ctx):
    store = _store(ctx)
    budget = _monitor(ctx).budget()
    state = "in pausa" if store.paused else "attivo"
    text = (
        f"<b>Monitor annunci eBay{' (SANDBOX, dati finti)' if config.EBAY_SANDBOX else ''}</b>\n\n"
        f"Stato: {state}\n"
        f"Parole monitorate: {len(store.keywords())}\n"
        f"Parole escluse: {len(store.excludes())}\n"
        f"Mercati attivi: {len(store.enabled_markets)} di {len(config.EBAY_MARKETS)}\n"
        f"Controllo ogni: {_fmt_seconds(budget.effective)}"
    )
    kb = [
        [BTN("Parole monitorate", callback_data="k"), BTN("Parole escluse", callback_data="e")],
        [BTN("Timer", callback_data="t"), BTN("Mercati", callback_data="mk")],
        [BTN("Stato", callback_data="s"), BTN("Riprendi" if store.paused else "Pausa", callback_data="p")],
        [BTN("Dashboard", callback_data="db")],
    ]
    return text, InlineKeyboardMarkup(kb)


def view_keywords(ctx):
    items = _store(ctx).keywords()
    lines = [f"- {html.escape(k.term)}" for k in items] or ["Nessuna parola. Aggiungine una."]
    text = "<b>Parole monitorate</b>\n\n" + "\n".join(lines) + "\n\nTocca una parola per rimuoverla."
    kb = [[BTN(f"Rimuovi: {k.term}"[:60], callback_data=f"kd:{k.id}")] for k in items]
    kb.append([BTN("Aggiungi parola", callback_data="ka")])
    kb.append(BACK)
    return text, InlineKeyboardMarkup(kb)


def view_excludes(ctx):
    items = _store(ctx).excludes()
    lines = [f"- {html.escape(t)}" for _, t in items] or ["Nessuna esclusione."]
    text = (
        "<b>Parole escluse</b>\n\n" + "\n".join(lines) +
        "\n\nUn annuncio che contiene una di queste parole nel titolo viene ignorato, per tutte le ricerche. "
        "Il confronto è per parola intera: 'cover' non blocca 'covers', aggiungi entrambe se servono."
    )
    kb = [[BTN(f"Rimuovi: {t}"[:60], callback_data=f"ed:{i}")] for i, t in items]
    kb.append([BTN("Aggiungi esclusione", callback_data="ea")])
    kb.append(BACK)
    return text, InlineKeyboardMarkup(kb)


def _budget_note(ctx) -> str:
    b = _monitor(ctx).budget()
    note = (
        f"Timer impostato: {_fmt_seconds(b.requested)}\n"
        f"Timer effettivo: {_fmt_seconds(b.effective)}\n"
        f"Chiamate eBay per ciclo: {b.calls_per_cycle}\n"
        f"Chiamate previste al giorno: {b.daily_calls} su {config.EBAY_DAILY_CALL_LIMIT}"
    )
    if b.effective > b.requested and b.min_interval >= b.requested:
        note += (
            f"\n\nCon {b.calls_per_cycle} chiamate per ciclo il limite giornaliero di eBay non basta per "
            f"{_fmt_seconds(b.requested)}. Uso {_fmt_seconds(b.effective)}. Per scendere puoi disattivare "
            "qualche mercato, ridurre le parole o chiedere a eBay più quota."
        )
    return note


def view_timer(ctx):
    text = "<b>Frequenza di controllo</b>\n\n" + _budget_note(ctx)
    presets = [BTN(_fmt_seconds(s), callback_data=f"ts:{s}") for s in config.TIMER_PRESETS]
    kb = [presets[i:i + 3] for i in range(0, len(presets), 3)]
    kb.append([BTN("Valore personalizzato", callback_data="tc")])
    kb.append(BACK)
    return text, InlineKeyboardMarkup(kb)


def view_markets(ctx):
    store = _store(ctx)
    enabled = set(store.enabled_markets)
    text = "<b>Mercati eBay</b>\n\nTocca un mercato per attivarlo o disattivarlo."
    kb = [
        [BTN(f"[{'x' if mid in enabled else ' '}] {info['name']}", callback_data=f"mt:{mid}")]
        for mid, info in config.EBAY_MARKETS.items()
    ]
    kb.append(BACK)
    return text, InlineKeyboardMarkup(kb)


async def view_status(ctx):
    store, monitor = _store(ctx), _monitor(ctx)
    last = monitor.last
    lines = ["<b>Stato</b>\n", f"Monitor: {'in pausa' if store.paused else 'attivo'}"]
    if monitor.missing_credentials:
        lines.append("Credenziali eBay mancanti: aggiungi EBAY_CLIENT_ID e EBAY_CLIENT_SECRET nel file .env")
    lines.append(f"Ultimo ciclo: {_fmt_time(last.started_at)} ({last.duration:.1f} s)")
    if last.skipped_reason:
        lines.append(f"Ciclo saltato: {html.escape(last.skipped_reason)}")
    else:
        lines.append(
            f"Annunci letti {last.fetched}, corrispondenze {last.matched}, "
            f"notificati {last.notified}, doppioni {last.duplicates}"
        )
    lines.append("")
    lines.append(_budget_note(ctx))
    lines.append(f"Chiamate eBay oggi (conteggio locale, UTC): {store.api_calls_today()}")
    auth = ctx.application.bot_data.get("ebay_auth")
    if auth:
        rate = await get_rate_limits(auth)
        if rate:
            reset = rate["reset"].astimezone().strftime("%d/%m %H:%M") if rate["reset"] else "?"
            lines.append(f"Quota eBay reale: {rate['remaining']} rimaste su {rate['limit']}, azzeramento {reset}")
    lines.append("\n<b>Mercati</b>")
    enabled = set(store.enabled_markets)
    for mid, info in config.EBAY_MARKETS.items():
        h = monitor.health.get(mid)
        if mid not in enabled:
            state = "disattivato"
        elif h is None or (h.failures == 0 and not h.last_ok):
            state = "in attesa del primo controllo"
        elif h.failures == 0:
            state = f"ok ({_fmt_time(h.last_ok)})"
        else:
            state = f"errore x{h.failures}: {html.escape(h.last_error[:120])}"
        lines.append(f"{info['name']}: {state}")
    lines.append(f"\nAnnunci nello storico: {store.seen_count()}")
    return "\n".join(lines), InlineKeyboardMarkup([[BTN("Aggiorna", callback_data="s")], BACK])


async def _show(update: Update, ctx, view, edit: bool):
    text, kb = await view(ctx) if view is view_status else view(ctx)
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                raise
    else:
        await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=kb)


def _add_keywords(ctx, text: str) -> str:
    store = _store(ctx)
    added, existing = [], []
    for term in split_terms(text):
        (added if store.add_keyword(term) else existing).append(term)
    if added:
        _monitor(ctx).trigger()
    msg = []
    if added:
        msg.append("Aggiunte: " + ", ".join(added))
    if existing:
        msg.append("Già presenti: " + ", ".join(existing))
    return "\n".join(msg) or "Nessuna parola valida."


def _add_excludes(ctx, text: str) -> str:
    store = _store(ctx)
    added = [t for t in split_terms(text) if store.add_exclude(t)]
    return ("Escluse: " + ", ".join(added)) if added else "Nessuna nuova esclusione."


def _set_timer(ctx, raw: str) -> str:
    try:
        seconds = int(float(raw.strip().lower().rstrip("s")))
    except ValueError:
        return "Scrivi un numero di secondi, per esempio 120."
    seconds = max(config.MIN_INTERVAL_SECONDS, min(config.MAX_INTERVAL_SECONDS, seconds))
    _store(ctx).interval = seconds
    _monitor(ctx).trigger()
    return f"Timer impostato a {_fmt_seconds(seconds)}."


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("awaiting", None)
    await _show(update, ctx, view_main, edit=False)


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(f"Il CHAT_ID di questa chat è: {update.effective_chat.id}")


async def cmd_aggiungi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        ctx.user_data["awaiting"] = "kw"
        await update.effective_chat.send_message("Scrivi le parole da monitorare, separate da virgola.")
        return
    await update.effective_chat.send_message(_add_keywords(ctx, " ".join(ctx.args)))


async def cmd_rimuovi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await _show(update, ctx, view_keywords, edit=False)
        return
    removed = _store(ctx).remove_keyword(" ".join(ctx.args))
    await update.effective_chat.send_message(f"Rimossa: {removed}" if removed else "Parola non trovata.")


async def cmd_escludi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        ctx.user_data["awaiting"] = "ex"
        await update.effective_chat.send_message("Scrivi le parole da escludere, separate da virgola.")
        return
    await update.effective_chat.send_message(_add_excludes(ctx, " ".join(ctx.args)))


async def cmd_includi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await _show(update, ctx, view_excludes, edit=False)
        return
    removed = _store(ctx).remove_exclude(" ".join(ctx.args))
    await update.effective_chat.send_message(f"Non più esclusa: {removed}" if removed else "Esclusione non trovata.")


async def cmd_timer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.args:
        await update.effective_chat.send_message(_set_timer(ctx, ctx.args[0]))
    await _show(update, ctx, view_timer, edit=False)


async def cmd_mercati(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _show(update, ctx, view_markets, edit=False)


async def cmd_stato(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _show(update, ctx, view_status, edit=False)


async def send_dashboard_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dashboard = ctx.application.bot_data.get("dashboard")
    chat = update.effective_chat
    if dashboard is None:
        await chat.send_message("La dashboard non è attiva.")
        return
    if not dashboard.is_public:
        await chat.send_message(
            f"La dashboard è aperta solo sul computer dove gira il bot: {dashboard.url}\n\n"
            "Per aprirla da qui, quando il bot sarà su un server imposta DASHBOARD_PUBLIC_URL nel file .env.",
            disable_web_page_preview=True,
        )
        return
    link = dashboard.create_login_link()
    await chat.send_message(
        f"Link personale per la dashboard. Vale {config.DASHBOARD_LINK_MINUTES} minuti e funziona una volta sola: "
        f"il browser con cui lo apri resta collegato per {config.DASHBOARD_SESSION_DAYS} giorni. Non inoltrarlo.",
        reply_markup=InlineKeyboardMarkup([[BTN("Apri dashboard", url=link)]]),
    )


async def cmd_dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await send_dashboard_link(update, ctx)


async def cmd_revoca(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dashboard = ctx.application.bot_data.get("dashboard")
    n = dashboard.revoke_sessions() if dashboard else 0
    await update.effective_chat.send_message(f"Accessi alla dashboard revocati ({n} dispositivi scollegati).")


async def cmd_pausa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _store(ctx).paused = True
    await update.effective_chat.send_message("Monitoraggio in pausa. Usa /riprendi per ripartire.")


async def cmd_riprendi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _store(ctx).paused = False
    _monitor(ctx).trigger()
    await update.effective_chat.send_message("Monitoraggio ripreso.")


async def cmd_annulla(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("awaiting", None)
    await update.effective_chat.send_message("Ok, annullato.")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    awaiting = ctx.user_data.pop("awaiting", None)
    text = update.effective_message.text or ""
    if awaiting == "kw":
        await update.effective_chat.send_message(_add_keywords(ctx, text))
        await _show(update, ctx, view_keywords, edit=False)
    elif awaiting == "ex":
        await update.effective_chat.send_message(_add_excludes(ctx, text))
        await _show(update, ctx, view_excludes, edit=False)
    elif awaiting == "timer":
        await update.effective_chat.send_message(_set_timer(ctx, text))
        await _show(update, ctx, view_timer, edit=False)
    else:
        await _show(update, ctx, view_main, edit=False)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_chat is None or update.effective_chat.id != config.TELEGRAM_CHAT_ID:
        await query.answer()
        return
    data = query.data or ""
    store = _store(ctx)
    await query.answer()
    ctx.user_data.pop("awaiting", None)

    if data == "m":
        await _show(update, ctx, view_main, edit=True)
    elif data == "k":
        await _show(update, ctx, view_keywords, edit=True)
    elif data == "e":
        await _show(update, ctx, view_excludes, edit=True)
    elif data == "t":
        await _show(update, ctx, view_timer, edit=True)
    elif data == "mk":
        await _show(update, ctx, view_markets, edit=True)
    elif data == "s":
        await _show(update, ctx, view_status, edit=True)
    elif data == "p":
        store.paused = not store.paused
        if not store.paused:
            _monitor(ctx).trigger()
        await _show(update, ctx, view_main, edit=True)
    elif data == "db":
        await send_dashboard_link(update, ctx)
    elif data == "ka":
        ctx.user_data["awaiting"] = "kw"
        await update.effective_chat.send_message("Scrivi le parole da monitorare, separate da virgola. /annulla per uscire.")
    elif data == "ea":
        ctx.user_data["awaiting"] = "ex"
        await update.effective_chat.send_message("Scrivi le parole da escludere, separate da virgola. /annulla per uscire.")
    elif data == "tc":
        ctx.user_data["awaiting"] = "timer"
        await update.effective_chat.send_message(
            f"Scrivi i secondi tra un controllo e l'altro ({config.MIN_INTERVAL_SECONDS}-{config.MAX_INTERVAL_SECONDS})."
        )
    elif data.startswith("kd:"):
        store.remove_keyword(int(data[3:]))
        await _show(update, ctx, view_keywords, edit=True)
    elif data.startswith("ed:"):
        store.remove_exclude(int(data[3:]))
        await _show(update, ctx, view_excludes, edit=True)
    elif data.startswith("ts:"):
        _set_timer(ctx, data[3:])
        await _show(update, ctx, view_timer, edit=True)
    elif data.startswith("mt:"):
        if data[3:] in config.EBAY_MARKETS:
            store.toggle_market(data[3:])
            _monitor(ctx).trigger()
        await _show(update, ctx, view_markets, edit=True)


async def cmd_not_configured(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        f"Il bot non è ancora collegato a questa chat.\n"
        f"Metti CHAT_ID={update.effective_chat.id} nel file .env e riavvia il programma."
    )


def register(app: Application):
    app.add_handler(CommandHandler("id", cmd_id))
    if not config.TELEGRAM_CHAT_ID:
        app.add_handler(MessageHandler(filters.ALL, cmd_not_configured))
        return
    me = filters.Chat(chat_id=config.TELEGRAM_CHAT_ID)
    handlers = {
        "start": cmd_menu, "menu": cmd_menu, "aggiungi": cmd_aggiungi, "rimuovi": cmd_rimuovi,
        "escludi": cmd_escludi, "includi": cmd_includi, "timer": cmd_timer, "mercati": cmd_mercati,
        "stato": cmd_stato, "dashboard": cmd_dashboard, "revoca_dashboard": cmd_revoca,
        "pausa": cmd_pausa, "riprendi": cmd_riprendi, "annulla": cmd_annulla,
    }
    for name, fn in handlers.items():
        app.add_handler(CommandHandler(name, fn, filters=me))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(me & filters.TEXT & ~filters.COMMAND, on_text))
