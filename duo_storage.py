"""Files DUO writes next to the app: atomic writes, saved model
pairs, saved relay presets, and saved conversations (JSON).

Split out of dual_chat.py in v0.25 (audit T14). The *_path()
helpers resolve "next to the app" from this file's own
location -- this module sits in the same folder dual_chat.py
does, so the paths are unchanged.
"""

import json
import os
import tempfile

from duo_model import Conversation


# Saved model pairs live in a plain JSON file next to the app.
# DUAL_CHAT_PAIRS_FILE points at a different file (used by the tests).
PAIRS_ENV = "DUAL_CHAT_PAIRS_FILE"


PAIRS_FILENAME = "duo-pairs.json"


# Saved relay presets live in their own plain JSON file next to the
# app, same shape as the pairs file. DUAL_CHAT_PRESETS_FILE points
# at a different file (used by the tests).
PRESETS_ENV = "DUAL_CHAT_PRESETS_FILE"


PRESETS_FILENAME = "duo-relay-presets.json"


def pairs_path():
    """Where saved pairs live: duo-pairs.json next to the app.

    DUAL_CHAT_PAIRS_FILE overrides it (the tests point it at a temp file).
    """
    override = os.environ.get(PAIRS_ENV)
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        PAIRS_FILENAME)


def load_pairs(path):
    """Saved pairs as {name: setup} from a JSON file.

    Missing, unreadable, or malformed files read as no pairs -- a bad
    file never stops the app from starting.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    pairs = data.get("pairs") if isinstance(data, dict) else None
    if not isinstance(pairs, dict):
        return {}
    return {name: setup for name, setup in pairs.items()
            if isinstance(setup, dict)}


def save_pair(path, name, setup):
    """Save one pair (creating or overwriting it) in the JSON file.

    A setup is {"model_a", "model_b", "instruction_a",
    "instruction_b"}; models may be None when nothing is picked.
    The write is atomic: a failed save leaves the previous file
    intact and raises the OSError.
    """
    pairs = load_pairs(path)
    pairs[name] = setup
    text = json.dumps({"pairs": pairs}, indent=2, sort_keys=True) + "\n"
    write_text_atomic(path, text, prefix=".duo-pairs-")


def delete_pair(path, name):
    """Remove one pair from the JSON file. Returns True when a pair
    was actually removed; a missing name is a no-op returning False.
    The rewrite is atomic: a failed delete leaves the previous file
    intact and raises the OSError.
    """
    pairs = load_pairs(path)
    if name not in pairs:
        return False
    del pairs[name]
    text = json.dumps({"pairs": pairs}, indent=2, sort_keys=True) + "\n"
    write_text_atomic(path, text, prefix=".duo-pairs-")
    return True


def presets_path():
    """Where saved relay presets live: duo-relay-presets.json next
    to the app.

    DUAL_CHAT_PRESETS_FILE overrides it (the tests point it at a
    temp file).
    """
    override = os.environ.get(PRESETS_ENV)
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        PRESETS_FILENAME)


def is_valid_preset(config):
    """Whether a loaded preset config has all six relay fields,
    typed right: start_side "A"/"B", mode "blind"/"aware", turn_cap
    an int, stop_phrases a list of strings, and both flags bools.
    A hand-edited cap outside 1-50 is fine -- the spinbox clamps it
    when the preset is applied, the same way a relay clamps it.
    """
    if not isinstance(config, dict):
        return False
    if config.get("start_side") not in ("A", "B"):
        return False
    if config.get("mode") not in ("blind", "aware"):
        return False
    cap = config.get("turn_cap")
    if isinstance(cap, bool) or not isinstance(cap, int):
        return False
    phrases = config.get("stop_phrases")
    if not isinstance(phrases, list) or not all(
            isinstance(p, str) for p in phrases):
        return False
    if not isinstance(config.get("stop_on_agreement"), bool):
        return False
    if not isinstance(config.get("review_each_hop"), bool):
        return False
    return True


def load_presets(path):
    """Saved relay presets as {name: config} from a JSON file.

    Missing, unreadable, or malformed files read as no presets --
    a bad file never stops the app from starting. Presets whose
    fields are missing or the wrong type are skipped, so one bad
    entry can't poison the rest.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    presets = data.get("presets") if isinstance(data, dict) else None
    if not isinstance(presets, dict):
        return {}
    return {name: config for name, config in presets.items()
            if is_valid_preset(config)}


