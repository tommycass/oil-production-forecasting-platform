import os
from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address

load_dotenv()

RATE_LIMIT = os.getenv("RATE_LIMIT", "60/minute")

limiter = Limiter(key_func=get_remote_address)
