"""Window constants for DUO: plain-language messages, activity-state
tables, QSettings keys, layout floors, temperature bounds, first-run
guide text, and field explanations.

Moved verbatim from dual_chat.py in v0.47 (redesign R9): the window
file was 2 lines under its 4,000-line bound, and the sparkline work
needed room. dual_chat.py re-exports every name here, so
`from dual_chat import ...` keeps working unchanged. No Qt at module
level (the link opener imports Qt lazily, as it always did), no I/O:
this module imports standalone.
"""

# Plain-language messages shown in the panes. No tracebacks in the UI.
MSG_STOPPED = "Stopped."
MSG_RELAY_NEEDS_BOTH = "Pick a model for both sides to start a relay."
MSG_RELAY_STOPPED_BY_YOU = "stopped by you"
MSG_PHONE_BUSY = "DUO is busy right now. Wait for the current turn to finish, then send again."
MSG_PHONE_RELAY = "A relay is running. Stop the relay before sending from your phone."
MSG_PHONE_TASK = "A task is running. Wait for it to finish, then send again."
# Update check (v0.28): the note when a newer Release is out
# ("DUO 0.29 is out ..."), what a manual check says when this copy
# is current, and what it says when the check itself failed. The
# automatic startup check only ever shows the first one -- when
# this copy is current or the check fails, it stays silent.
MSG_UPDATE_AVAILABLE = "DUO %s is out. Use Check for updates to get it."
MSG_UPDATE_DOWNLOADING = "Downloading DUO %s..."
MSG_UPDATE_PENDING = "The update will finish the next time DUO starts."
MSG_UPDATE_LATEST = "You're on the latest version."
MSG_UPDATE_FAILED = "Couldn't check for updates. Are you online?"
MSG_PYSIDE6_MISSING = (
    "DUO needs PySide6 to open its window, and it isn't installed.\n"
    "Run the installer first:\n"
    "\n"
    "    bash install.sh\n"
    "\n"
    "Then start DUO again with:\n"
    "\n"
    "    bash run.sh\n")

# Per-side activity states (v0.29): state name -> (light color,
# words). One setter (set_side_state, in build_window) sets the
# light and the words together, so they can never disagree. Grey
# is idle; amber is waiting -- queued behind the other side, a job
# started but no reply yet, or a relay hop parked for review;
# green is working -- a reply streaming, or a summarize turn; red
# is a failed turn, held a few seconds so it is seen, then idle.
SIDE_STATES = {
    "idle": ("#8a8a8a", "Idle"),
    "waiting": ("#e8a33d", "Waiting for Ollama…"),
    "thinking": ("#2fbf5a", "Thinking…"),
    "summarizing": ("#2fbf5a", "Summarizing…"),
    "queued": ("#e8a33d", "Queued…"),
    "review": ("#e8a33d", "Waiting for you to review…"),
    "error": ("#d64545", "Something went wrong"),
}
# How long the red light holds after a failed turn (ms) before the
# side goes back to idle. A new job starting first overrides it.
ERROR_STATE_HOLD_MS = 5000
# Relay chip (v0.34, redesign R3): the relay's status dot and words.
# The same palette as the activity lights, so the colors mean the
# same thing everywhere: grey idle, green working, amber waiting on
# you. The text is None where it comes from the relay session --
# the running turn counts, the review pause, and the end reason.
RELAY_CHIP_STATES = {
    "idle": ("#8a8a8a", "Relay off"),
    "running": ("#2fbf5a", None),
    "review": ("#e8a33d", None),
    "ended": ("#8a8a8a", None),
}
# System meter (v0.29): how often the status bar re-reads /proc.
METER_REFRESH_MS = 1000

