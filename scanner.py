import os
import logging
from dotenv import load_dotenv
from telegram import Bot
from firebase_keywords import get_keywords
from firebase_seen import get_seen_posts, add_seen_post

# Carica variabili d'ambiente
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Inizializza bot sincrono
bot = Bot(token=TOKEN)

# Fake annunci per simulazione
FAKE_POSTS = [
    {"title": "Vendo bici da corsa", "price": "300€", "link": "http://example.com/bici", "image": None},
    {"title": "PlayStation 5 nuova", "price": "450€", "link": "http://example.com/ps5", "image": None},
    {"title": "MacBook Air 2020", "price": "700€", "link": "http://example.com/macbook", "image": None},
]

def scan_and_notify():
    logging.info("🔍 Inizio scansione...")
    keywords = get_keywords()
    seen_links = get_seen_posts()
    matches = 0

    logging.info(f"🔑 Parole chiave attive: {keywords}")

    for post in FAKE_POSTS:
        title = post["title"].lower()
        link = post["link"]

        if link not in seen_links and any(kw in title for kw in keywords):
            msg = f"📦 {post['title']}\n💸 {post['price']}\n🔗 {post['link']}"
            bot.send_message(chat_id=CHAT_ID, text=msg)
            logging.info(f"✅ Inviato: {post['title']}")
            add_seen_post(link)
            matches += 1

    if matches == 0:
        keywords_str = ", ".join(keywords) if keywords else "(nessuna keyword)"
        msg = f"⚠️ Nessun annuncio trovato in questa scansione.\n🔍 Parole chiave: {keywords_str}"
        bot.send_message(chat_id=CHAT_ID, text=msg)
        logging.info(f"⚠️ Nessun match trovato. Keywords: {keywords_str}")

    logging.info("✅ Scansione completata.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    scan_and_notify()