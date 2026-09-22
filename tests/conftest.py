"""Load .env once for the whole test session so integration tests that need
HOST_DATABASE_URL/DATABASE_URL behave the same under pytest as under the CLI.
"""
from dotenv import load_dotenv

load_dotenv()
