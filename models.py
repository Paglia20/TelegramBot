from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Listing:
    source: str
    source_name: str
    item_key: str
    title: str
    url: str
    price: Optional[float] = None
    currency: str = ""
    is_auction: bool = False
    bid_count: int = 0
    images: list = field(default_factory=list)
    seller: str = ""
    origin: str = ""
    origin_name: str = ""
    location_country: str = ""
    created_at: Optional[datetime] = None

    @property
    def price_text(self) -> str:
        if self.price is None:
            return "n.d."
        amount = f"{self.price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        text = f"{amount} {self.currency}".strip()
        if self.is_auction:
            text += f" (asta, {self.bid_count} offerte)"
        return text


@dataclass
class FetchResult:
    listings: list
    requests: int


@dataclass
class Keyword:
    id: int
    term: str
    norm: str
    created_at: float
