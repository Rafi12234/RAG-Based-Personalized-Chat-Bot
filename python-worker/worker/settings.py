
"""Configuration for DocMind-AI Python services."""

import os

from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.environ.get(
    "REDIS_URL",
    "redis://127.0.0.1:6380/0",
)

DATABASE_HOST = os.environ.get(
    "DATABASE_HOST",
    "127.0.0.1",
)

DATABASE_PORT = int(
    os.environ.get("DATABASE_PORT", "3307")
)

DATABASE_USER = os.environ.get(
    "DATABASE_USER",
    "docmind_app",
)

DATABASE_PASSWORD = os.environ.get(
    "DATABASE_PASSWORD",
    "",
)

DATABASE_NAME = os.environ.get(
    "DATABASE_NAME",
    "docmind_ai",
)
