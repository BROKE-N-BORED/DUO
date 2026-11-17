"""The conversation model: one message structure (ChatMessage), one
side's state (ChatSide) and both sides together (Conversation),
plus the summarize constants and the no-model-picked error.

Split out of dual_chat.py in v0.25 (audit T14). No Qt, no I/O:
duo_storage builds Conversations from saved files, and the GUI
holds the live one.

In v0.26 (audit T15) the three parallel arrays (history, labels,
display) became one list of ChatMessage objects: each message
carries its role, content, label, and short display text together,
so the three can never drift out of sync.

In v0.40 (audit T25) the relay session (RelaySession, with its
stop/cap constants) moved here from dual_chat.py: it is pure
session logic, no Qt, and it belongs with the conversation model.
"""

from dataclasses import dataclass

from duo_text import phrase_matches


MSG_NO_MODEL = "Pick a model for {side} first."


SUMMARY_KEEP_LAST = 4


SUMMARY_LABEL = "Summary of earlier messages"


# Pane identity (v0.40, audit T25): each side's color. Blue for A,
# purple for B -- readable on the light theme, distinct from each
# other and from the activity palette (green thinking, amber
# waiting, red failed, grey idle). The window's pane titles and
# transcript labels use these, and the phone deck's side headers
# use the same two. Color is always redundant with text -- the
# "Model A" / "Model B" labels stay -- so it reinforces identity,
# never carries it alone.
SIDE_COLORS = {"A": "#2563eb", "B": "#7c3aed"}


class NoModelSelected(Exception):
    """Raised when a send targets a side with no model picked."""

    def __init__(self, side):
        super().__init__(MSG_NO_MODEL.format(side=side))
        self.side = side


@dataclass
class ChatMessage:
    """One message: everything the window and the model need for it,
    in one place.

    ``role`` is "user" or "assistant" -- what the model sees.
    ``content`` is the full text -- what the model sees.
    ``label`` is the sender label the window shows ("You",
    "You [relay from Model A]", or the model name). Labels are
    window-only -- they never reach the model.
    ``display`` is an optional shorter text the window shows instead
    of the content (used when a message carries attached files, so
    the transcript stays readable). None means show the content
    as-is.
    ``source_side`` is the side a relayed or handed-off message came
    from ("A" / "B"), so the window can show it in the source side's
    color. None for the user's own words and for model replies.
    Display-only: it is never saved -- old conversation files load
    with None, and the JSON format is unchanged.
    """

    role: str
    content: str
    label: str
    display: str = None
    source_side: str = None

    def display_text(self):
        """What the window shows for this message: the short display
        text when one is set, else the full content."""
        return self.display if self.display is not None else self.content

    def for_model(self):
        """This message as the model sees it: role + content only."""
        return {"role": self.role, "content": self.content}


class ChatSide:
    """One side's state: its picked model and its own messages.

    ``messages`` is the single list of ChatMessage objects, in
    order. ``instruction`` is this side's system prompt ("" means
    no system message).
    """

    def __init__(self, name):
        self.name = name  # "A" or "B"
        self.model = None
        self.messages = []  # [ChatMessage]
        self.instruction = ""

    def display_text(self, index):
        """What the window shows for message ``index``: the short
        display text when one is set, else the full content."""
        return self.messages[index].display_text()

    def add_user(self, text, label="You", display=None, source_side=None):
        self.messages.append(
            ChatMessage("user", text, label, display, source_side))
        return label

    def add_assistant(self, text, label=None):
        label = label if label is not None else (self.model or "Assistant")
        self.messages.append(ChatMessage("assistant", text, label))
        return label

    def clear(self):
        self.messages = []

    def history_chars(self):
        """Total characters across this side's message contents."""
        return sum(len(m.content) for m in self.messages)

    def summarize(self, summary_text):
        """Fold the older turns into one summary entry.

        Keeps the last SUMMARY_KEEP_LAST messages verbatim; everything
        older is replaced by a single user message carrying the
        summary, labelled SUMMARY_LABEL in the window. Returns False
        (and changes nothing) when there is nothing older to fold or
        the summary is empty.
        """
        if not summary_text.strip():
            return False
        if len(self.messages) <= SUMMARY_KEEP_LAST:
            return False
        kept = self.messages[-SUMMARY_KEEP_LAST:]
        entry = ChatMessage(
            "user",
            "Earlier in this conversation: " + summary_text.strip(),
            SUMMARY_LABEL)
        self.messages = [entry] + kept
        return True

    def move_up(self, index):
        """Move message ``index`` one slot earlier. No-op at the edges."""
        if 0 < index < len(self.messages):
            self._swap(index, index - 1)

    def move_down(self, index):
        """Move message ``index`` one slot later. No-op at the edges."""
        if 0 <= index < len(self.messages) - 1:
            self._swap(index, index + 1)

    def _swap(self, i, j):
        self.messages[i], self.messages[j] = self.messages[j], self.messages[i]

    def delete_at(self, index):
        """Remove message ``index``. No-op when out of range."""
        if 0 <= index < len(self.messages):
            del self.messages[index]

    def messages_for_model(self):
        """The messages a generation sends: system prompt (when set)
        followed by this side's messages as role/content pairs, in
        their current order. A fresh list of fresh dicts each call,
        so the caller can never mutate the side's messages."""
        messages = [m.for_model() for m in self.messages]
        if self.instruction.strip():
            return [{"role": "system", "content": self.instruction}] + messages
        return messages


