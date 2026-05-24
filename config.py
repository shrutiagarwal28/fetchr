import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH: str = os.getenv("DB_PATH", "fetchr.db")
JSON_PATH: str = os.getenv("JSON_PATH", "fetchr.json")
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
