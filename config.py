import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH: str = os.getenv("DB_PATH", "fetchr.db")

# Realistic Chrome UA so PetFinder doesn't fingerprint us as a bot immediately.
# Override via USER_AGENT in .env if you need to rotate.
USER_AGENT: str = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)
