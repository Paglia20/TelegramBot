import requests

FIREBASE_URL = "https://telegrambot-7cbb7-default-rtdb.firebaseio.com/keywords.json"

def get_keywords():
    try:
        response = requests.get(FIREBASE_URL)
        data = response.json()
        return list(data) if data else []
    except Exception as e:
        print(f"[Firebase] Errore nel recupero keyword: {e}")
        return []

def add_keyword(word):
    keywords = get_keywords()
    if word not in keywords:
        keywords.append(word)
        requests.put(FIREBASE_URL, json=keywords)

def remove_keyword(word):
    keywords = get_keywords()
    keywords = [k for k in keywords if k != word]
    requests.put(FIREBASE_URL, json=keywords)