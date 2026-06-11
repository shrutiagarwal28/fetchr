"""
fetchr CLI — scrape dog adoption listings into a local Postgres database.

Usage:
  python main.py scrape --source petfinder --max 100
  python main.py scrape --source adoptapet --max 100
  python main.py scrape --source all --max 200

  python main.py explore --source petfinder --max 200
"""

from __future__ import annotations

import argparse
import logging
import sys

from config import PETFINDER_LOCATION

# Configure logging before importing scrapers so all modules pick up the level
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("fetchr")


def _run_scrape(source: str, max_results: int, headless: bool, location: str) -> None:
    from scrapers.petfinder import PetFinderScraper
    from scrapers.adoptapet import AdoptAPetScraper
    from db.connection import export_to_json

    scrapers = {
        "petfinder": lambda: PetFinderScraper(max_results=max_results, headless=headless, location=location).run(),
        "adoptapet": lambda: AdoptAPetScraper(max_results=max_results, headless=headless, location=location).run(),
    }

    targets = list(scrapers.keys()) if source == "all" else [source]

    for target in targets:
        logger.info("Starting scraper: %s (max=%d)", target, max_results)
        scrapers[target]()

    export_to_json()


def _run_explore(source: str, max_results: int, headless: bool, location: str) -> None:
    from scrapers.petfinder_explore import PetFinderExploreScraper

    scrapers = {
        "petfinder": lambda: PetFinderExploreScraper(max_results=max_results, headless=headless, location=location).run(),
    }

    if source not in scrapers:
        logger.error("No explore scraper registered for source: %s", source)
        sys.exit(1)

    logger.info("Starting explore scraper: %s (max=%d)", source, max_results)
    scrapers[source]()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fetchr",
        description="Scrape dog adoption listings into a Postgres database.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scrape_parser = subparsers.add_parser("scrape", help="Run a scraper")
    scrape_parser.add_argument(
        "--source",
        choices=["petfinder", "adoptapet", "all"],
        default="petfinder",
        help="Which source to scrape (default: petfinder)",
    )
    scrape_parser.add_argument(
        "--max",
        type=int,
        default=100,
        dest="max_results",
        help="Maximum number of dogs to scrape per source (default: 100)",
    )
    scrape_parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in visible (non-headless) mode — helps bypass bot detection",
    )
    scrape_parser.add_argument(
        "--location",
        default=PETFINDER_LOCATION,
        metavar="STATE/CITY",
        help=(
            "Location to search, as '{state}/{city}', e.g. 'nj/jersey-city' or 'ny/new york'. "
            f"Defaults to PETFINDER_LOCATION env var, currently '{PETFINDER_LOCATION}'."
        ),
    )

    explore_parser = subparsers.add_parser("explore", help="Run the GraphQL-based search scraper")
    explore_parser.add_argument(
        "--source",
        choices=["petfinder"],
        default="petfinder",
        help="Which source to explore (default: petfinder)",
    )
    explore_parser.add_argument(
        "--max",
        type=int,
        default=200,
        dest="max_results",
        help="Maximum number of dogs to fetch per run (default: 200)",
    )
    explore_parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in visible (non-headless) mode — helps bypass bot detection",
    )
    explore_parser.add_argument(
        "--location",
        default=PETFINDER_LOCATION,
        metavar="STATE/CITY",
        help=(
            "Location to search, as '{state}/{city}', e.g. 'nj/jersey-city'. "
            f"Defaults to PETFINDER_LOCATION env var, currently '{PETFINDER_LOCATION}'."
        ),
    )

    args = parser.parse_args()

    if args.command == "scrape":
        _run_scrape(args.source, args.max_results, headless=not args.no_headless, location=args.location)
    elif args.command == "explore":
        _run_explore(args.source, args.max_results, headless=not args.no_headless, location=args.location)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
