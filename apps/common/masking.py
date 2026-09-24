"""
Redaction for the few endpoints an anonymous caller can reach. A ratepayer
holding a bill reference should be able to confirm what they owe; nobody who
merely guesses a reference should learn who lives behind it.
"""


def mask_name(name: str) -> str:
    """'Amina Bello' -> 'A*** B***'. Fixed-width stars so the length of the
    real name isn't leaked either."""
    return " ".join(f"{part[0]}***" for part in (name or "").split())


def mask_tail(value: str, keep: int = 3) -> str:
    """'08031234567' -> '********567' (last ``keep`` characters survive, enough
    for a ratepayer to recognise their own number or reference)."""
    value = value or ""
    if len(value) <= keep:
        return "*" * len(value)
    return "*" * (len(value) - keep) + value[-keep:]
