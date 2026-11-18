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


def phrase_is_whole_reply(phrase, text):
    """Whether text is just this phrase, nothing else.

    Case-insensitive. Leading and trailing whitespace, quotes,
    and sentence punctuation are ignored, and inner whitespace
    is collapsed, so "  Sounds good! " and "Sounds  good." both
    count as "sounds good" -- but "Sounds good, let's go." does
    not. The relay's loose agreement phrases (v0.57, P1-9) only
    stop the relay when the whole reply is the phrase: polite
    filler in the middle of a longer reply is not agreement.
    """
    if not phrase or not text:
        return False
    cleaned = " ".join(text.split()).strip(" .!?,;:'\"").lower()
    return cleaned == " ".join(phrase.split()).lower()
