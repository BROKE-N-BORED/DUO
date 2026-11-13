"""The conversation model: one side's state (ChatSide) and both
sides together (Conversation), plus the summarize constants
and the no-model-picked error.

Split out of dual_chat.py in v0.25 (audit T14). No Qt, no I/O:
duo_storage builds Conversations from saved files, and the GUI
holds the live one.
"""


MSG_NO_MODEL = "Pick a model for {side} first."


SUMMARY_KEEP_LAST = 4


SUMMARY_LABEL = "Summary of earlier messages"


class NoModelSelected(Exception):
    """Raised when a send targets a side with no model picked."""

    def __init__(self, side):
        super().__init__(MSG_NO_MODEL.format(side=side))
        self.side = side


class ChatSide:
    """One side's state: its picked model and its own history.

    ``labels`` runs parallel to ``history``: the display label for each
    entry ("You", "You [relay from Model A]", or the model name). Labels
    are window-only -- they never reach the model. ``instruction`` is
    this side's system prompt ("" means no system message).
    ``display`` also runs parallel to ``history``: an optional shorter
    text shown in the window instead of the full entry content (used
    when a message carries attached files, so the transcript stays
    readable). None means show the content as-is.
    """

    def __init__(self, name):
        self.name = name  # "A" or "B"
        self.model = None
        self.history = []  # [{"role": "user"/"assistant", "content": str}]
        self.labels = []
        self.display = []
        self.instruction = ""

    def display_text(self, index):
        """What the window shows for entry ``index``: the short display
        text when one is set, else the full content."""
        shown = self.display[index]
        return shown if shown is not None else self.history[index]["content"]

    def add_user(self, text, label="You", display=None):
        self.history.append({"role": "user", "content": text})
        self.labels.append(label)
        self.display.append(display)
        return label

    def add_assistant(self, text, label=None):
        self.history.append({"role": "assistant", "content": text})
        label = label if label is not None else (self.model or "Assistant")
        self.labels.append(label)
        self.display.append(None)
        return label

    def clear(self):
        self.history = []
        self.labels = []
        self.display = []

    def history_chars(self):
        """Total characters across this side's history contents."""
        return sum(len(m["content"]) for m in self.history)

    def summarize(self, summary_text):
        """Fold the older turns into one summary entry.

        Keeps the last SUMMARY_KEEP_LAST entries verbatim; everything
        older is replaced by a single user entry carrying the summary,
        labelled SUMMARY_LABEL in the window. Returns False (and
        changes nothing) when there is nothing older to fold or the
        summary is empty.
        """
        if not summary_text.strip():
            return False
        if len(self.history) <= SUMMARY_KEEP_LAST:
            return False
        kept = self.history[-SUMMARY_KEEP_LAST:]
        kept_labels = self.labels[-SUMMARY_KEEP_LAST:]
        kept_display = self.display[-SUMMARY_KEEP_LAST:]
        entry = {"role": "user",
                 "content": "Earlier in this conversation: "
                            + summary_text.strip()}
        self.history = [entry] + kept
        self.labels = [SUMMARY_LABEL] + kept_labels
        self.display = [None] + kept_display
        return True

    def move_up(self, index):
        """Move entry ``index`` one slot earlier. No-op at the edges."""
        if 0 < index < len(self.history):
            self._swap(index, index - 1)

    def move_down(self, index):
        """Move entry ``index`` one slot later. No-op at the edges."""
        if 0 <= index < len(self.history) - 1:
            self._swap(index, index + 1)

    def _swap(self, i, j):
        self.history[i], self.history[j] = self.history[j], self.history[i]
        self.labels[i], self.labels[j] = self.labels[j], self.labels[i]
        self.display[i], self.display[j] = self.display[j], self.display[i]

    def delete_at(self, index):
        """Remove entry ``index``. No-op when out of range."""
        if 0 <= index < len(self.history):
            del self.history[index]
            del self.labels[index]
            del self.display[index]

    def messages_for_model(self):
        """The messages a generation sends: system prompt (when set)
        followed by this side's history, in its current order."""
        messages = list(self.history)
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
