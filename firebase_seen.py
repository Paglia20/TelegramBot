import requests

FIREBASE_SEEN_URL = "https://telegrambot-7cbb7-default-rtdb.firebaseio.com/seen_posts.json"

def get_seen_posts():
    try:
        response = requests.get(FIREBASE_SEEN_URL)
        data = response.json()
        return set(data) if data else set()
    except Exception as e:
        print(f"[Firebase] Errore nel recupero dei post visti: {e}")
        return set()

def add_seen_post(link):
    seen = get_seen_posts()
    if link not in seen:
        seen.add(link)
        requests.put(FIREBASE_SEEN_URL, json=list(seen))