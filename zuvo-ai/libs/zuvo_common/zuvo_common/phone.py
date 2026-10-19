"""Canonical phone format: E.164 digits without '+' (same as Meta wa_id)."""
import re

_NON_DIGIT = re.compile(r"\D")


def normalize_phone(raw: str | None, default_country_code: str = "91") -> str:
    if not raw:
        raise ValueError("phone number is required")
    digits = _NON_DIGIT.sub("", str(raw))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10:
        digits = default_country_code + digits
    if not 8 <= len(digits) <= 15:
        raise ValueError("phone number has invalid length")
    return digits
