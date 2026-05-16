"""
fetchr CLI — scrape dog adoption listings into a local SQLite database.

Usage:
  python main.py scrape --source petfinder --max 100
  python main.py scrape --source adoptapet --max 100
  python main.py scrape --source all --max 200
"""

from __future__ import annotations

import argparse
import logging
import sys

# Configure logging before importing scrapers so all modules pick up the level
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("fetchr")


def _run_scrape(source: str, max_results: int, headless: bool) -> None:
    from scrapers.petfinder import PetFinderScraper
    from scrapers.adoptapet import AdoptAPetScraper

    scrapers = {
        "petfinder": lambda: PetFinderScraper(max_results=max_results, headless=headless).run(),
        "adoptapet": lambda: AdoptAPetScraper(max_results=max_results, headless=headless).run(),
    }

    targets = list(scrapers.keys()) if source == "all" else [source]

    for target in targets:
        logger.info("Starting scraper: %s (max=%d)", target, max_results)
        scrapers[target]()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fetchr",
        description="Scrape dog adoption listings into a local SQLite database.",
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

    args = parser.parse_args()

    if args.command == "scrape":
        _run_scrape(args.source, args.max_results, headless=not args.no_headless)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