def presets_file_corrupt(path):
    """Whether the presets file exists but can't be read as presets
    at all -- bad JSON, or JSON without a "presets" object. A
    missing file is fine (no presets saved yet); a file whose
    entries are merely invalid is not corrupt, just empty.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    presets = data.get("presets") if isinstance(data, dict) else None
    return not isinstance(presets, dict)


def save_preset(path, name, config):
    """Save one relay preset (creating or overwriting it) in the
    JSON file.

    A config is {"start_side", "mode", "turn_cap",
    "stop_phrases", "stop_on_agreement", "review_each_hop"}.
    The write is atomic: a failed save leaves the previous file
    intact and raises the OSError.
    """
    presets = load_presets(path)
    presets[name] = config
    text = json.dumps({"presets": presets}, indent=2, sort_keys=True) + "\n"
    write_text_atomic(path, text, prefix=".duo-presets-")


def delete_preset(path, name):
    """Remove one relay preset from the JSON file. Returns True when
    a preset was actually removed; a missing name is a no-op
    returning False. The rewrite is atomic: a failed delete leaves
    the previous file intact and raises the OSError.
    """
    presets = load_presets(path)
    if name not in presets:
        return False
    del presets[name]
    text = json.dumps({"presets": presets}, indent=2, sort_keys=True) + "\n"
    write_text_atomic(path, text, prefix=".duo-presets-")
    return True


# Conversation persistence (v0.17, audit T6): save the whole
# conversation to a JSON file and load it back. The file carries both
# sides' full histories -- every entry's role, content, label, and
# short display text -- plus both models, both instruction fields,
# and the interleaved full-transcript log, so a load restores exactly
# what was saved. The full content is saved, not just the display
# text: a message carrying attached files must come back with the
# file contents intact, or the next turn would send the model a
# different conversation than the one saved. The format is versioned;
# a file from a different version is refused rather than half-loaded.

CONVERSATION_FORMAT_VERSION = 1


class ConversationError(Exception):
    """A saved conversation file is unreadable or the wrong format."""


def conversation_to_data(convo, log):
    """Serialize a conversation: both sides plus the full log."""
    data = {"version": CONVERSATION_FORMAT_VERSION, "sides": {}}
    for side in ("A", "B"):
        side_obj = convo.sides[side]
        data["sides"][side] = {
            "model": side_obj.model,
            "instruction": side_obj.instruction,
            "entries": [
                {"role": entry["role"], "content": entry["content"],
                 "label": side_obj.labels[i],
                 "display": side_obj.display[i]}
                for i, entry in enumerate(side_obj.history)],
        }
    data["log"] = [[side, label, text] for side, label, text in log]
    return data


def data_to_conversation(data):
    """Validate saved data and build (Conversation, log) from it.

    Raises ConversationError on anything wrong -- not a dict, the
    wrong version, missing keys, or entries that are not shaped like
    entries. Everything is checked before anything is built, so a
    bad file can never leave a half-loaded conversation behind.
    """
    def _bad():
        raise ConversationError("this is not a DUO conversation file")

    if not isinstance(data, dict):
        _bad()
    if data.get("version") != CONVERSATION_FORMAT_VERSION:
        raise ConversationError(
            "this conversation file is from a different version of DUO")
    sides = data.get("sides")
    if not isinstance(sides, dict):
        _bad()
    convo = Conversation()
    for side in ("A", "B"):
        saved = sides.get(side)
        if not isinstance(saved, dict):
            _bad()
        model = saved.get("model")
        if model is not None and not isinstance(model, str):
            _bad()
        instruction = saved.get("instruction")
        if not isinstance(instruction, str):
            _bad()
        entries = saved.get("entries")
        if not isinstance(entries, list):
            _bad()
        side_obj = convo.sides[side]
        side_obj.model = model
        side_obj.instruction = instruction
        for entry in entries:
            if not isinstance(entry, dict):
                _bad()
            role = entry.get("role")
            content = entry.get("content")
            label = entry.get("label")
            display = entry.get("display")
            if role not in ("user", "assistant"):
                _bad()
            if (not isinstance(content, str)
                    or not isinstance(label, str)
                    or (display is not None
                        and not isinstance(display, str))):
                _bad()
            side_obj.history.append({"role": role, "content": content})
            side_obj.labels.append(label)
            side_obj.display.append(display)
    raw_log = data.get("log")
    if not isinstance(raw_log, list):
        _bad()
    log = []
    for item in raw_log:
        if (not isinstance(item, list) or len(item) != 3
                or item[0] not in ("A", "B")
                or not isinstance(item[1], str)
                or not isinstance(item[2], str)):
            _bad()
        log.append((item[0], item[1], item[2]))
    return convo, log


def write_text_atomic(path, text, prefix=".duo-"):
    """Write ``text`` to ``path`` atomically: the content goes to a
    temp file in the same folder first, then the temp file is renamed
    over the target, so a crash mid-save can never leave a
    half-written file behind. A failed write removes the temp file
    and raises the OSError; the previous file, if any, stays intact.
    Every file DUO writes -- pairs, transcripts, conversations --
    goes through here (v0.18, audit T7)."""
    folder = os.path.dirname(os.path.abspath(path))
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=folder, prefix=prefix,
                suffix=".tmp", delete=False) as fh:
            tmp = fh.name
            fh.write(text)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def save_conversation_file(path, convo, log):
    """Write a conversation to ``path`` atomically (see
    write_text_atomic): a failed write removes the temp file and
    raises the OSError, and the previous file, if any, stays intact."""
    text = json.dumps(conversation_to_data(convo, log),
                      indent=2, ensure_ascii=False) + "\n"
    write_text_atomic(path, text, prefix=".duo-convo-")


def load_conversation_file(path):
    """Read a saved conversation file. Returns (Conversation, log).
    Raises ConversationError for bad JSON or a bad format, OSError
    for a file that can't be read."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError:
        raise ConversationError("this is not a DUO conversation file")
    return data_to_conversation(data)
