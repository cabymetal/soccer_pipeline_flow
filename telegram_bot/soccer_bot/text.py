import re
import unicodedata


def normalize(value: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"[^a-z0-9 ]", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()
