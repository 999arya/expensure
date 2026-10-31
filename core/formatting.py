"""Money formatting.

All amounts in this project are integer paise, never floats. This module is the
single place that turns paise into something a person reads.
"""


def format_inr(paise: int) -> str:
    """Format integer paise as rupees with Indian digit grouping.

    Indian grouping puts the last three digits together, then groups of two:
    123456789 paise -> "₹12,34,567.89".
    """
    if not isinstance(paise, int):
        raise TypeError(f"amounts are integer paise, got {type(paise).__name__}")

    sign = "-" if paise < 0 else ""
    rupees, remainder = divmod(abs(paise), 100)
    digits = str(rupees)

    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])

    return f"{sign}₹{digits}.{remainder:02d}"
