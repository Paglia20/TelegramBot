import asyncio

import httpx

import config
from matching import Matcher, normalize
from models import Keyword
from sources import SourceError
from sources.ebay import API_ROOT, EbayAuth, EbaySource, build_queries, get_rate_limits

TEST_KEYWORDS = ["rtx 4090", "game boy", "thinkpad x1"]
SANDBOX_TEST_KEYWORDS = ["iphone", "laptop", "camera"]
OR_TEST_MARKET = "EBAY_DE"
SANDBOX_OR_TEST_MARKET = "EBAY_US"


async def main():
    if not (config.EBAY_CLIENT_ID and config.EBAY_CLIENT_SECRET):
        print("Mancano EBAY_CLIENT_ID e EBAY_CLIENT_SECRET nel file .env")
        return
    terms = SANDBOX_TEST_KEYWORDS if config.EBAY_SANDBOX else TEST_KEYWORDS
    or_market = SANDBOX_OR_TEST_MARKET if config.EBAY_SANDBOX else OR_TEST_MARKET
    print(f"Ambiente eBay: {config.EBAY_ENV.upper()} ({API_ROOT})\n")

    async with httpx.AsyncClient(timeout=15) as client:
        auth = EbayAuth(client, config.EBAY_CLIENT_ID, config.EBAY_CLIENT_SECRET)
        try:
            await auth.token()
        except SourceError as exc:
            print(exc)
            return
        print("Token eBay: OK\n")

        for mid in config.EBAY_MARKETS:
            src = EbaySource(mid, auth, client)
            try:
                items = await src._search(terms[0], offset=0)
                print(f"{src.name}: {len(items)} risultati per '{terms[0]}'")
            except SourceError as exc:
                print(f"{src.name}: errore {exc}")

        keywords = [Keyword(i, t, normalize(t), 0) for i, t in enumerate(terms)]
        src = EbaySource(or_market, auth, client)
        combined = build_queries(keywords)[0]
        print(f"\nTest ricerca combinata su {src.name}: q={combined!r}")
        try:
            found = [x for x in (src._to_listing(i) for i in await src._search(combined, offset=0)) if x]
            for kw in keywords:
                hits = [f for f in found if Matcher([kw], []).match(f.title)]
                alone = [x for x in (src._to_listing(i) for i in await src._search(kw.term, offset=0)) if x]
                print(f"  '{kw.term}': {len(hits)} nella ricerca combinata, {len(alone)} da sola")
            print("Se una parola ha annunci da sola ma 0 nella ricerca combinata,")
            print("imposta EBAY_QUERY_MODE = \"single\" in config.py.")
            if found:
                s = found[0]
                print(f"\nEsempio: {s.title} | {s.price_text} | creato {s.created_at} | "
                      f"origine {s.origin} | foto {len(s.images)}\n{s.url}")
        except SourceError as exc:
            print(f"  errore {exc}")

        rate = await get_rate_limits(auth)
        print(f"\nQuota Browse API: {rate}" if rate else "\nQuota Browse API: non disponibile")
        if config.EBAY_SANDBOX:
            print("\nIn sandbox gli annunci sono pochi e finti, spesso solo su eBay USA.")
            print("Zero risultati sui mercati europei e' normale. Serve a provare chiavi e bot, non la ricerca.")


if __name__ == "__main__":
    asyncio.run(main())
