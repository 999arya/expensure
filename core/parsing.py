"""Turning money a person typed into integer paise.

People type "₹1,23,456.78", "1234.5" and "1 000". All of those mean the same
kind of thing, and all of them must become an exact integer before they touch
the database.
"""

import re

# Rupees, optional two decimal places. Anything else is a typo, not a format.
_ALLOWED = re.compile(r"^(\d+)(?:\.(\d{1,2}))?$")

# Characters people put in amounts that carry no meaning.
_NOISE = str.maketrans("", "", "₹, \u00a0\u2009")


class AmountError(ValueError):
    """The text could not be read as an amount."""


def parse_inr(value, *, allow_negative=False):
    """Return integer paise for an amount typed by a person.

    Raises AmountError with a message that can be shown next to a form field.
    """
    if value is None:
        raise AmountError("Enter an amount.")

    text = str(value).strip()
    if not text:
        raise AmountError("Enter an amount.")

    negative = text.startswith("-")
    if negative:
        text = text[1:].strip()

    text = text.translate(_NOISE)
    match = _ALLOWED.match(text)
    if not match:
        raise AmountError("Enter an amount like 1,234.50.")

    rupees, paise = match.groups()
    amount = int(rupees) * 100 + int((paise or "").ljust(2, "0"))

    if negative:
        if not allow_negative:
            raise AmountError("Enter a positive amount.")
        amount = -amount

    return amount
