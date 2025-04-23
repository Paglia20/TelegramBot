import os
import logging
from dotenv import load_dotenv
import telegram
from firebase_keywords import get_keywords
from firebase_seen import get_seen_posts, add_seen_post

# Carica le variabili d'ambiente
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Inizializza il bot
bot = telegram.Bot(token=TOKEN)

# Fake dataset (sostituibile con scraping reale)
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

    for post in FAKE_POSTS:
        title = post["title"].lower()
        link = post["link"]

        if link not in seen_links and any(kw in title for kw in keywords):
            msg = f"📦 {post['title']}\n💸 {post['price']}\n🔗 {post['link']}"
            bot.send_message.sync(chat_id=CHAT_ID, text=msg)
            logging.info(f"✅ Inviato: {post['title']}")
            add_seen_post(link)
            matches += 1

    if matches == 0:
        bot.send_message.sync(chat_id=CHAT_ID, text="⚠️ Nessun annuncio trovato in questa scansione.")
        logging.info("⚠️ Nessun match trovato.")

    logging.info("✅ Scansione completata.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    scan_and_notify()