import html
import logging
from typing import Callable, Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut

import config
from models import Listing

log = logging.getLogger(__name__)


def format_listing(item: Listing, keyword: str, old_price: Optional[float] = None) -> str:
    e = html.escape
    site = item.source_name
    if item.origin and item.origin != item.source:
        site += f" (pubblicato su {item.origin_name})"
    header = "Prezzo ribassato" if old_price is not None else "Nuovo annuncio trovato"
    price = e(item.price_text)
    if old_price is not None:
        old = Listing(source="", source_name="", item_key="", title="", url="", price=old_price,
                      currency=item.currency).price_text
        price = f"{price} (prima {e(old)})"
    return (
        f"<b>{header}</b>\n\n"
        f"Categoria: {e(keyword)}\n"
        f"Titolo: {e(item.title)}\n"
        f"Prezzo: {price}\n"
        f"Sito: {e(site)}\n"
        f"Link: {e(item.url)}"
    )


class Notifier:
    def __init__(self, bot: Bot, recipients: Callable[[], list]):
        self.bot = bot
        self.recipients = recipients

    async def send_listing(self, item: Listing, keyword: str, old_price: Optional[float] = None) -> bool:
        text = format_listing(item, keyword, old_price)
        results = [await self._send_listing_to(chat_id, item, text) for chat_id in self.recipients()]
        return any(results)

    async def _send_listing_to(self, chat_id: int, item: Listing, text: str) -> bool:
        button = InlineKeyboardMarkup([[InlineKeyboardButton("Apri annuncio", url=item.url)]])
        photos = item.images[: config.MAX_PHOTOS]
        try:
            if len(photos) > 1:
                media = [InputMediaPhoto(url) for url in photos]
                media[0] = InputMediaPhoto(photos[0], caption=text, parse_mode=ParseMode.HTML)
                await self.bot.send_media_group(chat_id, media)
                return True
            if len(photos) == 1:
                await self.bot.send_photo(chat_id, photos[0], caption=text,
                                          parse_mode=ParseMode.HTML, reply_markup=button)
                return True
        except Forbidden as exc:
            log.warning("La chat %s ha bloccato il bot: %s", chat_id, exc)
            return False
        except BadRequest as exc:
            log.warning("Foto rifiutate da Telegram (%s), invio solo testo", exc)
        except (RetryAfter, TimedOut, NetworkError) as exc:
            log.warning("Telegram non disponibile, riprovo al prossimo ciclo: %s", exc)
            return False
        except TelegramError as exc:
            log.warning("Invio foto fallito (%s), invio solo testo", exc)
        try:
            await self.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=button)
            return True
        except TelegramError as exc:
            log.error("Invio notifica a %s fallito: %s", chat_id, exc)
            return False

    async def alert(self, text: str):
        for chat_id in self.recipients():
            try:
                await self.bot.send_message(chat_id, f"Avviso: {text}")
            except TelegramError as exc:
                log.error("Invio avviso a %s fallito: %s", chat_id, exc)
