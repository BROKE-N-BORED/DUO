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
"""

from dataclasses import dataclass


MSG_NO_MODEL = "Pick a model for {side} first."


SUMMARY_KEEP_LAST = 4


SUMMARY_LABEL = "Summary of earlier messages"


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
    """

    role: str
    content: str
    label: str
    display: str = None

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

    def add_user(self, text, label="You", display=None):
        self.messages.append(
            ChatMessage("user", text, label, display))
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
