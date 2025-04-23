import os
import logging
from dotenv import load_dotenv
import telegram
from firebase_keywords import get_keywords
from firebase_seen import get_seen_posts, add_seen_post

# Carica variabili d'ambiente
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Inizializza il bot Telegram
bot = telegram.Bot(token=TOKEN)

# Dati fake da scansionare (puoi sostituire con scraping reale)
FAKE_POSTS = [
    {"title": "Vendo bici da corsa", "price": "300€", "link": "http://example.com/bici", "image": None},
    {"title": "PlayStation 5 nuova", "price": "450€", "link": "http://example.com/ps5", "image": None},
    {"title": "MacBook Air 2020", "price": "700€", "link": "http://example.com/macbook", "image": None},
]

def scan_and_notify():
    logging.info("🔍 Inizio scansione...")
    keywords = get_keywords()
    seen_links = get_seen_posts()

    for post in FAKE_POSTS:
        title = post["title"].lower()
        link = post["link"]

        if link not in seen_links and any(kw in title for kw in keywords):
            msg = f"📦 {post['title']}\n💸 {post['price']}\n🔗 {post['link']}"
            bot.send_message(chat_id=CHAT_ID, text=msg)
            logging.info(f"✅ Inviato: {post['title']}")
            add_seen_post(link)

    logging.info("✅ Scansione completata.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    scan_and_notify()