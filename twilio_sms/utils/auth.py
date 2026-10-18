"""Authentication utilities for Twilio SMS."""

import os


def get_credentials() -> dict[str, str]:
    """
    Get developer-provided Twilio credentials from environment.

    Set as env vars on the hosted server. Users don't need to provide anything.
    """
    return {
        "account_sid": os.environ.get("TWILIO_ACCOUNT_SID", ""),
        "api_key_sid": os.environ.get("TWILIO_API_KEY_SID", ""),
        "api_key_secret": os.environ.get("TWILIO_API_KEY_SECRET", ""),
        "from_number": os.environ.get("TWILIO_FROM_NUMBER", ""),
    }
