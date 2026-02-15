from datetime import datetime, timedelta
from secrets import token_urlsafe


def generate_token_value():
    return token_urlsafe(32)


def default_expiry(days=7):
    return datetime.utcnow() + timedelta(days=days)
