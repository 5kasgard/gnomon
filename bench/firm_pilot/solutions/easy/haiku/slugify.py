import re

_NON_ALNUM_RUN = re.compile(r"[^a-z0-9]+")


def slugify(s: str) -> str:
    """Lowercase s, collapse each run of non-ASCII-alphanumeric characters
    into a single '-', and strip leading/trailing '-'."""
    return _NON_ALNUM_RUN.sub("-", s.lower()).strip("-")
