import json
from pathlib import Path

KEYWORDS_FILE = Path("keywords.json")

def load_keywords():
    if not KEYWORDS_FILE.exists():
        return []
    with open(KEYWORDS_FILE, "r") as f:
        return json.load(f)

def save_keywords(keywords):
    with open(KEYWORDS_FILE, "w") as f:
        json.dump(list(set(keywords)), f, indent=2)

def add_keyword(word):
    keywords = load_keywords()
    keywords.append(word.lower())
    save_keywords(keywords)

def remove_keyword(word):
    keywords = load_keywords()
    keywords = [k for k in keywords if k != word.lower()]
    save_keywords(keywords)

def list_keywords():
    return load_keywords()