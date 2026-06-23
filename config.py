import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL: str = os.getenv("DATABASE_URL", "")
JSON_PATH: str = os.getenv("JSON_PATH", "fetchr.json")
# Persisted log of data-quality / scrape errors (WARNING+). Separate from the
# console stream so bad-row issues (e.g. future-dated birth_date) survive a run
# and can be audited later instead of scrolling past in the terminal.
SCRAPE_ERROR_LOG_PATH: str = os.getenv("SCRAPE_ERROR_LOG_PATH", "scrape_errors.log")
# Format: "{state}/{city}", e.g. "nj/jersey-city" or "ny/new york".
# Spaces and hyphens in the city are stripped when building the PetFinder URL.
PETFINDER_LOCATION: str = os.getenv("PETFINDER_LOCATION", "nj/jersey-city")

# Realistic Chrome UA so PetFinder doesn't fingerprint us as a bot immediately.
# Override via USER_AGENT in .env if you need to rotate.
USER_AGENT: str = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)
