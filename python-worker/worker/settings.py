"""Local worker configuration; production secrets come from the environment."""
import os
from dotenv import load_dotenv

load_dotenv()
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6380/0")
