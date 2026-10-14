"""One-off catalogue snapshot: game top-ups, Roblox, gaming gift cards and non-Steam keys.

Look at the real response shapes first (about 9 requests):
    docker compose run --rm bot python -m scripts.snapshot_import --probe
Then import with your rate and markup:
    docker compose run --rm bot python -m scripts.snapshot_import --usd-rub 95 --markup 15
Re-running continues where a previous run stopped and keeps manually edited prices.
"""
import argparse
import asyncio
import json
import logging
from decimal import Decimal

from config import get_settings
from database.session import SessionLocal
from services.snapshot_service import SECTIONS, Pricing, count_by_section, probe, run_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", action="store_true", help="only print sample supplier responses, write nothing")
    parser.add_argument("--usd-rub", type=Decimal, help="RUB per 1 USD; defaults to USD_TO_RUB_MINOR / 100")
    parser.add_argument("--markup", type=Decimal, default=Decimal("15"), help="markup in percent on top of the supplier price (default 15)")
    parser.add_argument("--sections", default=",".join(SECTIONS), help=f"comma-separated subset of: {', '.join(SECTIONS)}")
    parser.add_argument("--budget", type=int, default=1500, help="maximum supplier requests for this run (daily limit is 5000)")
    parser.add_argument("--per-minute", type=int, default=25, help="request pace; the supplier allows 30 per minute")
    parser.add_argument("--refresh", action="store_true", help="re-fetch categories that are already imported")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = get_settings()
    if not settings.resellcodes_api_key:
        raise SystemExit("RESELLCODES_API_KEY is empty")
    if args.probe:
        print(json.dumps(await probe(settings.resellcodes_api_key), ensure_ascii=False, indent=2)[:20000])
        return
    rate = args.usd_rub or (Decimal(settings.usd_to_rub_minor) / 100 if settings.usd_to_rub_minor else None)
    if not rate:
        raise SystemExit("Pass --usd-rub (for example --usd-rub 95): the cart only sells items priced in RUB")
    sections = tuple(s.strip() for s in args.sections.split(",") if s.strip())
    unknown = set(sections) - set(SECTIONS)
    if unknown:
        raise SystemExit(f"Unknown sections: {', '.join(sorted(unknown))}")
    print(f"Pricing: supplier USD × {rate} RUB × (1 + {args.markup}%), rounded up to whole roubles")
    async with SessionLocal() as session:
        result = await run_snapshot(session, settings.resellcodes_api_key, Pricing(rate, args.markup), sections=sections,
                                    budget=args.budget, per_minute=args.per_minute, refresh=args.refresh)
        totals = await count_by_section(session)
    print(f"Requests used: {result.requests}")
    print(f"Result: {result.counters}")
    for warning in result.warnings[:50]:
        print(f"! {warning}")
    print(f"Active products by section: {totals}")
    if result.stopped_by_budget:
        print("Stopped by the request budget. Run the same command again later to continue.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main())