class Conversation:
    """Both sides. Histories are fully independent per side."""

    def __init__(self):
        self.sides = {"A": ChatSide("A"), "B": ChatSide("B")}

    def targets_for(self, action):
        """Return the ordered sides a send targets, or raise NoModelSelected.

        action is "A", "B" or "Both". "Both" always orders A first, then B.
        """
        wanted = ["A", "B"] if action == "Both" else [action]
        for side in wanted:
            if self.sides[side].model is None:
                raise NoModelSelected("Model " + side)
        return [self.sides[s] for s in wanted]


# Agreement stop: simple case-insensitive substring matching on this
# fixed list. No LLM judge -- the 2B models are slow enough already.
AGREEMENT_PHRASES = ("i agree", "agreed", "sounds good", "that works")
AWARE_TEMPLATE = ("You are talking with another AI assistant (Model %s). "
                  "It said: %s")
RELAY_CAP_MIN = 1
RELAY_CAP_MAX = 50
RELAY_CAP_DEFAULT = 10


class RelaySession:
    """One relay run: config, turn counting, framing, stop checks.

    Pure logic, no Qt -- the GUI drives it from the runner callbacks.
    A "turn" is one generation. The seed turn is turn 1, so a cap of N
    means exactly N generations before the relay halts.
    """

    def __init__(self, start_side, turn_cap=RELAY_CAP_DEFAULT, mode="blind",
                 stop_phrases=(), stop_on_agreement=False):
        self.start_side = start_side
        self.current_side = start_side  # side whose turn is in flight
        self.hop_from = "you"  # who the current turn is answering
        self.turn_cap = max(RELAY_CAP_MIN,
                            min(RELAY_CAP_MAX, int(turn_cap)))
        self.mode = mode  # "blind" or "aware"
        self.stop_phrases = list(stop_phrases)
        self.stop_on_agreement = stop_on_agreement
        self.turns_done = 0
        self.active = True
        self.stop_reason = None
        # The seed message, set by the window when the relay starts
        # (v0.53, P1-1): a skipped first hop hands it to the other
        # side, which answers it instead. ``seed`` is the full text
        # the first side received (attachments inline);
        # ``seed_display`` is the short form the window shows, or
        # None when there is nothing to shorten.
        self.seed = None
        self.seed_display = None

    @staticmethod
    def other(side):
        return "B" if side == "A" else "A"

    def frame(self, text, from_side):
        """The message the other model receives for a relayed reply.

        Blind: the bare reply text, no framing at all -- the model sees
        only a person talking to it. Aware: framed so the models know
        they are talking to another AI.
        """
        if self.mode == "aware":
            return AWARE_TEMPLATE % (from_side, text)
        return text

    def find_stop_phrase(self, text):
        """The configured stop phrase found in text, or None.

        Case-insensitive whole-word match: a phrase only counts when
        it is not glued to another word, so "cap" does not stop on
        "capital".
        """
        for phrase in self.stop_phrases:
            if phrase_matches(phrase, text):
                return phrase
        return None

    def find_agreement(self, text):
        """The agreement phrase found in text, or None.

        Only when stop-on-agreement is enabled. Case-insensitive
        whole-word match on the fixed AGREEMENT_PHRASES list, so
        "disagreed" never counts as "agreed".
        """
        if not self.stop_on_agreement:
            return None
        for phrase in AGREEMENT_PHRASES:
            if phrase_matches(phrase, text):
                return phrase
        return None

    def check_stop(self, reply_text):
        """Stop reason for this reply, or None to keep relaying.

        Call after incrementing turns_done. A phrase or agreement stop
        is reported before the cap when they land on the same turn,
        because it says more about why the relay ended.
        """
        phrase = self.find_stop_phrase(reply_text)
        if phrase is not None:
            return "phrase %r found" % phrase
        agreement = self.find_agreement(reply_text)
        if agreement is not None:
            return "agreement reached (%r)" % agreement
        if self.turns_done >= self.turn_cap:
            return "turn cap reached"
        return None

    def status_running(self):
        """Live status line, e.g. "Relay on: turn 3/10, A to B"."""
        return "Relay on: turn %d/%d, %s to %s" % (
            self.turns_done + 1, self.turn_cap,
            self.hop_from, self.current_side)


