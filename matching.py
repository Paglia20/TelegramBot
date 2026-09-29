import hashlib
import re
import unicodedata
from typing import Iterable, Optional

from models import Keyword

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_ALPHA_DIGIT = re.compile(r"(?<=[a-z])(?=[0-9])|(?<=[0-9])(?=[a-z])")
_MAX_WINDOW = 6


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _NON_ALNUM.sub(" ", text)
    text = _ALPHA_DIGIT.sub(" ", text)
    return " ".join(text.split())


class _Title:
    __slots__ = ("tokens", "windows")

    def __init__(self, title: str):
        tokens = normalize(title).split()
        self.tokens = set(tokens)
        self.windows = set()
        for i in range(len(tokens)):
            joined = ""
            for j in range(i, min(i + _MAX_WINDOW, len(tokens))):
                joined += tokens[j]
                self.windows.add(joined)


def _term_matches(norm_term: str, title: _Title) -> bool:
    parts = norm_term.split()
    if not parts:
        return False
    if all(p in title.tokens for p in parts):
        return True
    return "".join(parts) in title.windows


class Matcher:
    def __init__(self, keywords: Iterable[Keyword], excludes: Iterable[str]):
        self.keywords = sorted(keywords, key=lambda k: len(k.norm), reverse=True)
        self.excludes = [normalize(e) for e in excludes if normalize(e)]

    def match(self, title: str) -> Optional[Keyword]:
        parsed = _Title(title)
        for word in self.excludes:
            if _term_matches(word, parsed):
                return None
        for kw in self.keywords:
            if _term_matches(kw.norm, parsed):
                return kw
        return None

    def excluded_by(self, title: str) -> Optional[str]:
        parsed = _Title(title)
        for word in self.excludes:
            if _term_matches(word, parsed):
                return word
        return None


def split_terms(text: str) -> list:
    return [t.strip() for t in re.split(r"[,\n;]+", text) if t.strip()]


def fingerprint(seller: str, title: str) -> str:
    raw = f"{seller.casefold().strip()}|{normalize(title)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