# Persisted preferences (QSettings): the "Enter to send" tickbox is
# remembered between runs. Explicit org/app names so the setting
# lands in the same place no matter how the app was launched.
SETTINGS_ORG = "BrokeNBored"
SETTINGS_APP = "DUO"
SETTING_ENTER_TO_SEND = "composer/enter_to_send"
# Director bar (v0.31): whether the director area below the panes
# is collapsed is remembered the same way.
SETTING_DIRECTOR_COLLAPSED = "director/collapsed"
# Director sections (v0.37, R4): each section's collapsed state is
# remembered the same way. %s is the section key ("relay", "pairs",
# "tasks"). Relay starts expanded, Pairs and Tasks start collapsed.
SETTING_SECTION_COLLAPSED = "director/%s_collapsed"
# Window finish (v0.15): the window's size/position and the pane
# split are remembered the same way, saved when the window closes.
SETTING_WINDOW_GEOMETRY = "window/geometry"
SETTING_SPLITTER_STATE = "window/splitter"
# History editor (v0.38, T20): each pane's transcript/history split
# is remembered the same way. %s is the side ("A"/"B").
SETTING_HIST_SPLITTER_STATE = "history_splitter/%s"
# History editor floors (v0.38, T20): the divider can never crush
# the transcript below 100 px or the editor below 60 px; the editor
# opens at 110 px, the height it had when it was fixed.
HIST_TRANSCRIPT_MIN_HEIGHT = 100
HIST_EDITOR_MIN_HEIGHT = 60
HIST_EDITOR_DEFAULT_HEIGHT = 110
# Model parameters (v0.19): each side's temperature is remembered
# per side the same way. %s is the side ("A"/"B").
SETTING_TEMPERATURE = "temperature/%s"
TEMPERATURE_DEFAULT = 0.7  # Ollama's own default, made visible.
TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0
# First-run guide (v0.39, redesign R5): the flag remembers that the
# user has used the app (first send, relay, task, opened
# conversation, or import). While it is unset and both panes are
# empty, each transcript shows the guide instead of "No messages
# yet." -- three steps that teach the loop. A step ticks only when
# honestly done (library entry 020): step 1 ticks once both models
# are picked.
SETTING_FIRST_RUN_DONE = "first_run/done"
FIRST_RUN_HEADER = "New here? Here's the loop:"
FIRST_RUN_STEP1 = "Pick a model for each side above."
FIRST_RUN_STEP2 = "Type a message below and send it to both."
FIRST_RUN_STEP3 = "Start a relay and watch them talk."
FIRST_RUN_TICK = "\u2713"  # a checkmark, not a bullet

# Field explanations (v0.42, redesign U20): every field that needs
# one explains itself in plain visible words -- beside the field
# where its row has empty space, underneath where the layout has
# room (inside sections that start closed). Beside, not underneath,
# everywhere: the default 1100x700 window is full (the v0.39
# geometry probe: an 87 px transcript slot against a 76 px
# first-run guide), and a line under every field would clip the
# guide. The model picker needs none (its placeholder + the guided
# size/fit entries + its position already say what it is for),
# and neither do the composer, the search boxes, or the pair and
# preset name fields (their placeholders cover them).
EXPLAIN_TEMP = "Lower is more focused, higher is more creative."
EXPLAIN_RELAY_ROW1 = ("Start with picks which side answers first. "
                      "Turns caps the relay. "
                      "Blind hides that the other side is an AI.")
EXPLAIN_STOP = "A reply containing one of these stops the relay."
EXPLAIN_INSTR = "Sent to this side's model with every message."
EXPLAIN_TASK = "Describe the work, then press Run Task."
EXPLAIN_TASK_ROW = ("Worker picks which side does the task. "
                    "Follow-up picks what the other side does with "
                    "the result. Rounds sets how many times the work "
                    "passes between the sides.")
EXPLAIN_STYLE = "font-size: 11px; color: #6b6b6b;"


def first_run_guide_html(models_picked):
    """The first-run guide as HTML: a bold header and three steps.

    Step 1 shows a checkmark instead of its number once both models
    are picked. Read back as plain text, the document is exactly the
    four lines: header, then the three steps."""
    step1 = ("%s %s" % (FIRST_RUN_TICK, FIRST_RUN_STEP1)
             if models_picked else "1. %s" % FIRST_RUN_STEP1)
    return ("<b>%s</b><br>%s<br>2. %s<br>3. %s"
            % (FIRST_RUN_HEADER, step1, FIRST_RUN_STEP2, FIRST_RUN_STEP3))


# Chat reliability (v0.10): Code Forge waits 1800s on a chat call and

# Summarize (Item 4): when a side's history passes this many characters
# the pane nudges the user to summarize. Summarizing keeps the last
# SUMMARY_KEEP_LAST entries verbatim and replaces everything older
# with one summary entry, written by that side's own model.
HISTORY_BUDGET_CHARS = 12000
SUMMARIZE_PROMPT = (
    "Summarize our conversation so far in a few short sentences, in "
    "the third person, covering the main topics and any decisions. "
    "Keep it brief -- it will replace the earlier messages as context.")

# Compare mode: the picked reply is sent to the other side as a user
# message with this prefix, so the conversation continues from it.
COMPARE_PREFIX = "You picked this answer: %s"

# Tasks (Item 6): rounds of delegation. The worker gets the bare
MSG_TASK_EMPTY = "Describe the task first."
MSG_TASK_NEEDS_WORKER = "Pick which model does the task."
MSG_TASK_NEEDS_FOLLOW_UP = "Pick what the other model does with the result."

MSG_PAIR_NEEDS_NAME = "Name the pair first."
MSG_PRESET_NEEDS_NAME = "Name the preset first."
MSG_REVIEW_EMPTY = "Nothing to forward -- edit the text or skip this hop."


def _open_link_in_browser(url):
    """Open a link clicked in a transcript pane in the default
    browser. The import is lazy: Qt lives in build_window, not at
    module level."""
    from PySide6 import QtGui
    QtGui.QDesktopServices.openUrl(url)
