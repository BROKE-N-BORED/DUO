"""Small text helpers: whole-word phrase matching (the relay's
stop and agreement checks) and stop-phrase field parsing.

Split out of dual_chat.py in v0.25 (audit T14).
"""

import re


def parse_stop_phrases(text):
    """Split a comma-separated stop-phrase field into clean phrases."""
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def phrase_matches(phrase, text):
    """Whether phrase appears in text as whole words, not a fragment.

    Case-insensitive. The phrase must not be glued to another word on
    either side: "agreed" matches "Agreed, moving on." but not
    "I disagreed with that" (v0.12: substring matching ended a relay
    the moment a model disagreed). Punctuation is fine on either side,
    so "Sounds good!" still matches, and so does a user stop phrase
    that ends in punctuation.
    """
    if not phrase:
        return False
    pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None
