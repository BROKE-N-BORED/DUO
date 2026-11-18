#!/usr/bin/env python3
"""DUO 0.29 - two Ollama models side by side in one desktop window.

Item 1: the shell, the model pickers, and send-to-A/B/both.
Item 2: the auto-relay AI-to-AI loop. The user types a seed message, picks
  the starting side, and presses Start Relay. Each reply is handed to the
  other model, one generation at a time, until the turn cap, the Stop
  button, or a stop condition. Blind mode (default) delivers the bare
  reply text as a plain user message, so each model sees only a person
  talking to it. Aware mode frames the relay so the models know they are
  talking to another AI. Relayed hops are tagged in the window's
  transcripts only -- the models never see the tag.
Item 3: director controls. Reorder or delete any message in a side's
  history (the model sees the edited order on the next turn); review
  each relay hop before it is forwarded, editing it if you like;
  per-side instructions (system prompts) plus saved model pairs; and
  compare mode -- after Send to Both, pick the better reply and the
  conversation continues from it.
Item 4: long-loop support. Each pane gets a Summarize button: that
  side's own model summarizes the conversation so far, and the older
  turns are replaced by that summary (kept as a visible "Summary of
  earlier messages" entry) while the last 4 entries stay verbatim.
  A budget nudge appears when a side's history passes ~12,000
  characters -- it never calls the model on its own. And each pane
  shows a stats line for the last completed turn: tokens/sec, time
  to first token, total turn time, and the model's RAM from
  Ollama /api/ps.
Item 5: file upload. Attach up to 5 text files (100 KB each) to a
  message: their contents ride along with the typed message as plain
  text blocks the model can read. The window shows a short
  "Attached: ..." tag instead of the full contents. Binary files are
  skipped with a plain note. Attachments are one-shot -- they clear
  after a send, and a relay only carries them on the seed message.
Item 6: tasks. Hand a job to one model: it answers, and then the
  work passes back and forth for as many rounds as you pick -- the
  other model reviews the result or continues it, and a review
  round hands a review back to the worker to revise from. Or
  nothing happens after the first round. The task shows as
  "You [task]" in the worker's pane and each handoff as
  "You [task handoff]" in the receiving pane; the tags never
  reach the models.
Item 7: transcripts. Save one side's conversation, or both sides in
  one file, as plain text that reads like the window: a header line
  naming DUO, the model, and when the file was saved, then each
  message in order with its labels, one blank line between messages.
  Attached file contents are never pasted in -- the short tag is all
  the file carries. Saving is idle-only: never mid-generation or
  mid-relay, when the transcript is moving.
Item 8: final pass. The status-line nit (a stale "Attached: ..." note
  after a send) fixed, and the README rewritten for the complete app.
Item 9: chat layout (blank line between messages, bold sender
  labels, explicit word wrap) and the full transcript export: both
  sides interleaved in the order the conversation actually happened.
v0.10: Ollama reliability. Chat turns retry transient failures with
  a pause between attempts and wait as long as Code Forge does; a
  Start Ollama button starts the server from inside DUO when it is
  down; and a debug log layer (the BNB T21 standard, vendored as
  debug_log.py) records events and writes a report to email when
  something breaks. The log never holds message or file contents.
v0.11: the phone deck. DUO serves a small local web page on the PC
  (the one place a web page is wanted -- Joshua ordered it): a QR
  code in the window opens it on his phone, the page follows
  whichever model is thinking, and he can send messages from the
  phone and export the full transcript with one button. LAN-only,
  no accounts, no internet.
v0.12: the agreement matcher fixed. Stop phrases and agreement
  phrases now match whole words only -- "I disagreed" no longer
  stops a relay that was waiting for "agreed".
v0.13: keyboard send and the composer cap gone. Ctrl+Enter sends to
  both models, and an "Enter to send" tickbox (remembered between
  runs) makes plain Enter send too, with Shift+Enter still adding a
  new line. The composer no longer drops pasted paragraphs -- the
  5-block cap is removed entirely.
v0.14: the first-run path guarded. If PySide6 is missing, main()
  says so in plain language (stderr, plus a small dialog when
  tkinter is available) and exits cleanly -- never a raw traceback.
  install.sh pins PySide6-Essentials and checks the install before
  calling it done; run.sh no longer falls back to system Python.
v0.15: the window finished. Its size, position, and the pane split
  are remembered between runs (clamped to the screen, so a stale
  restore can never strand it off-screen); it has a window icon,
  drawn in code; the title shows the version; and saved pairs can
  be deleted, with a confirmation first.
v0.16: the front door. A CHANGELOG and .gitignore, a CI workflow
  that runs the suite on every push, a .desktop launcher the
  installer puts in the app menu (with an icon drawn from the
  window icon), and an uninstaller that asks first. The license is
  still to be decided.
v0.17: conversation persistence. Save conversation writes the whole
  conversation -- both sides' histories with their labels, both
  models, both instruction fields, and the interleaved full-transcript
  log -- to a JSON file, atomically, and Open conversation loads it
  back exactly. A saved model that isn't installed anymore stays
  unpicked, with a plain note naming it. Opening replaces the
  conversation on screen, so it asks first.
v0.18: atomic writes everywhere. The pairs file and all three
  transcript saves now go through the same write-to-temp-then-rename
  step the conversation save already used, so a crash or a failed
  write can never leave a half-written file behind -- the old file
  stays intact, and a failed save says so plainly instead of
  crashing.
v0.19: temperature controls. Each pane has a Temperature control
  (0.0-2.0, default 0.7, which is Ollama's own default -- made
  visible instead of silent). The value travels with every turn
  that side generates, as Ollama's options, and is remembered per
  side between runs.
v0.20: token counts. The stats line shows how many tokens the last
  turn used -- generated and prompt, straight from Ollama's done
  chunk, never estimated -- and each pane keeps a running session
  total underneath. A failed turn shows n/a and adds nothing;
  Clear never resets the session total.
v0.21: relay presets. The whole relay row -- start side, mode, turn
  cap, stop phrases, stop-on-agreement, review-each-hop -- can be
  saved under a name and loaded back from a dropdown, the same way
  saved model pairs work. Presets live in duo-relay-presets.json
  next to the app, written atomically; nothing loads by itself.
v0.22: multi-round tasks. A task runs for as many rounds as the
  Rounds box says (1-10, default 1): each round passes the work to
  the other side, alternating. "Continue it" keeps continuing; a
  review round hands the review back to the worker to revise from.
  "Nothing" is always one round. The status bar shows which round
  a chain is on, and Stop still ends it.
v0.25: the monolith split (audit T14). The code that used to live
  in this one file (5,165 lines) is split into focused sibling
  modules -- duo_ollama, duo_stats, duo_text, duo_tasks,
  duo_markdown, duo_model, duo_transcript, duo_storage,
  duo_runner, duo_icon -- and this file keeps the window, the
  phone deck, and the entry point, re-exporting the moved names
  so imports through here keep working. Nothing you can see
  changed: same window, same features, same files.
v0.26: one message structure (audit T15). Each side used to keep
  three separate lists -- message contents, sender labels, short
  display texts -- that had to stay in step by hand. Each message
  is now one object holding its role, content, label, and display
  text together, so a label can never land on the wrong message.
  Same window, same features, same files -- and DUO is now
  MIT-licensed (see LICENSE).
v0.27: the full transcript log is bounded (audit T16). It used to
  grow without limit across a long session; it now keeps the most
  recent 2,000 entries, oldest first out, and the saved full
  transcript says so at the top when a session passes it.
v0.28: the update story (audit T17). DUO checks GitHub once at
  startup, on a background thread, and says in the status bar when
  a newer Release is out -- silently saying nothing when offline.
  A "Check for updates" button runs the same check on demand and
  reports either way. DUO never downloads or installs anything;
  the user downloads from GitHub themselves.
v0.29: activity lights and a system meter. Each pane has a small
  colored light and small words saying what that side is doing
  right now -- grey Idle, amber while it waits (queued, waiting
  for Ollama to answer, or a relay hop waiting for review), green
  while it thinks or summarizes, red for a few seconds when a
  turn fails. And the status bar shows the whole machine's CPU %
  and RAM, read from /proc once a second (stdlib only, no new
  dependency); on a machine without /proc the meter stays empty
  and silent.
v0.30: a menu bar and keyboard shortcuts (audit T18). File, Relay,
  and Help menus across the top, every item calling the same
  handler as its button -- Ctrl+S saves the conversation, Ctrl+O
  opens one, Ctrl+Shift+S saves the full transcript, Ctrl+Q quits.
  Help holds Check for updates, an About box, and a built-in How
  to Use index. The menu lives in its own module (duo_menu.py) so
  the window file stays under its 4,000-line bound.
v0.31: the director bar collapses (audit T19). One "Director"
  toggle on the boundary between the panes and the director rows
  hides the controls so the panes take the whole window; the
  toggle stays visible either way, and the choice is remembered
  between runs. Collapsing never touches running work. The phone
  deck moved to its own module (duo_deck.py) to make room: the
  window file was 16 lines under its 4,000-line bound.
v0.55: the update story grows up (standing order 2026-11-17).
  When a newer Release is out, a popup asks whether to update;
  on the user's tap DUO pulls it, checks the zip against the
  release's checksum before installing anything, and asks
  whether to restart now to finish -- Yes restarts DUO on its
  own, No leaves the update staged for the next launch.
v0.56: the window gets room to breathe, and room to grow.
  The saved-pair and relay-preset handlers move to duo_pairs.py
  (P1-12), which buys back 200 lines under the bound. The relay
  button greys out on an empty composer (P1-3), history delete
  and closing with an unsaved conversation ask first (P1-4,
  P1-5), the panes keep a guaranteed minimum so transcripts stop
  squeezing (P1-6), the send row loses the four buttons the File
  menu already has (P1-7), and the director scrolls instead of
  running off the bottom of a short window (P1-8).
"""

import html
import os
import re
import shutil  # noqa: F401 -- unused here, but the tests patch
# dual_chat.shutil.which (the Ollama fallback lookup lives in
# duo_ollama; shutil is one shared module, so the patch lands).
import sys
import threading
import time

# The code used to live in this one file (5,165 lines). v0.25
# (audit T14) splits it into focused sibling modules; this file
# keeps the window and the entry point (the phone deck moved to
# duo_deck.py in v0.31, the background Qt workers to duo_workers.py
# in v0.39, the relay session to duo_model.py in v0.40, the debug
# log fallback to duo_debug.py and the attachments to
# duo_attachments.py in v0.42, the single-instance socket to
# duo_single.py in v0.44, and the window constants to
# duo_constants.py in v0.47), and
# re-exports the moved names so
# `from dual_chat import ...` keeps working unchanged -- the
# tests, the installer, and the deck all import through here.
# build_window stays a closure in this file: splitting the
# window itself into a class is a bigger redesign and not this
# task.
from duo_accessibility import (
    explanation_style, install_accessibility, small_label_style,
    title_style,)
from duo_attachments import (
    MAX_ATTACHMENTS, MAX_ATTACHMENT_BYTES, AttachmentError,
    attachment_tag, build_attached_message, load_attachment,)
from duo_constants import (
    MSG_STOPPED, MSG_RELAY_NEEDS_BOTH, MSG_RELAY_STOPPED_BY_YOU,
    MSG_PHONE_BUSY, MSG_PHONE_RELAY, MSG_PHONE_TASK,
    MSG_UPDATE_AVAILABLE, MSG_UPDATE_LATEST, MSG_UPDATE_FAILED,
    MSG_UPDATE_DOWNLOADING, MSG_UPDATE_PENDING,
    MSG_PYSIDE6_MISSING, SIDE_STATES, ERROR_STATE_HOLD_MS,
    RELAY_CHIP_STATES, METER_REFRESH_MS, SETTINGS_ORG, SETTINGS_APP,
    SETTING_ENTER_TO_SEND, SETTING_DIRECTOR_COLLAPSED,
    SETTING_SECTION_COLLAPSED, SETTING_WINDOW_GEOMETRY,
    SETTING_SPLITTER_STATE, SETTING_HIST_SPLITTER_STATE,
    HIST_TRANSCRIPT_MIN_HEIGHT, HIST_EDITOR_MIN_HEIGHT,
    PANES_MIN_HEIGHT, DIRECTOR_SCROLL_MAX_HEIGHT,
    HIST_EDITOR_DEFAULT_HEIGHT, SETTING_TEMPERATURE,
    TEMPERATURE_DEFAULT, TEMPERATURE_MIN, TEMPERATURE_MAX,
    SETTING_FIRST_RUN_DONE, FIRST_RUN_HEADER, FIRST_RUN_STEP1,
    FIRST_RUN_STEP2, FIRST_RUN_STEP3, FIRST_RUN_TICK, EXPLAIN_TEMP,
    EXPLAIN_RELAY_ROW1, EXPLAIN_STOP, EXPLAIN_INSTR, EXPLAIN_TASK,
    EXPLAIN_TASK_ROW, EXPLAIN_STYLE, first_run_guide_html,
    HISTORY_BUDGET_CHARS, SUMMARIZE_PROMPT, COMPARE_PREFIX,
    MSG_TASK_EMPTY, MSG_TASK_NEEDS_WORKER, MSG_TASK_NEEDS_FOLLOW_UP,
    MSG_REVIEW_EMPTY,
    _open_link_in_browser,
)
from duo_debug import _NullLogger, _debug_log, make_debug_logger
from duo_deck import (
    DECK_PAGE_HTML, DECK_PORT, DECK_PORT_ENV, DECK_PORT_TRIES,
    MSG_EMPTY, MSG_PHONE_TARGET, DeckServer, build_deck_state,
    get_lan_ip, render_qr_pixmap,)
from duo_icon import make_app_icon
from duo_markdown import markdown_to_html
from duo_menu import build_menu_bar
from duo_model import (
    AGREEMENT_PHRASES, AWARE_TEMPLATE, RELAY_CAP_DEFAULT, RELAY_CAP_MAX,
    RELAY_CAP_MIN, SIDE_COLORS, ChatMessage, ChatSide, Conversation,
    MSG_NO_MODEL, NoModelSelected, RelaySession, SUMMARY_KEEP_LAST,
    SUMMARY_LABEL,)
from duo_motion import DirectorSpring, install_motion, theme_colors
from duo_ollama import (
    CHAT_MAX_ATTEMPTS, CHAT_RETRY_DELAYS, CHAT_TIMEOUT, DEFAULT_OLLAMA_URL,
    MSG_MODEL_FAILED, MSG_OLLAMA_DOWN, MSG_OLLAMA_SLOW, OLLAMA_URL_ENV,
    UPDATE_CHECK_TIMEOUT, UPDATE_CHECK_URL, UPDATE_CHECK_URL_ENV, Cancelled,
    OllamaClient, OllamaError, check_for_update,
    parse_ollama_list_output, parse_ps_payload, parse_tags_payload,
    parse_tags_sizes, parse_version,)
from duo_pairs import PairPresetController
from duo_runner import SerialRunner
from duo_single import (SINGLE_INSTANCE_ENV, SINGLE_INSTANCE_NAME,
                        SingleInstance, notify_existing,
                        single_instance_name,)
from duo_sparkline import (SPARKLINE_SAMPLE_MS, Sparkline, live_rate,)
from duo_stats import (
    MODEL_FIT_RESERVE_BYTES, STATS_EMPTY_LINE, compute_turn_stats,
    cpu_percent_between, format_model_entry, format_ram,
    format_session_line, format_stats_line, format_system_meter,
    model_fits, parse_cpu_times, parse_meminfo, read_system_meter,
    read_total_ram,)
from duo_storage import (
    CONVERSATION_FORMAT_VERSION, ConversationError, conversation_to_data,
    data_to_conversation, load_conversation_file,
    save_conversation_file, write_text_atomic,)
from duo_tasks import (
    TASK_CONTINUE_TEMPLATE, TASK_FOLLOW_UPS, TASK_REVIEW_TEMPLATE,
    TASK_REVISE_TEMPLATE, TASK_ROUNDS_DEFAULT, TASK_ROUNDS_MAX,
    TASK_ROUNDS_MIN, TaskSession,)
from duo_text import parse_stop_phrases, phrase_matches
from duo_transcript import (
    FULL_LOG_MAX_ENTRIES, FULL_LOG_TRIMMED_NOTE, TranscriptError,
    append_full_log_entry, default_both_filename,
    default_conversation_filename, default_debug_filename,
    default_full_filename, default_side_filename,
    format_both_transcript, format_full_transcript, format_side_transcript,
    load_side_transcript_file, parse_side_transcript, sanitize_filename_part,)
from duo_updater import (apply_pending_update, download_and_stage,
                         install_update_wiring, offer_update,
                         restart_quietly,)
from duo_workers import (_ModelLoader, _SignalObject,
                         _Signals, _UpdateChecker, _qt,)


APP_NAME = "DUO"
APP_VERSION = "0.56"

# P1-5 (v0.56): closing with an unsaved conversation asks first.
# The GUI tests close windows with messages in every tearDown, so
# the test module turns the ask off globally (setUpModule) and the
# close-confirm tests turn it back on around their checks.
CLOSE_CONFIRM_ENABLED = True


def close_needs_confirm(convo, state):
    """True when closing would lose something: either pane has
    messages, or work is in flight (a generation, a relay, or a
    task). There is no autosave -- Save conversation is the only
    persistence -- so any of these is worth asking about."""
    has_messages = (bool(convo.sides["A"].messages)
                    or bool(convo.sides["B"].messages))
    work_in_flight = (state["busy"] or state.get("relay") is not None
                      or state.get("task") is not None)
    return has_messages or work_in_flight



# ---------------------------------------------------------------------------
# Qt GUI
# ---------------------------------------------------------------------------

# The background Qt workers (the signal bridge and the loader /
# starter / update-checker threads) live in duo_workers.py, moved
# verbatim in v0.39; they are imported at the top of this file.




class MainWindow:
    """Built by build_window(); kept import-light so tests can skip Qt."""

    pass


def build_window(client=None, logger=None, deck_port=None):
    """Create the main window. Returns (window, app_state_dict).

    ``logger`` is a DebugLogger (tests pass one writing to a temp
    dir); without one, a logger is created with its log file next
    to the app (duo-debug.log). ``deck_port`` overrides the phone
    deck's port (0 means any free port -- used by the tests)."""
    from PySide6 import QtCore, QtGui, QtWidgets
    import shiboken6

    client = client or OllamaClient()
    if logger is None:
        logger = make_debug_logger()
    # The debug log records events and timings only -- never message
    # or attached-file contents. That rule is load-bearing: the report
    # is meant to be emailed, so nothing private may reach the log.
    ollama_url = getattr(client, "base_url", DEFAULT_OLLAMA_URL)
    logger.set_setting("ollama_url", ollama_url)
    if _debug_log is not None:
        logger.register_service(
            "ollama",
            lambda: _debug_log.check_ollama(ollama_url))
    logger.register_dependency("PySide6-Essentials")
    logger.action("DUO %s started" % APP_VERSION)
    # Theme colors (v0.51): the code-block background and the search
    # highlight colors follow the actual palette, read once at build
    # -- on a dark theme the old hardcoded light colors made white
    # text invisible. The focus tint is read the same way by
    # install_motion. A theme change mid-run needs a restart.
    theme = theme_colors()
    signals = _Signals()
    convo = Conversation()

    window = QtWidgets.QMainWindow()
    window.setWindowTitle("%s %s" % (APP_NAME, APP_VERSION))
    window.setWindowIcon(make_app_icon())
    # Default height 700 (v0.39, R5): at 680 the panes only got 135 px
    # for a 225 px transcript block, so the transcript's 100 px floor
    # painted underneath the search row -- the first-run guide's
    # steps 2-3 (and the bottom line of real replies) hid behind it.
    # 700 is the tallest the screen-fit test allows: 700 plus the
    # window's title bar plus the Mint panel still fits 1366x768.
    window.resize(1100, 700)
    window.setMinimumSize(900, 540)

    def alive():
        # Queued runner signals can land after the window is gone (e.g.
        # closing right as a relay stops). Never touch widgets then.
        return shiboken6.isValid(window)

    def _make_explanation(text, wrap=True):
        """A field explanation label (v0.42, U20): 11 px grey text in
        plain words, beside or under the field it explains. Wraps by
        default; a label that must stay on one line (the stop-phrase
        explanation, which shares its row with the field) passes
        wrap=False, and the field absorbs the squeeze."""
        label = QtWidgets.QLabel(text)
        label.setStyleSheet(EXPLAIN_STYLE)
        label.setWordWrap(wrap)
        return label

    central = QtWidgets.QWidget()
    window.setCentralWidget(central)
    root = QtWidgets.QVBoxLayout(central)
    # 4 px margins and spacing (v0.39, R5): 8 px left the panes too
    # short for the transcript block at the default window size
    # (see the resize note below).
    root.setContentsMargins(4, 4, 4, 4)
    root.setSpacing(4)

    panes = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
    # P1-6 (v0.56): the panes get a guaranteed minimum height, so
    # the transcript keeps its 100 px floor even with the director
    # fully expanded at the minimum window height. The director's
    # scroll area (below) absorbs the squeeze instead.
    panes.setMinimumHeight(PANES_MIN_HEIGHT)
    root.addWidget(panes, stretch=1)

    # Persisted preferences (QSettings): created here, before the
    # panes, because the panes read their saved temperatures as they
    # are built. Explicit org/app names so the settings land in the
    # same place no matter how the app was launched.
    prefs = QtCore.QSettings(SETTINGS_ORG, SETTINGS_APP)

    # --- one pane per side -----------------------------------------------
    widgets = {}  # side -> dict of widgets

    class _LinkClickFilter(QtCore.QObject):
        """Makes links in a read-only transcript clickable. QTextEdit
        has no anchorClicked signal in this Qt build (that is
        QTextBrowser-only), so clicks on the viewport are caught
        here: a left-click on a link opens it in the default
        browser, and hovering a link shows the pointing hand."""
        def __init__(self, transcript):
            super().__init__(transcript)
            self._transcript = transcript

        def eventFilter(self, obj, event):
            if event.type() == QtCore.QEvent.MouseButtonPress:
                if event.button() == QtCore.Qt.LeftButton:
                    href = self._transcript.anchorAt(
                        event.position().toPoint())
                    if href:
                        _open_link_in_browser(QtCore.QUrl(href))
                        return True
            elif event.type() == QtCore.QEvent.MouseMove:
                href = self._transcript.anchorAt(
                    event.position().toPoint())
                self._transcript.viewport().setCursor(
                    QtCore.Qt.PointingHandCursor if href
                    else QtCore.Qt.ArrowCursor)
            return super().eventFilter(obj, event)

    def make_pane(side):
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        layout = QtWidgets.QVBoxLayout(frame)
        # 3 px margins and spacing (v0.39, R5): 6 px left the panes
        # too short for the transcript block at the default window
        # size (see the resize note in build_window).
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)

        title_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Model " + side)
        # Pane identity (v0.40, T25): the title carries the side's
        # color -- blue A, purple B -- so the panes are
        # distinguishable at a glance. The words stay: color
        # reinforces the label, never replaces it.
        title.setStyleSheet("font-weight: bold; font-size: 14px; "
                            "color: %s;" % SIDE_COLORS[side])
        title_row.addWidget(title, stretch=1)
        instr_toggle = QtWidgets.QPushButton("Instructions ▸")
        instr_toggle.setCheckable(True)
        instr_toggle.setFlat(True)
        title_row.addWidget(instr_toggle)
        layout.addLayout(title_row)

        picker_row = QtWidgets.QHBoxLayout()
        picker = QtWidgets.QComboBox()
        picker.setPlaceholderText("Pick a model")
        picker.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        picker.setMinimumContentsLength(20)
        picker_row.addWidget(picker, stretch=1)
        refresh = QtWidgets.QPushButton("Refresh")
        picker_row.addWidget(refresh)
        layout.addLayout(picker_row)

        # Temperature (v0.19): per side, 0.0-2.0, default 0.7 -- that
        # is Ollama's own default, made visible instead of silent.
        # Remembered per side between runs (QSettings).
        temp_row = QtWidgets.QHBoxLayout()
        temp_label = QtWidgets.QLabel("Temperature")
        temp_row.addWidget(temp_label)
        temp_spin = QtWidgets.QDoubleSpinBox()
        temp_spin.setRange(TEMPERATURE_MIN, TEMPERATURE_MAX)
        temp_spin.setSingleStep(0.1)
        temp_spin.setDecimals(1)
        temp_spin.setToolTip(
            "0.7 is Ollama's default. Lower is more focused, "
            "higher is more random.")
        temp_spin.setValue(prefs.value(
            SETTING_TEMPERATURE % side, TEMPERATURE_DEFAULT,
            type=float))

        def _save_temperature(value):
            prefs.setValue(SETTING_TEMPERATURE % side, float(value))
            prefs.sync()

        temp_spin.valueChanged.connect(_save_temperature)
        temp_row.addWidget(temp_spin)
        # Field explanation (v0.42, U20): beside the spinbox, in
        # the row's empty space -- the row is wide and the control
        # is narrow, so it costs no height. One line, not wrapped:
        # a word-wrapped label's size hint wraps the sentence at
        # ~198 px and the row grows to two lines tall, while the
        # unwrapped sentence is 244 px and fits the row even at
        # the 900 px minimum width (probed, not assumed).
        temp_explain = _make_explanation(EXPLAIN_TEMP, wrap=False)
        temp_row.addWidget(temp_explain)
        temp_row.addStretch(1)
        layout.addLayout(temp_row)

        # Per-side instructions (system prompt). Collapsed by default;
        # empty means no system message is sent for this side.
        instr_edit = QtWidgets.QPlainTextEdit()
        instr_edit.setPlaceholderText(
            "Instructions for this model (system prompt)...")
        instr_edit.setFixedHeight(44)
        instr_edit.setVisible(False)
        layout.addWidget(instr_edit)
        # Field explanation (v0.42, U20): under the editor, inside
        # the collapsible -- hidden with it, so zero cost while
        # the editor is closed. Toggled with the editor in
        # make_instr_toggle, below.
        instr_explain = _make_explanation(EXPLAIN_INSTR)
        instr_explain.setVisible(False)
        layout.addWidget(instr_explain)

        # Transcript block (v0.38, T20): the transcript and its
        # rows, as the top half of the history splitter -- the
        # divider resizes them against the history editor.
        top_frame = QtWidgets.QFrame()
        top_layout = QtWidgets.QVBoxLayout(top_frame)
        top_layout.setContentsMargins(0, 0, 0, 0)
        # 2 px (v0.39, R5): 6 px left the panes too short for the
        # transcript block at the default window size (see the
        # resize note in build_window).
        top_layout.setSpacing(2)

        transcript = QtWidgets.QTextEdit()
        transcript.setReadOnly(True)
        # Links in a rendered reply open in the default browser
        # (via the viewport filter: QTextEdit has no anchorClicked
        # signal in this Qt build).
        transcript.viewport().installEventFilter(
            _LinkClickFilter(transcript))
        transcript.setPlaceholderText("No messages yet.")
        # Wrap at the widget's width, set explicitly -- long model
        # replies must wrap in the pane, never run off the edge.
        transcript.setLineWrapMode(
            QtWidgets.QTextEdit.LineWrapMode.WidgetWidth)
        # The divider can never crush the transcript below this.
        transcript.setMinimumHeight(HIST_TRANSCRIPT_MIN_HEIGHT)
        top_layout.addWidget(transcript, stretch=1)

        # Search row: find text in this pane's transcript. Display
        # only -- it never changes history, labels, or the saved
        # file. Any change to the transcript clears it (see
        # clear_search).
        search_row = QtWidgets.QHBoxLayout()
        search_edit = QtWidgets.QLineEdit()
        search_edit.setPlaceholderText("Search this conversation…")
        search_row.addWidget(search_edit, stretch=1)
        search_count = QtWidgets.QLabel("")
        search_row.addWidget(search_count)
        search_prev = QtWidgets.QPushButton("Previous")
        search_row.addWidget(search_prev)
        search_next = QtWidgets.QPushButton("Next")
        search_row.addWidget(search_next)
        top_layout.addLayout(search_row)

        # Stats line: the last completed turn's speed and RAM. Dashes
        # until a turn has run.
        stats = QtWidgets.QLabel(format_stats_line(None))
        stats.setStyleSheet("font-size: 11px;")
        top_layout.addWidget(stats)

        # Activity row (v0.29): a small colored light and small words
        # saying what this side is doing right now -- grey Idle,
        # amber waiting, green thinking, red failed. Both are set
        # together by set_side_state, the one setter, so the color
        # and the words always agree. The bottom-row status label
        # shows the same words (set by the same setter). The session
        # token total shares this row (v0.39, R5): it used to have a
        # row of its own, and the panes were too short for the
        # transcript block at the default window size (see the
        # resize note in build_window).
        activity_row = QtWidgets.QHBoxLayout()
        activity_dot = QtWidgets.QLabel()
        activity_dot.setFixedSize(12, 12)
        activity_dot.setStyleSheet(
            "background-color: %s; border-radius: 6px;"
            % SIDE_STATES["idle"][0])
        activity_row.addWidget(activity_dot)
        activity_label = QtWidgets.QLabel(SIDE_STATES["idle"][1])
        activity_label.setStyleSheet("font-size: 11px;")
        activity_row.addWidget(activity_label)
        activity_row.addStretch(1)
        # Sparkline (v0.47, R9): the live tok/s graph -- one per
        # pane, in the side's color, on the activity row where the
        # space is free. Zero height cost: it sits beside the
        # session total, not under it.
        sparkline = Sparkline(SIDE_COLORS[side])
        activity_row.addWidget(sparkline.widget)
        # Session token total: every token this side has generated
        # or been prompted with since the app started. Clear never
        # resets it -- only restarting the app does.
        session_tokens = QtWidgets.QLabel(
            format_session_line({"out": 0, "in": 0}))
        session_tokens.setStyleSheet("font-size: 11px;")
        activity_row.addWidget(session_tokens)
        top_layout.addLayout(activity_row)

        # Summarize row: the nudge label appears when this side's
        # history passes the budget; the button is always here.
        summarize_row = QtWidgets.QHBoxLayout()
        nudge = QtWidgets.QLabel("History is long. Summarize?")
        nudge.setVisible(False)
        summarize_row.addWidget(nudge)
        summarize_btn = QtWidgets.QPushButton("Summarize")
        summarize_btn.setEnabled(False)
        summarize_row.addWidget(summarize_btn)
        summarize_row.addStretch(1)
        top_layout.addLayout(summarize_row)

        # History editor: reorder or delete any message. Hidden by
        # default; the model sees the edited order on the next turn.
        hist_frame = QtWidgets.QFrame()
        hist_layout = QtWidgets.QVBoxLayout(hist_frame)
        hist_layout.setContentsMargins(0, 0, 0, 0)
        hist_layout.setSpacing(4)
        hist_list = QtWidgets.QListWidget()
        # No fixed height any more (v0.38, T20): the list is as tall
        # as the divider leaves it, down to one row. The frame's own
        # floor is the 60 px minimum, so the buttons stay reachable.
        hist_list.setMinimumHeight(24)
        hist_frame.setMinimumHeight(HIST_EDITOR_MIN_HEIGHT)
        hist_layout.addWidget(hist_list)
        hist_btn_row = QtWidgets.QHBoxLayout()
        hist_up = QtWidgets.QPushButton("Up")
        hist_down = QtWidgets.QPushButton("Down")
        hist_delete = QtWidgets.QPushButton("Delete")
        for btn in (hist_up, hist_down, hist_delete):
            btn.setEnabled(False)
            hist_btn_row.addWidget(btn)
        hist_btn_row.addStretch(1)
        hist_layout.addLayout(hist_btn_row)
        hist_frame.setVisible(False)

        # History splitter (v0.38, T20): the transcript block and
        # the history editor, with a draggable divider. The editor
        # keeps its size (stretch 0); the transcript takes the rest.
        # Every drag is saved as it happens, so the size survives
        # hiding the editor.
        hist_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        hist_split.addWidget(top_frame)
        hist_split.addWidget(hist_frame)
        hist_split.setStretchFactor(0, 1)
        hist_split.setStretchFactor(1, 0)
        hist_split.splitterMoved.connect(
            (lambda s: lambda *a: prefs.setValue(
                SETTING_HIST_SPLITTER_STATE % s,
                hist_split.saveState()))(side))
        layout.addWidget(hist_split, stretch=1)

        bottom = QtWidgets.QHBoxLayout()
        status = QtWidgets.QLabel("Idle")
        bottom.addWidget(status, stretch=1)
        use_this = QtWidgets.QPushButton("Use this")
        use_this.setEnabled(False)
        bottom.addWidget(use_this)
        stop = QtWidgets.QPushButton("Stop")
        stop.setEnabled(False)
        bottom.addWidget(stop)
        clear = QtWidgets.QPushButton("Clear")
        bottom.addWidget(clear)
        save = QtWidgets.QPushButton("Save")
        bottom.addWidget(save)
        import_btn = QtWidgets.QPushButton("Import")
        bottom.addWidget(import_btn)
        hist_toggle = QtWidgets.QPushButton("History ▸")
        hist_toggle.setCheckable(True)
        hist_toggle.setFlat(True)
        bottom.addWidget(hist_toggle)
        layout.addLayout(bottom)

        widgets[side] = {
            "title": title,
            "picker": picker, "refresh": refresh, "transcript": transcript,
            "temperature": temp_spin,
            "temp_explain": temp_explain,
            "status": status, "stop": stop, "clear": clear,
            "save": save, "import": import_btn,
            "search_edit": search_edit, "search_count": search_count,
            "search_prev": search_prev, "search_next": search_next,
            "search_matches": [], "search_current": -1,
            "instr_toggle": instr_toggle, "instr_edit": instr_edit,
            "instr_explain": instr_explain,
            "use_this": use_this, "hist_toggle": hist_toggle,
            "hist_frame": hist_frame, "hist_list": hist_list,
            "hist_split": hist_split,
            "hist_up": hist_up, "hist_down": hist_down,
            "hist_delete": hist_delete,
            "guide_shown": False,
            "stats": stats, "session_tokens": session_tokens,
            "activity_dot": activity_dot, "activity": activity_label,
            "sparkline": sparkline,
            "nudge": nudge, "summarize": summarize_btn,
            "reply_open": False,
            # Document position where the streaming reply's body
            # starts (just after its label). on_done uses it to
            # replace the streamed plain text with the Markdown
            # rendering; None when no reply is open.
            "reply_body_pos": None,
            # How long the streaming reply's label is ("model: "),
            # so on_done can color it when the turn finishes.
            "reply_label_len": None,
        }
        return frame

    panes.addWidget(make_pane("A"))
    panes.addWidget(make_pane("B"))
    panes.setSizes([550, 550])

    # --- director bar -----------------------------------------------------
    director = QtWidgets.QFrame()
    director.setFrameShape(QtWidgets.QFrame.StyledPanel)
    d_layout = QtWidgets.QVBoxLayout(director)
    d_layout.setContentsMargins(6, 6, 6, 6)
    d_layout.setSpacing(6)
    composer = QtWidgets.QPlainTextEdit()
    composer.setPlaceholderText("Type a message for the models...")
    # No block cap on purpose: a chat composer must never drop typed
    # or pasted text. (v0.12 and earlier capped it at 5 blocks, which
    # silently threw away the oldest paragraphs of a long paste.)
    # 38 px (v0.39, R5): 64 px left the panes too short for the
    # transcript block at the default window size (see the resize
    # note above). Still shows two full lines of typed text.
    composer.setFixedHeight(38)
    d_layout.addWidget(composer)

    # --- attachments -------------------------------------------------------
    # Attach reads the file contents right away (text files only, up to
    # 100 KB each, max 5). The list shows what will ride along with the
    # next message; each row has an x to drop that file.
    attach_row = QtWidgets.QHBoxLayout()
    attach_btn = QtWidgets.QPushButton("Attach")
    attach_row.addWidget(attach_btn)
    attach_note = QtWidgets.QLabel(
        "Text files only, up to 100 KB each, max 5 files.")
    attach_note.setStyleSheet("font-size: 11px;")
    attach_row.addWidget(attach_note)
    attach_row.addStretch(1)
    # "Enter to send": when ticked, plain Enter sends to both models
    # and Shift+Enter adds a new line. Off by default; the tick is
    # remembered between runs (QSettings). Ctrl+Enter always sends,
    # ticked or not -- see the key filter by the send buttons.
    enter_send_check = QtWidgets.QCheckBox("Enter to send")
    enter_send_check.setToolTip(
        "When on, Enter sends to both models and Shift+Enter adds "
        "a new line. Ctrl+Enter always sends to both.")
    enter_send_check.setChecked(
        prefs.value(SETTING_ENTER_TO_SEND, False, type=bool))

    def _save_enter_to_send(checked):
        prefs.setValue(SETTING_ENTER_TO_SEND, bool(checked))
        prefs.sync()

    enter_send_check.toggled.connect(_save_enter_to_send)
    attach_row.addWidget(enter_send_check)
    d_layout.addLayout(attach_row)

    attach_frame = QtWidgets.QFrame()
    attach_layout = QtWidgets.QVBoxLayout(attach_frame)
    attach_layout.setContentsMargins(0, 0, 0, 0)
    attach_layout.setSpacing(2)
    attach_frame.setVisible(False)
    d_layout.addWidget(attach_frame)

    btn_row = QtWidgets.QHBoxLayout()
    # No hint label here: the buttons say what they do; the stretch
    # keeps them right-aligned, where they were.
    btn_row.addStretch(1)
    send_a = QtWidgets.QPushButton("Send to A")
    send_b = QtWidgets.QPushButton("Send to B")
    send_both = QtWidgets.QPushButton("Send to Both")
    for btn in (send_a, send_b, send_both):
        btn.setEnabled(False)
        btn_row.addWidget(btn)
    debug_btn = QtWidgets.QPushButton("Debug report")
    btn_row.addWidget(debug_btn)
    phone_btn = QtWidgets.QPushButton("Phone deck")
    btn_row.addWidget(phone_btn)
    update_btn = QtWidgets.QPushButton("Check for updates")
    btn_row.addWidget(update_btn)
    d_layout.addLayout(btn_row)

    # P1-7 (v0.56): the row held ten buttons and four labels
    # clipped at the 900 px minimum width. The save/open actions
    # move to a second row -- the File menu already mirrors them,
    # but the buttons stay for one-click saving.
    btn_row2 = QtWidgets.QHBoxLayout()
    btn_row2.addStretch(1)
    save_both = QtWidgets.QPushButton("Save both")
    btn_row2.addWidget(save_both)
    save_full = QtWidgets.QPushButton("Save full transcript")
    btn_row2.addWidget(save_full)
    save_convo = QtWidgets.QPushButton("Save conversation")
    btn_row2.addWidget(save_convo)
    open_convo = QtWidgets.QPushButton("Open conversation")
    btn_row2.addWidget(open_convo)
    d_layout.addLayout(btn_row2)

    # --- director sections, scrollable (P1-8, v0.56) ---------------
    # The sections used to sit directly in the director frame, so
    # with Relay + Pairs + Tasks open they ran off the bottom of a
    # short window with no way to reach them -- on a 768 px screen
    # there is no taller resize to escape to. They now live in a
    # vertical scroll area; the composer and the send rows above
    # stay pinned, and the sections scroll under them. The review
    # frame stays pinned below the scroll area: it is an
    # interruption, and it must never be scrolled out of sight.
    director_scroll = QtWidgets.QScrollArea()
    director_scroll.setWidgetResizable(True)
    director_scroll.setHorizontalScrollBarPolicy(
        QtCore.Qt.ScrollBarAlwaysOff)
    director_scroll.setVerticalScrollBarPolicy(
        QtCore.Qt.ScrollBarAsNeeded)
    director_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
    director_scroll.setMinimumHeight(60)
    director_scroll.setMaximumHeight(DIRECTOR_SCROLL_MAX_HEIGHT)
    sections = QtWidgets.QWidget()
    sections_layout = QtWidgets.QVBoxLayout(sections)
    sections_layout.setContentsMargins(0, 0, 0, 0)
    sections_layout.setSpacing(6)
    director_scroll.setWidget(sections)
    d_layout.addWidget(director_scroll, stretch=1)

    # --- director sections (v0.37, R4) -------------------------------
    # Progressive disclosure: the director's controls live in three
    # collapsible sections under the send path, each with its own
    # header toggle in the Director toggle's style. The composer,
    # the attach row, and the button row above stay always visible
    # -- that is the core send path. Relay starts expanded (the
    # relay is the heart of DUO); Pairs and Tasks start collapsed
    # (set once, used occasionally). Each choice is remembered
    # between runs. Collapsing a section hides only its controls:
    # running work keeps going, and nothing ever auto-expands -- a
    # relay running with its section collapsed still shows on the
    # chip above the Director toggle.
    def _make_section(name, key, default_collapsed):
        toggle = QtWidgets.QPushButton()
        toggle.setFlat(True)
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)

        def _apply(collapsed):
            body.setVisible(not collapsed)
            toggle.setText("%s %s" % (
                name, "\u25b8" if collapsed else "\u25be"))

        def _set_collapsed(collapsed):
            _apply(collapsed)
            prefs.setValue(SETTING_SECTION_COLLAPSED % key,
                           bool(collapsed))
            prefs.sync()

        toggle.clicked.connect(
            lambda: _set_collapsed(body.isVisible()))
        _apply(prefs.value(SETTING_SECTION_COLLAPSED % key,
                           default_collapsed, type=bool))
        return toggle, body, body_layout, _set_collapsed

    # --- relay section -------------------------------------------------
    # The relay controls sit in TWO rows now (audit T24, subsumed
    # by R4): one row held nine widgets and the stop-phrase field
    # shrank to a sliver at 900 px. Row one starts the relay, row
    # two stops it; the stop-phrase field stretches across the
    # second row. The preset row lives here too -- presets are
    # relay settings.
    relay_toggle, relay_body, relay_layout, _set_relay_collapsed = \
        _make_section("Relay", "relay", False)
    sections_layout.addWidget(relay_toggle)
    sections_layout.addWidget(relay_body)
    relay_row1 = QtWidgets.QHBoxLayout()
    relay_btn = QtWidgets.QPushButton("Start Relay")
    relay_btn.setEnabled(False)
    relay_row1.addWidget(relay_btn)
    relay_row1.addWidget(QtWidgets.QLabel("Start with:"))
    start_picker = QtWidgets.QComboBox()
    start_picker.addItems(["A", "B"])
    relay_row1.addWidget(start_picker)
    relay_row1.addWidget(QtWidgets.QLabel("Turns:"))
    cap_spin = QtWidgets.QSpinBox()
    cap_spin.setRange(RELAY_CAP_MIN, RELAY_CAP_MAX)
    cap_spin.setValue(RELAY_CAP_DEFAULT)
    relay_row1.addWidget(cap_spin)
    mode_picker = QtWidgets.QComboBox()
    mode_picker.addItems(["Blind", "Aware"])
    relay_row1.addWidget(mode_picker)
    # Field explanation (v0.42, U20): in the row's empty right
    # space, taking the stretch's place. It wraps to two lines
    # there; the row stays about the same height because the
    # controls are ~28 px tall.
    relay_explain = _make_explanation(EXPLAIN_RELAY_ROW1)
    relay_row1.addWidget(relay_explain, stretch=1)
    relay_layout.addLayout(relay_row1)
    relay_row2 = QtWidgets.QHBoxLayout()
    relay_row2.addWidget(QtWidgets.QLabel("Stop on:"))
    stop_edit = QtWidgets.QLineEdit()
    stop_edit.setPlaceholderText("words, separated by commas")
    relay_row2.addWidget(stop_edit, stretch=1)
    # Field explanation (v0.42, U20): beside the field, on one
    # line -- the field narrows to make room, and keeps 200 px+
    # at the 900 px minimum width. T24's intent was usable, not
    # a sliver, and 200 px still shows a stop word.
    stop_explain = _make_explanation(EXPLAIN_STOP, wrap=False)
    relay_row2.addWidget(stop_explain)
    agree_check = QtWidgets.QCheckBox("Stop on agreement")
    relay_row2.addWidget(agree_check)
    review_check = QtWidgets.QCheckBox("Review each hop")
    relay_row2.addWidget(review_check)
    relay_layout.addLayout(relay_row2)

    # --- relay presets -----------------------------------------------------
    # Same layout as the saved-pairs row: a dropdown of what is saved,
    # a name field, Save, Delete. Nothing loads by itself.
    preset_row = QtWidgets.QHBoxLayout()
    preset_row.addWidget(QtWidgets.QLabel("Preset:"))
    preset_combo = QtWidgets.QComboBox()
    preset_combo.setPlaceholderText("Saved presets")
    preset_row.addWidget(preset_combo, stretch=1)
    preset_name = QtWidgets.QLineEdit()
    preset_name.setPlaceholderText("Name for this preset")
    preset_row.addWidget(preset_name, stretch=1)
    preset_save = QtWidgets.QPushButton("Save preset")
    preset_row.addWidget(preset_save)
    preset_delete = QtWidgets.QPushButton("Delete preset")
    preset_delete.setEnabled(False)
    preset_row.addWidget(preset_delete)
    relay_layout.addLayout(preset_row)

    # --- pairs section -------------------------------------------------
    pairs_toggle, pairs_body, pairs_layout, _set_pairs_collapsed = \
        _make_section("Pairs", "pairs", True)
    sections_layout.addWidget(pairs_toggle)
    sections_layout.addWidget(pairs_body)

    # --- saved pairs -------------------------------------------------------
    pair_row = QtWidgets.QHBoxLayout()
    pair_row.addWidget(QtWidgets.QLabel("Pair:"))
    pair_combo = QtWidgets.QComboBox()
    pair_combo.setPlaceholderText("Saved pairs")
    pair_row.addWidget(pair_combo, stretch=1)
    pair_name = QtWidgets.QLineEdit()
    pair_name.setPlaceholderText("Name for this pair")
    pair_row.addWidget(pair_name, stretch=1)
    pair_save = QtWidgets.QPushButton("Save pair")
    pair_row.addWidget(pair_save)
    pair_delete = QtWidgets.QPushButton("Delete pair")
    pair_delete.setEnabled(False)
    pair_row.addWidget(pair_delete)
    pairs_layout.addLayout(pair_row)

    # --- tasks section -------------------------------------------------
    tasks_toggle, tasks_body, tasks_layout, _set_tasks_collapsed = \
        _make_section("Tasks", "tasks", True)
    sections_layout.addWidget(tasks_toggle)
    sections_layout.addWidget(tasks_body)

    # --- task row ---------------------------------------------------------
    # Tasks (Item 6): hand a job to one model, and optionally have the
    # other model review or continue the result. Nothing pre-selected.
    task_edit = QtWidgets.QPlainTextEdit()
    task_edit.setPlaceholderText("Describe a task for one model...")
    task_edit.setFixedHeight(44)
    tasks_layout.addWidget(task_edit)
    # Field explanation (v0.42, U20): under the task box, inside
    # the collapsed Tasks section -- zero cost at default.
    task_explain = _make_explanation(EXPLAIN_TASK)
    tasks_layout.addWidget(task_explain)

    task_row = QtWidgets.QHBoxLayout()
    task_row.addWidget(QtWidgets.QLabel("Task:"))
    task_worker = QtWidgets.QComboBox()
    task_worker.addItems(["Model A", "Model B"])
    task_worker.setPlaceholderText("Who does it")
    task_worker.setCurrentIndex(-1)
    task_row.addWidget(task_worker)
    task_row.addWidget(QtWidgets.QLabel("Then the other side:"))
    task_follow_up = QtWidgets.QComboBox()
    task_follow_up.addItems(["Review it", "Continue it", "Nothing"])
    task_follow_up.setPlaceholderText("What next")
    task_follow_up.setCurrentIndex(-1)
    task_row.addWidget(task_follow_up)
    task_row.addWidget(QtWidgets.QLabel("Rounds:"))
    task_rounds = QtWidgets.QSpinBox()
    task_rounds.setRange(TASK_ROUNDS_MIN, TASK_ROUNDS_MAX)
    task_rounds.setValue(TASK_ROUNDS_DEFAULT)
    task_rounds.setToolTip(
        "How many rounds the task runs: each round passes the work "
        "to the other side. With Review it or Continue it the task "
        "always runs at least 2 rounds.")
    task_row.addWidget(task_rounds)
    task_run = QtWidgets.QPushButton("Run Task")
    task_run.setEnabled(False)
    task_row.addWidget(task_run)
    task_row.addStretch(1)
    tasks_layout.addLayout(task_row)
    # Field explanation (v0.42, U20): one combined line under the
    # task row, inside the collapsed Tasks section -- the three
    # controls share a row, so one line covers all three.
    task_row_explain = _make_explanation(EXPLAIN_TASK_ROW)
    tasks_layout.addWidget(task_row_explain)

    # --- hop review (shown only while a relay waits on a review) ----------
    # It stays outside the sections on purpose: a review pause needs
    # its Forward / Skip / Stop buttons reachable, and the Relay
    # section might be collapsed. The box only appears while a relay
    # waits, so it adds no permanent clutter.
    review_frame = QtWidgets.QFrame()
    review_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
    review_layout = QtWidgets.QVBoxLayout(review_frame)
    review_layout.setContentsMargins(6, 6, 6, 6)
    review_layout.setSpacing(6)
    review_label = QtWidgets.QLabel("Review this hop")
    review_label.setStyleSheet("font-weight: bold;")
    review_layout.addWidget(review_label)
    review_edit = QtWidgets.QPlainTextEdit()
    review_edit.setFixedHeight(64)
    review_layout.addWidget(review_edit)
    review_btn_row = QtWidgets.QHBoxLayout()
    review_forward = QtWidgets.QPushButton("Forward")
    review_skip = QtWidgets.QPushButton("Skip this hop")
    review_stop = QtWidgets.QPushButton("Stop relay")
    for btn in (review_forward, review_skip, review_stop):
        review_btn_row.addWidget(btn)
    review_btn_row.addStretch(1)
    review_layout.addLayout(review_btn_row)
    review_frame.setVisible(False)
    d_layout.addWidget(review_frame)

    # --- relay status chip (v0.34) -------------------------------------
    # The relay's status lives OUTSIDE the collapsible director area,
    # in a small chip row directly under the panes: a dot, the status
    # words, and a Stop button that only shows while a relay runs.
    # The old status label sat at the bottom of the director, so
    # collapsing the director hid the relay's state entirely -- the
    # chip stays visible either way, and it is the only relay status
    # display. The relay row loses its label in the move, which also
    # relieves some of its crowding (audit T24, subsumed by R4).
    relay_chip = QtWidgets.QFrame()
    relay_chip.setFrameShape(QtWidgets.QFrame.StyledPanel)
    chip_layout = QtWidgets.QHBoxLayout(relay_chip)
    chip_layout.setContentsMargins(6, 3, 6, 3)
    chip_layout.setSpacing(6)
    relay_dot = QtWidgets.QLabel()
    relay_dot.setFixedSize(12, 12)
    relay_dot.setStyleSheet(
        "background-color: %s; border-radius: 6px;"
        % RELAY_CHIP_STATES["idle"][0])
    chip_layout.addWidget(relay_dot)
    relay_status = QtWidgets.QLabel(RELAY_CHIP_STATES["idle"][1])
    relay_status.setStyleSheet("font-size: 11px;")
    chip_layout.addWidget(relay_status)
    chip_layout.addStretch(1)
    relay_chip_stop = QtWidgets.QPushButton("Stop")
    relay_chip_stop.setVisible(False)
    chip_layout.addWidget(relay_chip_stop)
    root.addWidget(relay_chip)

    def set_relay_chip(chip_state, text=None):
        """The one setter for the relay chip: dot color, status words,
        and whether the chip's Stop button shows. ``text`` overrides
        the state's default words -- the running turn counts, the
        review pause, and the end reason all come from the relay
        session. The Stop button shows only while a relay runs:
        idle and ended have nothing to stop, and a review pause
        already has its own Stop in the review box.
        """
        color, default_text = RELAY_CHIP_STATES[chip_state]
        relay_dot.setStyleSheet(
            "background-color: %s; border-radius: 6px;" % color)
        relay_status.setText(default_text if text is None else text)
        relay_chip_stop.setVisible(chip_state == "running")

    # --- director toggle (v0.31) --------------------------------------
    # One toggle for the whole director area, on the boundary between
    # the panes and the director rows. Collapsing hides the rows and
    # the panes grow to fill the freed space; the toggle itself stays
    # visible either way, so the controls are always one click back.
    # Collapsing changes nothing else: a relay or task in flight keeps
    # running, and the activity lights and meter live in the panes
    # and the status bar, outside the collapsed area. Expanded on
    # first open; the choice is remembered between runs (QSettings).
    director_toggle = QtWidgets.QPushButton("Director \u25be")
    director_toggle.setFlat(True)
    director_toggle.setToolTip(
        "Hide the director controls so the panes take the whole "
        "window. Click again to bring them back.")
    root.addWidget(director_toggle)
    root.addWidget(director)

    # Director spring (v0.46, R8; the animation lives in
    # duo_motion.py): the click flips the spring's target, not the
    # director's visibility -- mid-animation it still shows the old
    # state, so flipping on it would re-collapse, not expand.
    director_spring = DirectorSpring(director, prefs)

    def _set_director_collapsed(collapsed, instant=False):
        director_toggle.setText(
            "Director \u25b8" if collapsed else "Director \u25be")
        prefs.setValue(SETTING_DIRECTOR_COLLAPSED, bool(collapsed))
        prefs.sync()
        director_spring.set_collapsed(collapsed, instant=instant)

    director_toggle.clicked.connect(
        lambda: _set_director_collapsed(
            not director_spring.target_collapsed))
    if prefs.value(SETTING_DIRECTOR_COLLAPSED, False, type=bool):
        _set_director_collapsed(True, instant=True)

    statusbar = window.statusBar()
    # Status notes (v0.36, T22): a permanent label, not a timed
    # message -- a note stays until the next note replaces it, so
    # looking away for a few seconds never loses it. One setter,
    # set_status_message, is the only way notes are written.
    status_label = QtWidgets.QLabel("")
    statusbar.addWidget(status_label, 1)

    def set_status_message(text):
        status_label.setText(text)

    set_status_message("Loading models...")
    # The status bar holds only the status message and the system
    # meter.

    # System meter (v0.29): the whole machine's CPU % and RAM in the
    # status bar, re-read from /proc once a second. Two small file
    # reads a second -- cheap -- and the timer is parented to the
    # window, so it stops when the window closes. When /proc is
    # unreadable the label stays empty: fail silent, never a note.
    meter_label = QtWidgets.QLabel("")
    meter_label.setStyleSheet("font-size: 11px;")
    statusbar.addPermanentWidget(meter_label)
    meter_prev = {"cpu": None}

    def refresh_meter():
        cpu_pct, used, total, prev = read_system_meter(
            meter_prev["cpu"])
        meter_prev["cpu"] = prev
        meter_label.setText(
            format_system_meter(cpu_pct, used, total))

    meter_timer = QtCore.QTimer(window)
    meter_timer.setInterval(METER_REFRESH_MS)
    meter_timer.timeout.connect(refresh_meter)
    meter_timer.start()
    refresh_meter()  # First read now: RAM shows at once, CPU needs
    # the second read (a % is a delta between two samples).

    state = {"busy": False, "active": set(), "queued": set(), "relay": None,
             # Compare mode: the latest reply per side from a Send to
             # Both round. Complete when both sides are present.
             "compare": {}, "compare_round": False,
             # Edit-before-forward: the hop waiting on a review, or None.
             "pending_hop": None,
             # Last completed turn's stats per side (None until a turn
             # has run), and the side currently summarizing, if any.
             "stats": {"A": None, "B": None}, "summarizing": None,
             # Session token totals per side: {"out": int, "in": int}
             # across every generation since the app started. Clear
             # never resets these -- only restarting the app does.
             "session_tokens": {"A": {"out": 0, "in": 0},
                                "B": {"out": 0, "in": 0}},
             # Attachments waiting for the next send: [{"name", "text"}].
             # One-shot -- they clear after a send.
             "attachments": [],
             # The task in flight (a TaskSession), or None.
             "task": None,
             # The full transcript: every message that lands in either
             # side's history, in arrival order, as (side, label,
             # display text). Reorder/delete never touch it -- it is
             # what actually happened. Clearing a side removes that
             # side's entries; summarize leaves it alone. The log is
             # bounded (FULL_LOG_MAX_ENTRIES): the oldest entries
             # drop off once the cap is passed, oldest first.
             "full_log": [],
             # True once the log has been trimmed: the full-transcript
             # export then says so, so a saved file never silently
             # loses old messages. Set when a trim actually happens;
             # cleared when the log is rebuilt (a conversation load --
             # the loaded log is complete by definition) or emptied
             # (clearing both sides). Clearing one side leaves it
             # set: the remaining entries are still a trimmed log.
             "full_log_trimmed": False,
             # The model list is loading (v0.33): the pickers stay
             # disabled with a "Loading models..." placeholder until
             # the list lands or the load fails.
             "models_loading": False,
             # Total RAM in bytes, read once at startup for the
             # picker's fit labels (v0.33, U39). None when /proc
             # can't be read: the pickers then show sizes only.
             "total_ram": read_total_ram(),
             # First-run guide (v0.39, R5): True once the user has
             # used the app (first send, relay, task, opened
             # conversation, or import). While False and both panes
             # are empty, the transcripts show the guide.
             "first_run_done": prefs.value(
                 SETTING_FIRST_RUN_DONE, False, type=bool)}

    # --- helpers ----------------------------------------------------------
    # Transcript formats: sender labels are bold, everything else
    # plain. Defined here (Qt lives in this function, not the module).
    _PLAIN_FMT = QtGui.QTextCharFormat()
    _BOLD_FMT = QtGui.QTextCharFormat()
    _BOLD_FMT.setFontWeight(QtGui.QFont.Weight.Bold)

    def _label_format(color):
        """A bold label format in the given side color, or plain
        bold when the color is None. A copy each time: the shared
        bold format must never pick up a color."""
        if color is None:
            return _BOLD_FMT
        fmt = QtGui.QTextCharFormat(_BOLD_FMT)
        fmt.setForeground(QtGui.QColor(color))
        return fmt

    def _label_color(side, msg):
        """The color for one message's label in this pane (v0.40,
        T25): the source side's color for a relay/task handoff, the
        pane's own color for a model reply or the summary entry, and
        None (default text) for the user's own words. Provenance
        comes from the message's source_side, never from parsing
        the label text."""
        if msg.source_side is not None:
            return SIDE_COLORS[msg.source_side]
        if msg.role == "assistant" or msg.label == SUMMARY_LABEL:
            return SIDE_COLORS[side]
        return None

    def scroll_to_end(transcript):
        bar = transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def append_text(side, text):
        # Raw append, always in the plain (non-bold) format: streaming
        # chunks and reply bodies must never inherit the bold label
        # format the cursor carries after a label insert.
        t = widgets[side]["transcript"]
        cursor = t.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text, _PLAIN_FMT)
        t.setTextCursor(cursor)
        scroll_to_end(t)

    def _block_gap(cursor):
        """A blank line before a new block, when the document already
        holds something. Blocks end with exactly one "\n", so one more
        "\n" is the blank line between messages."""
        if not cursor.document().isEmpty():
            cursor.insertText("\n", _PLAIN_FMT)

    def append_message(side, label, text, markdown=False, color=None):
        """Append one message block: a blank line separating it from
        the previous block, a bold "label: ", then the text. The live
        path and the re-render path both use this, so the window
        always shows the same format.

        ``markdown`` renders the body through markdown_to_html
        (assistant messages only -- user messages stay plain text
        exactly as typed). The block is closed with insertBlock
        rather than a "\n" insert: after a list or code block the
        cursor is still inside that structure, and a plain "\n"
        would land in it as a stray empty item (and swallow the
        NEXT message into the list). insertBlock with a default
        format steps out cleanly; the plain text comes out the
        same either way.

        ``color`` is a side color for the label (v0.40, T25): the
        pane's color for a model reply, the source side's color for
        a relay/task handoff. None leaves the label default text
        color -- the user's own words."""
        t = widgets[side]["transcript"]
        cursor = t.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        _block_gap(cursor)
        cursor.insertText(label + ": ", _label_format(color))
        if markdown:
            cursor.insertHtml(
                markdown_to_html(text, code_bg=theme["code_bg"]))
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertBlock(QtGui.QTextBlockFormat())
        else:
            cursor.insertText(text, _PLAIN_FMT)
            cursor.insertText("\n", _PLAIN_FMT)
        t.setTextCursor(cursor)
        scroll_to_end(t)
        clear_search(side)

    def begin_reply(side, label):
        """Open a streaming reply: the blank-line gap plus the bold
        label, with the body to follow as chunks arrive. Records
        where the body starts, so on_done can replace the streamed
        plain text with the Markdown rendering, and how long the
        label is, so on_done can color it then too. The label goes
        in plain bold while the reply streams (v0.24: streaming
        stays plain); the color lands with the finish re-render."""
        t = widgets[side]["transcript"]
        cursor = t.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        _block_gap(cursor)
        cursor.insertText(label + ": ", _BOLD_FMT)
        widgets[side]["reply_body_pos"] = cursor.position()
        widgets[side]["reply_label_len"] = len(label) + 2
        t.setTextCursor(cursor)
        scroll_to_end(t)

    def render_finished_reply(side, text):
        """Re-render the reply that just finished streaming as
        Markdown. The body streamed as plain text (partial Markdown
        mid-stream would flicker and mangle); now that the turn is
        done, the whole body -- plus the trailing newline
        finish_reply added -- is replaced with the rendered version,
        closed the same way append_message closes a Markdown block.
        The replacement uses the finished ``text`` (the history's
        copy), not the streamed characters, so the pane ends up
        holding exactly what the history holds. A failed or stopped
        turn never reaches this: partial text stays plain. The
        search is cleared, as for any transcript change. The label
        takes the pane's color here too (v0.40, T25): it went in
        plain bold while the reply streamed, and the turn is done
        now, so the label range just before the body is recolored
        in the same step."""
        body_pos = widgets[side].get("reply_body_pos")
        widgets[side]["reply_body_pos"] = None
        label_len = widgets[side].get("reply_label_len")
        widgets[side]["reply_label_len"] = None
        if body_pos is None:
            return
        t = widgets[side]["transcript"]
        doc = t.document()
        end = doc.characterCount() - 1
        if body_pos > end:
            return
        if label_len:
            label_cursor = QtGui.QTextCursor(doc)
            label_cursor.setPosition(body_pos - label_len)
            label_cursor.setPosition(body_pos,
                                     QtGui.QTextCursor.KeepAnchor)
            label_cursor.mergeCharFormat(
                _label_format(SIDE_COLORS[side]))
        cursor = QtGui.QTextCursor(doc)
        cursor.setPosition(body_pos)
        cursor.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertHtml(markdown_to_html(text, code_bg=theme["code_bg"]))
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertBlock(QtGui.QTextBlockFormat())
        t.setTextCursor(cursor)
        scroll_to_end(t)
        clear_search(side)

    def append_note(side, text):
        """Append a transient note ("(Relay stopped: ...)",
        "(Picked for Model B.)") as its own block: blank-line gap,
        plain text, trailing newline. Notes are not in the history,
        so a re-render still drops them."""
        t = widgets[side]["transcript"]
        cursor = t.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        _block_gap(cursor)
        cursor.insertText(text, _PLAIN_FMT)
        cursor.insertText("\n", _PLAIN_FMT)
        t.setTextCursor(cursor)
        scroll_to_end(t)
        clear_search(side)

    def log_event(side, label, text):
        """Record one message in the shared chronological log (the
        full transcript). Called for every message that lands in a
        side's history, in arrival order. This is the SINGLE append
        point for the log -- every path that adds an entry goes
        through here, so the cap in append_full_log_entry covers
        sends, relay hops, tasks, notes, and imports alike. No other
        code appends to state["full_log"]."""
        if append_full_log_entry(state["full_log"], (side, label, text)):
            state["full_log_trimmed"] = True

    def render_transcript(side):
        """Rebuild a pane's transcript from its messages.

        Used after a reorder or delete. Rebuilt through append_message,
        so the format matches the live transcript exactly -- blank
        line between messages, bold sender labels. Transient notes
        like "(Stopped.)" are not in the history, so a re-render
        drops them. Assistant messages render as Markdown; user
        messages stay plain. The summary entry renders too -- it is
        model output, even though it is stored as a user entry.
        """
        side_obj = convo.sides[side]
        widgets[side]["transcript"].clear()
        widgets[side]["reply_body_pos"] = None
        widgets[side]["reply_label_len"] = None
        # The clear takes the first-run guide with it, if it was
        # showing (v0.39, R5); the refresh at the end puts it back
        # when the pane is still empty on a first run.
        widgets[side]["guide_shown"] = False
        for msg in side_obj.messages:
            markdown = (msg.role == "assistant"
                        or msg.label == SUMMARY_LABEL)
            append_message(side, msg.label, msg.display_text(),
                           markdown=markdown,
                           color=_label_color(side, msg))
        scroll_to_end(widgets[side]["transcript"])
        # A rebuild drops the highlights with the old document, so
        # the search state goes too (empty histories never reach
        # append_message, which clears it on its own).
        clear_search(side)
        refresh_empty_state(side)

    def refresh_empty_state(side):
        """Show or hide the first-run guide in one pane (v0.39, R5).

        The guide shows only while the user has never used the app
        (the first-run flag is unset) and both panes are empty -- it
        is document text, so it replaces the "No messages yet."
        placeholder, and it never enters the history, so exports
        and saves never see it. Step 1 ticks once both models are
        picked. Rebuilding is cheap and idempotent, so a refresh
        picks up a new tick. When the guide should not show, only a
        pane that is actually showing it is touched -- a pane with
        real messages is never cleared here. Either way the search
        goes, as for any transcript change."""
        t = widgets[side]["transcript"]
        if (not state["first_run_done"]
                and not convo.sides["A"].messages
                and not convo.sides["B"].messages):
            t.clear()
            cursor = t.textCursor()
            cursor.insertHtml(first_run_guide_html(
                convo.sides["A"].model is not None
                and convo.sides["B"].model is not None))
            widgets[side]["guide_shown"] = True
            clear_search(side)
        elif widgets[side]["guide_shown"]:
            t.clear()
            widgets[side]["guide_shown"] = False
            clear_search(side)

    def mark_first_run_done():
        """The user has used the app: the first-run guide never
        shows again. Sets the persisted flag and clears the guide
        from both panes. Called before the first message lands, so
        the guide is gone before anything is appended after it."""
        if state["first_run_done"]:
            return
        state["first_run_done"] = True
        prefs.setValue(SETTING_FIRST_RUN_DONE, True)
        for _side in ("A", "B"):
            refresh_empty_state(_side)

    # --- transcript search ---------------------------------------------------
    # Search is display-only: it highlights matches in the pane's
    # transcript and jumps between them, and never touches the
    # messages or the saved file. Matching is a case-insensitive plain
    # substring search over the displayed text -- no regex. Matches
    # are found through the document itself (QTextDocument.find), so
    # positions are always document positions, whatever characters
    # the text holds. The highlight colors come from the theme
    # (v0.51): yellow/orange on light, dark golds on dark -- the
    # hardcoded yellow made white text unreadable on a dark theme.
    _SEARCH_FMT = QtGui.QTextCharFormat()
    _SEARCH_FMT.setBackground(QtGui.QColor(theme["search_bg"]))
    _SEARCH_CURRENT_FMT = QtGui.QTextCharFormat()
    _SEARCH_CURRENT_FMT.setBackground(
        QtGui.QColor(theme["search_current_bg"]))

    def _apply_search_selections(side):
        w = widgets[side]
        doc = w["transcript"].document()
        selections = []
        for i, (start, end) in enumerate(w["search_matches"]):
            sel = QtWidgets.QTextEdit.ExtraSelection()
            cursor = QtGui.QTextCursor(doc)
            cursor.setPosition(start)
            cursor.setPosition(end, QtGui.QTextCursor.KeepAnchor)
            sel.cursor = cursor
            sel.format = (_SEARCH_CURRENT_FMT if i == w["search_current"]
                          else _SEARCH_FMT)
            selections.append(sel)
        w["transcript"].setExtraSelections(selections)

    def clear_search(side):
        """Drop a pane's search: query, highlights, count. Called
        whenever the transcript changes -- a new message, a note, a
        re-render, a clear -- because the matches would be stale."""
        w = widgets[side]
        w["search_matches"] = []
        w["search_current"] = -1
        w["transcript"].setExtraSelections([])
        w["search_count"].setText("")
        edit = w["search_edit"]
        edit.blockSignals(True)
        edit.setText("")
        edit.blockSignals(False)

    def _jump_to_search_match(side):
        w = widgets[side]
        if w["search_current"] < 0:
            return
        start, _end = w["search_matches"][w["search_current"]]
        t = w["transcript"]
        cursor = t.textCursor()
        cursor.setPosition(start)
        t.setTextCursor(cursor)
        t.ensureCursorVisible()

    def run_search(side):
        """Run the pane's current query over its displayed text."""
        w = widgets[side]
        query = w["search_edit"].text()
        if not query:
            w["search_matches"] = []
            w["search_current"] = -1
            w["transcript"].setExtraSelections([])
            w["search_count"].setText("")
            return
        doc = w["transcript"].document()
        matches = []
        cursor = doc.find(query, 0)
        while not cursor.isNull():
            matches.append((cursor.selectionStart(),
                            cursor.selectionEnd()))
            cursor = doc.find(query, cursor)
        w["search_matches"] = matches
        if not matches:
            w["search_current"] = -1
            w["transcript"].setExtraSelections([])
            w["search_count"].setText("No matches")
            return
        w["search_current"] = 0
        _apply_search_selections(side)
        w["search_count"].setText("%d matches" % len(matches))
        _jump_to_search_match(side)

    def step_search(side, delta):
        """Move to the next (+1) or previous (-1) match, wrapping at
        the ends. Does nothing when there are no matches."""
        w = widgets[side]
        matches = w["search_matches"]
        if not matches:
            return
        w["search_current"] = (w["search_current"] + delta) % len(matches)
        _apply_search_selections(side)
        w["search_count"].setText(
            "%d of %d" % (w["search_current"] + 1, len(matches)))
        _jump_to_search_match(side)

    def refresh_history_list(side):
        """Rebuild a pane's history-editor list from its history.

        Each item carries its color (v0.40, T25): the pane's own
        color for its own entries, the source side's color for a
        relay/task handoff -- the same provenance rule as the
        transcript labels, applied per item."""
        lst = widgets[side]["hist_list"]
        lst.blockSignals(True)
        lst.clear()
        side_obj = convo.sides[side]
        for msg in side_obj.messages:
            text = msg.display_text().replace("\n", " ")
            if len(text) > 70:
                text = text[:67] + "..."
            lst.addItem("%s: %s" % (msg.label, text))
            color = (SIDE_COLORS[msg.source_side]
                     if msg.source_side is not None
                     else SIDE_COLORS[side])
            lst.item(lst.count() - 1).setForeground(
                QtGui.QColor(color))
        lst.blockSignals(False)
        refresh_history_buttons(side)

    def refresh_history_buttons(side):
        """Enable the Up/Down/Delete buttons for the current selection.

        Editing is idle-only: never while this side is generating, a
        relay is running, or anything is busy.
        """
        w = widgets[side]
        lst = w["hist_list"]
        row = lst.currentRow()
        count = len(convo.sides[side].messages)
        idle = (not state["busy"] and state.get("relay") is None
                and side not in state["active"]
                and side not in state["queued"])
        w["hist_up"].setEnabled(idle and row > 0)
        w["hist_down"].setEnabled(idle and 0 <= row < count - 1)
        w["hist_delete"].setEnabled(idle and row >= 0)

    # The menu bar's actions, filled in when the menu is built near
    # the end of this function. refresh_buttons mirrors the buttons'
    # enabled states onto them; the mirror guards on the dict being
    # filled, so it is safe before the menu exists.
    menu_actions = {}

    def refresh_buttons():
        busy = state["busy"]
        relay = state.get("relay")
        relay_active = relay is not None and relay.active
        a_ready = convo.sides["A"].model is not None
        b_ready = convo.sides["B"].model is not None
        # Send buttons (v0.43, T21): disabled while the composer is
        # empty -- whitespace-only counts as empty. Attachments
        # alone never enable a send: a typed message is still
        # required even with files attached (v0.5).
        composer_has_text = bool(composer.toPlainText().strip())
        send_a.setEnabled(not busy and a_ready and composer_has_text)
        send_b.setEnabled(not busy and b_ready and composer_has_text)
        send_both.setEnabled(not busy and a_ready and b_ready
                             and composer_has_text)
        # Attaching mid-generation or mid-relay makes no sense: the
        # attachments clear at the send, so there is nothing to add to.
        attach_idle = not busy and not relay_active
        attach_btn.setEnabled(attach_idle)
        for btn in attach_frame.findChildren(QtWidgets.QPushButton):
            btn.setEnabled(attach_idle)
        relay_btn.setText("Stop Relay" if relay_active else "Start Relay")
        # P1-3 (v0.56): a relay needs a seed message, so the button
        # greys out on an empty composer, mirroring the send
        # buttons. While a relay runs the button is Stop, and the
        # composer is irrelevant then.
        relay_btn.setEnabled(
            relay_active or (not busy and a_ready and b_ready
                             and composer_has_text))
        for widget in (start_picker, cap_spin, mode_picker, stop_edit,
                       agree_check, review_check):
            widget.setEnabled(not relay_active)
        # Saving or loading a pair mid-generation could swap a model's
        # setup under a running turn, so pairs wait for idle too.
        pairs_idle = not busy and not relay_active
        pair_combo.setEnabled(pairs_idle)
        pair_name.setEnabled(pairs_idle)
        pair_save.setEnabled(pairs_idle)
        # Delete needs a pair selected as well as idle -- with
        # nothing selected there is nothing to delete.
        pair_delete.setEnabled(pairs_idle
                               and pair_combo.currentIndex() >= 0)
        # Relay presets wait for idle too: loading one mid-relay
        # would change the row under a running relay, and saving
        # one then would catch the row mid-flight.
        presets_idle = not busy and not relay_active
        preset_combo.setEnabled(presets_idle)
        preset_name.setEnabled(presets_idle)
        preset_save.setEnabled(presets_idle)
        # Delete needs a preset selected as well as idle.
        preset_delete.setEnabled(presets_idle
                                 and preset_combo.currentIndex() >= 0)
        # Saving is idle-only too: the transcript is moving
        # mid-generation or mid-relay, so a save then would catch a
        # half-written turn. Loading is idle-only for the same reason:
        # swapping the conversation under a running turn would strand
        # the reply in the wrong history.
        save_idle = not busy and not relay_active
        save_both.setEnabled(save_idle)
        save_full.setEnabled(save_idle)
        save_convo.setEnabled(save_idle)
        open_convo.setEnabled(save_idle)
        # The menu bar mirrors the buttons: same actions, same
        # enabled states (v0.30). The relay button is one toggle;
        # the menu splits it into Start (only when a relay can
        # start) and Stop (only while one runs).
        if menu_actions:
            menu_actions["save_conversation"].setEnabled(save_idle)
            menu_actions["open_conversation"].setEnabled(save_idle)
            menu_actions["save_full"].setEnabled(save_idle)
            menu_actions["save_both"].setEnabled(save_idle)
            menu_actions["start_relay"].setEnabled(
                not relay_active and not busy and a_ready and b_ready
                and composer_has_text)
            menu_actions["stop_relay"].setEnabled(relay_active)
        # Run Task: idle only, and only once the task text and both
        # pickers are set. Nothing is pre-selected, ever. The task
        # setup itself can't change mid-task.
        task_active = state.get("task") is not None
        task_ready = (not busy and not relay_active and not task_active
                      and bool(task_edit.toPlainText().strip())
                      and task_worker.currentIndex() >= 0
                      and task_follow_up.currentIndex() >= 0)
        task_run.setEnabled(task_ready)
        for widget in (task_edit, task_worker, task_follow_up):
            widget.setEnabled(not task_active)
        # Rounds means nothing without a follow-up: "Nothing" is
        # always one round, so the box is off until a follow-up that
        # passes work on is picked. It is also locked mid-task, like
        # the rest of the setup.
        follow_up_nothing = (
            task_follow_up.currentIndex() >= 0
            and TASK_FOLLOW_UPS[task_follow_up.currentIndex()]
            == "nothing")
        task_rounds.setEnabled(not task_active and not follow_up_nothing)
        compare_ready = (not busy and not relay_active
                         and len(state.get("compare", {})) == 2)
        for side in ("A", "B"):
            w = widgets[side]
            # The pickers stay disabled while the model list is
            # loading (v0.33): refresh_buttons must not re-enable
            # them mid-load.
            w["picker"].setEnabled(
                not busy and not state.get("models_loading", False))
            w["refresh"].setEnabled(not busy)
            w["stop"].setEnabled(side in state["active"]
                                 or side in state["queued"])
            # No clearing either side mid-relay: the relay is writing
            # into both histories.
            w["clear"].setEnabled(not relay_active
                                  and side not in state["active"]
                                  and side not in state["queued"])
            w["save"].setEnabled(save_idle)
            # Import replaces the pane's history, so it waits for
            # idle for the same reason a save does.
            w["import"].setEnabled(save_idle)
            w["use_this"].setEnabled(compare_ready)
            # Summarize: idle only, a model picked, and something older
            # to fold. While this side is summarizing the button says
            # so (it is disabled by the busy check above).
            summarizing = state.get("summarizing") == side
            w["summarize"].setText(
                "Summarizing..." if summarizing else "Summarize")
            w["summarize"].setEnabled(
                not busy and not relay_active
                and convo.sides[side].model is not None
                and len(convo.sides[side].messages) > SUMMARY_KEEP_LAST)
            # The nudge shows only when summarizing can actually help:
            # over budget AND something older to fold. It never calls
            # the model on its own -- it only shows or hides.
            w["nudge"].setVisible(
                len(convo.sides[side].messages) > SUMMARY_KEEP_LAST
                and convo.sides[side].history_chars()
                > HISTORY_BUDGET_CHARS)
            refresh_history_buttons(side)

    # Current activity state per side ("idle" until something
    # happens). The one source of truth for the light and the words.
    side_state = {"A": "idle", "B": "idle"}

    def set_side_state(side, new_state):
        """Set a side's activity state -- light, activity words, and
        the bottom-row status label, all together, from SIDE_STATES.
        The single setter: every path that moves a side in or out of
        a busy state goes through here, so the light can never get
        stuck showing the wrong thing. A no-op when the state is
        unchanged (on_chunk fires per word). An "error" state holds
        for a few seconds so the failure is seen, then falls back to
        idle -- unless a new job started first, which the state
        check in the timer covers.
        """
        if side_state[side] == new_state:
            return
        side_state[side] = new_state
        color, text = SIDE_STATES[new_state]
        widgets[side]["activity_dot"].setStyleSheet(
            "background-color: %s; border-radius: 6px;" % color)
        widgets[side]["activity"].setText(text)
        widgets[side]["status"].setText(text)
        if new_state == "error":
            error_timers[side].start(ERROR_STATE_HOLD_MS)
        else:
            error_timers[side].stop()

    def _make_error_clear(side):
        def clear_error():
            if side_state[side] == "error":
                set_side_state(side, "idle")
        return clear_error

    # One single-shot timer per side, parented to the window (so
    # they die with it): each fires once, ERROR_STATE_HOLD_MS after
    # a failure, and clears the red light if it is still showing.
    error_timers = {}
    for _side in ("A", "B"):
        _timer = QtCore.QTimer(window)
        _timer.setSingleShot(True)
        _timer.setInterval(ERROR_STATE_HOLD_MS)
        _timer.timeout.connect(_make_error_clear(_side))
        error_timers[_side] = _timer

    # Sparkline sampling (v0.47, R9): one 500 ms timer per side,
    # running only while that side generates. The probe tracks the
    # turn in progress -- characters streamed so far and when the
    # first token landed -- and each tick samples the live rate
    # into the side's sparkline. The timers are parented to the
    # window and stopped on close, like the error timers.
    spark_probe = {"A": {"chars": 0, "t0": None},
                   "B": {"chars": 0, "t0": None}}

    def _make_spark_tick(side):
        def tick():
            probe = spark_probe[side]
            if (side not in state["active"]
                    or probe["t0"] is None):
                return
            rate = live_rate(probe["chars"],
                             time.monotonic() - probe["t0"])
            if rate is not None:
                widgets[side]["sparkline"].add_sample(rate)
        return tick

    spark_timers = {}
    for _side in ("A", "B"):
        _timer = QtCore.QTimer(window)
        _timer.setInterval(SPARKLINE_SAMPLE_MS)
        _timer.timeout.connect(_make_spark_tick(_side))
        spark_timers[_side] = _timer

    def _stop_sparkline(side):
        """Stop a side's sampling timer and clear its probe: the
        turn is over (done, failed, or stopped). The samples stay
        -- they are the recent history, not the turn's."""
        spark_timers[side].stop()
        spark_probe[side]["chars"] = 0
        spark_probe[side]["t0"] = None

    def refresh_stats_label(side):
        widgets[side]["stats"].setText(
            format_stats_line(state["stats"][side]))

    def refresh_session_label(side):
        widgets[side]["session_tokens"].setText(
            format_session_line(state["session_tokens"][side]))

    # --- runner wiring ----------------------------------------------------
    job_stats = {}  # job id -> {"metrics": dict|None, "ram": str|None}

    def _ram_text(model):
        """'<size> in RAM' for a running model, 'not loaded' when it
        isn't running, or None when the lookup itself failed (shown
        as 'RAM n/a')."""
        try:
            running = client.running_models()
        except Exception:
            return None
        if model in running:
            return "%s in RAM" % format_ram(running[model])
        return "not loaded"

    def make_job_fn(job_id, model, messages, track_stats=True):
        holder = {"metrics": None, "ram": None}
        job_stats[job_id] = holder
        # Temperature (v0.19): read when the job is made -- the value
        # in effect when the turn was started -- and sent as Ollama's
        # options. Every generation goes through here: direct sends,
        # relay hops, task runs, and summaries.
        job_side, _is_summary = _job_side(job_id)
        options = {"temperature":
                   widgets[job_side]["temperature"].value()}

        def _on_retry(attempt, delay, kind):
            logger.warning(
                "chat retry: model %s, attempt %d failed (%s), "
                "retrying in %.1fs", model, attempt, kind, delay)

        def fn(emit, cancel_event):
            kwargs = {"on_retry": _on_retry, "options": options}
            if track_stats:
                kwargs["on_metrics"] = (
                    lambda m: holder.update(metrics=m))
            text = client.chat_stream(model, messages, emit, cancel_event,
                                      **kwargs)
            if track_stats:
                holder["ram"] = _ram_text(model)
            return text
        return fn

    def _emit(signal, *args):
        # The runner is a daemon thread and outlives the window: once
        # the window is gone its signal source is deleted and there is
        # nothing left to update. Swallow that instead of killing the
        # thread with a traceback.
        try:
            signal.emit(*args)
        except RuntimeError:
            pass

    runner = SerialRunner(
        on_start=lambda job: _emit(signals.started, job["id"]),
        on_chunk=lambda job, text: _emit(signals.chunk, job["id"], text),
        on_done=lambda job, text: _emit(signals.done, job["id"], text),
        on_error=lambda job, msg: _emit(signals.failed, job["id"], msg),
        on_cancelled=lambda job: _emit(signals.cancelled, job["id"]),
        on_idle=lambda: _emit(signals.idle),
    )

    def _job_side(job_id):
        """(side, is_summarize) for a runner job id.

        Normal turns use the side as the job id ("A"/"B"); summarize
        turns use "sum-A"/"sum-B".
        """
        if job_id.startswith("sum-"):
            return job_id[4:], True
        return job_id, False

    def _take_job_stats(job_id, side):
        """Pop a finished job's holder and return its stats dict.

        The turn's token counts -- when Ollama reported them -- are
        folded into the side's session total first. A job with no
        holder (never tracked) returns None. A failed turn has no
        metrics, so its stats come back all None and nothing is
        added to the total. The caller decides whether the stats
        line itself gets the result: summaries count toward the
        total but never overwrite the last real turn's line.
        """
        holder = job_stats.pop(job_id, None)
        if holder is None:
            return None
        stats = compute_turn_stats(holder.get("metrics") or {})
        stats["ram"] = holder.get("ram")
        totals = state["session_tokens"][side]
        if stats["eval_count"] is not None:
            totals["out"] += stats["eval_count"]
        if stats["prompt_eval_count"] is not None:
            totals["in"] += stats["prompt_eval_count"]
        refresh_session_label(side)
        return stats

    def on_started(job_id):
        if not alive():
            return
        side, is_summary = _job_side(job_id)
        if is_summary:
            # A summarize turn streams nothing into the transcript: the
            # summary lands as one entry when it finishes. The side is
            # deliberately NOT added to active/queued -- the per-side
            # Stop button cancels job id == side, which would poison
            # the next real turn for this side.
            set_side_state(side, "summarizing")
            refresh_buttons()
            return
        state["queued"].discard(side)
        state["active"].add(side)
        # The job started but no reply has arrived yet: amber
        # "Waiting for Ollama…" until the first chunk lands, when
        # on_chunk flips it to green "Thinking…". A slow or
        # retrying Ollama honestly stays amber.
        set_side_state(side, "waiting")
        widgets[side]["reply_open"] = True
        begin_reply(side, convo.sides[side].model)
        refresh_buttons()

    def on_chunk(job_id, text):
        if not alive():
            return
        side, is_summary = _job_side(job_id)
        if is_summary:
            return
        # The first chunk is the moment Ollama starts answering:
        # amber "Waiting for Ollama…" becomes green "Thinking…".
        if side_state[side] == "waiting":
            set_side_state(side, "thinking")
            # ...and the sparkline starts sampling: the first
            # token's arrival time anchors the live rate. Only
            # while this side is still the active turn -- a chunk
            # that lands after a stop must not restart it.
            if side in state["active"]:
                spark_probe[side]["chars"] = len(text)
                spark_probe[side]["t0"] = time.monotonic()
                spark_timers[side].start()
        elif spark_probe[side]["t0"] is not None:
            spark_probe[side]["chars"] += len(text)
        append_text(side, text)

    def finish_reply(side):
        if widgets[side]["reply_open"]:
            append_text(side, "\n")
            widgets[side]["reply_open"] = False

    def end_relay(reason, note_side=None):
        """End the active relay, showing why in the status line.

        note_side (optional) also gets a "(Relay stopped: ...)" line in
        its pane, so the stop is visible where the conversation ended.
        Any waiting hop review is dismissed with the relay.
        """
        session = state.get("relay")
        if session is None:
            return
        session.active = False
        # Reasons that are full sentences (the Ollama messages) already
        # end with a period; the status line adds its own, so strip
        # one to avoid "Try again in a moment..".
        reason = reason.rstrip(".")
        session.stop_reason = reason
        state["relay"] = None
        state["pending_hop"] = None
        review_frame.setVisible(False)
        logger.action("relay ended after %d turn(s): %s"
                      % (session.turns_done, reason))
        # A side left waiting on the relay -- a hop parked for
        # review, or queued for a hop that will never run -- goes
        # back to idle. A side still generating keeps its light
        # (its own done/cancelled handler settles it), and a side
        # showing red keeps it (its error timer settles it).
        for side in ("A", "B"):
            if (side not in state["active"]
                    and side_state[side] != "error"):
                set_side_state(side, "idle")
        # If nothing is still generating or queued (e.g. the relay ended
        # from a review pause, where no job runs), the window is idle
        # again -- no on_idle signal will come to say so.
        if not state["active"] and not state["queued"]:
            state["busy"] = False
        # The chip holds the end reason until the next relay starts,
        # exactly as the old label did -- no timeout, nothing hidden.
        set_relay_chip("ended", "Relay stopped: %s." % reason)
        if note_side is not None:
            append_note(note_side, "(Relay stopped: %s.)" % reason)
        refresh_buttons()

    def forward_hop(session, from_side, framed):
        """Relay one reply to the other side and start its turn.

        ``framed`` is exactly what the other model receives as a plain
        user message; the window shows the same text with a [relay] tag,
        which the models never see.
        """
        next_side = RelaySession.other(from_side)
        convo.sides[next_side].add_user(
            framed, label="You [relay from Model %s]" % from_side,
            source_side=from_side)
        append_message(next_side,
                       "You [relay from Model %s]" % from_side, framed,
                       color=SIDE_COLORS[from_side])
        log_event(next_side, "You [relay from Model %s]" % from_side,
                  framed)
        refresh_history_list(next_side)
        session.current_side = next_side
        session.hop_from = from_side
        state["queued"].add(next_side)
        set_side_state(next_side, "queued")
        set_relay_chip("running", session.status_running())
        runner.submit(next_side, make_job_fn(
            next_side, convo.sides[next_side].model,
            convo.sides[next_side].messages_for_model()))

    def skip_hop(session, from_side):
        """Skip a reviewed hop: the reply is not forwarded, and the
        relay advances to the other side's turn (v0.53, P1-1).

        The skipped reply stays in the speaker's pane -- it did say
        it -- but never reaches the other model. The other side
        generates from its own history as it stands, which ends in
        its own last reply: it picks up its own thread, and the
        relay keeps flowing with the skipped hop dropped from the
        exchange. The skipped turn still counts toward the cap --
        it ran, tokens and all.

        The first hop is the one exception: the other side has no
        history yet, so there is nothing for it to answer. It takes
        over as the first speaker instead and answers the seed, as
        if the relay had started on it.
        """
        next_side = RelaySession.other(from_side)
        next_obj = convo.sides[next_side]
        if not next_obj.messages and not session.seed:
            # Nothing to answer and no seed to hand over: the relay
            # cannot continue. Unreachable from the window -- a
            # relay always starts with a seed -- but never submit an
            # empty job to a model.
            end_relay("hop skipped; Model %s has nothing to answer"
                      % next_side)
            return
        append_note(next_side,
                    "(Hop from Model %s skipped.)" % from_side)
        if not next_obj.messages:
            next_obj.add_user(session.seed,
                              display=session.seed_display)
            shown = session.seed_display or session.seed
            append_message(next_side, "You", shown)
            log_event(next_side, "You", shown)
            refresh_history_list(next_side)
        logger.action("relay hop skipped: Model %s to Model %s"
                      % (from_side, next_side))
        session.current_side = next_side
        session.hop_from = from_side
        state["queued"].add(next_side)
        set_side_state(next_side, "queued")
        set_relay_chip("running", session.status_running())
        runner.submit(next_side, make_job_fn(
            next_side, next_obj.model,
            next_obj.messages_for_model()))

    def end_task(status_message=None):
        """End the active task. The statusbar says how it ended."""
        if state.get("task") is None:
            return
        state["task"].active = False
        state["task"] = None
        logger.action("task ended: %s" % (status_message or "done"))
        if status_message:
            set_status_message(status_message)
        refresh_buttons()

    def show_task_progress(session):
        """The status bar names the round a task chain is on. A
        one-round task shows nothing -- it is over before a progress
        line would mean anything."""
        if session.rounds > 1:
            set_status_message(
                "Task: round %d of %d..."
                % (session.rounds_done + 1, session.rounds))

    def forward_task_result(session, result, from_side):
        """Hand the latest result to the next side and start its turn.

        ``result`` is framed for the next round (review, revise, or
        continuation); the window shows a short handoff note, and the
        framed text is what the next model receives. Returns False
        when the follow-up is "nothing" (no further rounds).
        """
        framed = session.frame_next(result)
        if framed is None:
            return False
        next_side = session.next_side
        note = session.handoff_note_next(from_side)
        convo.sides[next_side].add_user(
            framed, label="You [task handoff]",
            display=note, source_side=from_side)
        append_message(next_side, "You [task handoff]", note,
                       color=SIDE_COLORS[from_side])
        log_event(next_side, "You [task handoff]", note)
        refresh_history_list(next_side)
        session.phase = "followup"
        state["queued"].add(next_side)
        set_side_state(next_side, "queued")
        show_task_progress(session)
        runner.submit(next_side, make_job_fn(
            next_side, convo.sides[next_side].model,
            convo.sides[next_side].messages_for_model()))
        return True

    def pause_for_review(session, from_side, reply_text):
        """Park the relay on a hop for the user to review and edit.

        The relay stays active (so the window stays busy and no other
        send can start), but no job runs until the user forwards,
        skips, or stops from the review box.
        """
        next_side = RelaySession.other(from_side)
        framed = session.frame(reply_text, from_side)
        state["pending_hop"] = {"session": session, "from_side": from_side,
                                "framed": framed}
        review_label.setText(
            "Review this hop -- Model %s to Model %s (%s):"
            % (from_side, next_side, session.mode))
        review_edit.setPlainText(framed)
        review_frame.setVisible(True)
        set_relay_chip("review",
                       "Relay paused for review: turn %d/%d."
                       % (session.turns_done + 1, session.turn_cap))
        # The receiving side waits on the user now: amber, "Waiting
        # for you to review…". Forwarding or skipping flips it to
        # queued; stopping ends the relay, which clears it.
        set_side_state(next_side, "review")
        refresh_buttons()

    def do_forward(text):
        """Forward a reviewed hop with the given text and resume relaying."""
        hop = state.get("pending_hop")
        if hop is None:
            return
        state["pending_hop"] = None
        review_frame.setVisible(False)
        session = hop["session"]
        if session is None or not session.active:
            refresh_buttons()
            return
        forward_hop(session, hop["from_side"], text)
        refresh_buttons()

    def finish_summarize(side, text):
        """Apply a summarize turn: fold the older history into the
        summary entry. An empty summary changes nothing."""
        state["summarizing"] = None
        set_side_state(side, "idle")
        if not text.strip():
            set_status_message(
                "Summarize came back empty. History unchanged.")
            refresh_buttons()
            return
        if convo.sides[side].summarize(text):
            render_transcript(side)
            refresh_history_list(side)
            set_status_message("History summarized.")
        refresh_buttons()

    def on_done(job_id, text):
        if not alive():
            return
        side, is_summary = _job_side(job_id)
        if is_summary:
            # A summarize turn counts toward the session token total
            # but never overwrites the last real turn's stats line.
            _take_job_stats(job_id, side)
            finish_summarize(side, text)
            return
        finish_reply(side)
        label = convo.sides[side].add_assistant(text)
        # The turn finished: the streamed plain text becomes the
        # Markdown rendering (before any relay/task note lands).
        render_finished_reply(side, text)
        log_event(side, label, text)
        refresh_history_list(side)
        state["active"].discard(side)
        _stop_sparkline(side)
        set_side_state(side, "idle")
        # The turn finished: the dot flashes lime for 200 ms (v0.45,
        # R7) -- a finish moment, not just the light going grey.
        motion.flash(side)
        # Stats for this turn (every turn, relay turns included).
        stats = _take_job_stats(job_id, side)
        if stats is not None:
            state["stats"][side] = stats
            refresh_stats_label(side)
            # ...and the sparkline's final sample is the turn's
            # official tok/s -- the same number the stats line
            # shows, from the done chunk, never estimated.
            if stats["tok_per_sec"] is not None:
                widgets[side]["sparkline"].add_sample(
                    stats["tok_per_sec"])
            logger.info(
                "turn done: side %s, model %s, %d chars, %s"
                % (side, convo.sides[side].model, len(text),
                   format_stats_line(stats)))
        session = state.get("relay")
        task = state.get("task")
        if session is not None and session.active:
            session.turns_done += 1
            logger.info("relay turn %d/%d done on side %s"
                        % (session.turns_done, session.turn_cap, side))
            reason = session.check_stop(text)
            if reason is not None:
                end_relay(reason, note_side=side)
            elif review_check.isChecked():
                pause_for_review(session, side, text)
            else:
                forward_hop(session, side, session.frame(text, side))
        elif task is not None and task.active:
            task.rounds_done += 1
            task.phase = "followup"
            if task.rounds_done >= task.rounds:
                # The chain ran its rounds: the task is over.
                end_task("Task done.")
            elif not forward_task_result(task, text, side):
                # No further round (a "nothing" follow-up, which is
                # always one round anyway): the task is over.
                end_task("Task done.")
        elif state.get("compare_round"):
            # A Send to Both round: remember this side's reply so the
            # "Use this" buttons can offer it once both sides land.
            state["compare"][side] = text
        refresh_buttons()

    def on_failed(job_id, message):
        if not alive():
            return
        side, is_summary = _job_side(job_id)
        _stop_sparkline(side)
        if is_summary:
            state["summarizing"] = None
            # A failed summarize contributes nothing -- no counts,
            # and the stats line is not a summarize turn's to touch.
            job_stats.pop(job_id, None)
            append_note(side, "(Summarize failed: %s)" % message)
            set_side_state(side, "idle")
            logger.error("summarize failed: side %s: %s" % (side, message))
            refresh_buttons()
            return
        finish_reply(side)
        widgets[side]["reply_body_pos"] = None
        widgets[side]["reply_label_len"] = None
        append_note(side, "(%s)" % message)
        state["active"].discard(side)
        # Red for a few seconds, so the failure is seen, then idle
        # (the error timer in set_side_state handles the fall-back).
        set_side_state(side, "error")
        # The failed turn has no numbers: its stats show n/a, and
        # nothing is added to the session total.
        stats = _take_job_stats(job_id, side)
        if stats is not None:
            state["stats"][side] = stats
            refresh_stats_label(side)
        logger.error("turn failed: side %s, model %s: %s"
                     % (side, convo.sides[side].model, message))
        if state.get("relay") is not None:
            end_relay(message)
        if state.get("task") is not None:
            end_task("Task failed: %s" % message)
        refresh_buttons()

    def on_cancelled(job_id):
        if not alive():
            return
        # A stopped turn contributes nothing -- no stats, no counts.
        job_stats.pop(job_id, None)
        side, is_summary = _job_side(job_id)
        _stop_sparkline(side)
        if is_summary:
            # No button cancels a summarize turn, but stay safe if one
            # ever does: the history is simply left as it was.
            state["summarizing"] = None
            set_side_state(side, "idle")
            refresh_buttons()
            return
        finish_reply(side)
        widgets[side]["reply_body_pos"] = None
        widgets[side]["reply_label_len"] = None
        append_note(side, "(%s)" % MSG_STOPPED)
        state["active"].discard(side)
        state["queued"].discard(side)
        set_side_state(side, "idle")
        if state.get("relay") is not None:
            end_relay(MSG_RELAY_STOPPED_BY_YOU)
        if state.get("task") is not None:
            end_task("Task stopped.")
        refresh_buttons()

    def on_idle():
        if not alive():
            return
        # A relay submits its next job from the done handler, which the
        # GUI processes before this idle signal -- but the runner may
        # still report idle between hops. Stay busy while a relay runs.
        if state.get("relay") is not None:
            refresh_buttons()
            return
        # Same for a task: the follow-up job is submitted from the
        # done handler, so idle can fire between the two turns.
        if state.get("task") is not None:
            refresh_buttons()
            return
        state["busy"] = False
        refresh_buttons()

    signals.started.connect(on_started)
    signals.chunk.connect(on_chunk)
    signals.done.connect(on_done)
    signals.failed.connect(on_failed)
    signals.cancelled.connect(on_cancelled)
    signals.idle.connect(on_idle)

    # --- attachments ------------------------------------------------------
    def refresh_attach_list():
        """Rebuild the attached-files list from state["attachments"].

        Each row is the file name plus an x button that drops that file.
        The frame hides when nothing is attached.
        """
        while attach_layout.count():
            item = attach_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            layout = item.layout()
            if layout is not None:
                while layout.count():
                    sub = layout.takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().deleteLater()
                layout.deleteLater()
        for index, att in enumerate(state["attachments"]):
            row = QtWidgets.QHBoxLayout()
            name = QtWidgets.QLabel(att["name"])
            row.addWidget(name)
            row.addStretch(1)
            drop = QtWidgets.QPushButton("x")
            drop.setFixedWidth(28)
            drop.clicked.connect(
                (lambda i: lambda: remove_attachment(i))(index))
            row.addWidget(drop)
            attach_layout.addLayout(row)
        attach_frame.setVisible(bool(state["attachments"]))
        refresh_buttons()

    def add_attachments(paths):
        """Attach the given files, validating each one.

        Files are read now (text only, 100 KB each, max 5 at once).
        Skipped files get a plain note naming the file; valid ones
        still attach. Returns the number of files added.
        """
        if state["busy"] or state.get("relay") is not None:
            return 0
        added = []
        notes = []
        for path in paths:
            if len(state["attachments"]) >= MAX_ATTACHMENTS:
                notes.append(
                    "You can attach up to %d files at once."
                    % MAX_ATTACHMENTS)
                break
            try:
                name, text = load_attachment(path)
            except AttachmentError as exc:
                notes.append(str(exc))
                continue
            state["attachments"].append({"name": name, "text": text})
            added.append(name)
        refresh_attach_list()
        if notes:
            set_status_message(" ".join(notes))
        elif added:
            set_status_message(
                "Attached: %s." % ", ".join(added))
        return len(added)

    def remove_attachment(index):
        """Drop one attached file by its position in the list."""
        if 0 <= index < len(state["attachments"]):
            del state["attachments"][index]
            refresh_attach_list()

    def clear_attachments():
        """Drop every attachment (after a send).

        The "Attached: ..." note in the status bar is stale once the
        files have gone with the message, so clear it too -- but only
        when files were actually attached, so an unrelated note (a
        saved pair, say) survives an ordinary send.
        """
        had = bool(state["attachments"])
        state["attachments"] = []
        refresh_attach_list()
        if had:
            set_status_message("")

    def on_attach_clicked():
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            window, "Attach files")
        if paths:
            add_attachments(paths)

    # --- transcript save ---------------------------------------------------
    def save_text_to_file(text, default_name):
        """Offer a save dialog and write ``text`` to the picked file.

        Cancelling writes nothing and shows no error. A failed write
        gets a plain note naming the problem -- never a crash, never
        silent -- and the previous file at that path, if any, stays
        intact: the write is atomic. Returns True when the file was
        written.
        """
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            window, "Save transcript", default_name,
            "Text files (*.txt)")
        if not path:
            return False
        try:
            write_text_atomic(path, text)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            set_status_message(
                "Couldn't save to %s: %s" % (path, reason))
            return False
        set_status_message("Saved to %s" % path)
        return True

    def do_save(side):
        """Save one side's transcript to a text file."""
        if state["busy"] or state.get("relay") is not None:
            return
        side_obj = convo.sides[side]
        save_text_to_file(
            format_side_transcript(side_obj),
            default_side_filename(side, side_obj.model))

    def do_import(side):
        """Import a saved single-pane transcript into this pane,
        replacing its conversation. The file is read and parsed
        before anything changes, so a bad file leaves the pane
        untouched.

        The file's model names are display labels only: the pane's
        picked model is NOT changed by an import. The imported
        messages do join the pane's history, so the next send
        includes them in the model's context -- that is the point
        of importing.

        The full-transcript log: the pane's existing entries are
        removed and the imported entries are appended at the end
        in file order. The file has no timestamps, so interleaving
        them with the other side's entries by time is impossible;
        the other side's entries are untouched."""
        if state["busy"] or state.get("relay") is not None:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            window, "Import transcript", "",
            "Text files (*.txt)")
        if not path:
            return
        try:
            entries = load_side_transcript_file(path)
        except (TranscriptError, OSError) as exc:
            set_status_message(
                "Couldn't import that file: %s" % exc)
            return
        side_obj = convo.sides[side]
        if side_obj.messages:
            answer = QtWidgets.QMessageBox.question(
                window, "Import transcript?",
                "Importing replaces the conversation in this pane.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return
        # Importing counts as using the app (v0.39, R5): the
        # first-run guide never shows again.
        mark_first_run_done()
        side_obj.messages = [ChatMessage(entry["role"], entry["content"],
                                         entry["label"])
                             for entry in entries]
        state["full_log"] = [entry for entry in state["full_log"]
                             if entry[0] != side]
        for entry in entries:
            # Imported content IS the display text: exports carry
            # what the window showed, so content and display agree.
            # Through log_event, like every other append: the cap
            # covers imports too.
            log_event(side, entry["label"], entry["content"])
        render_transcript(side)
        refresh_history_list(side)
        # A replaced conversation ends any compare offer and strands
        # the last turn's stats, like a clear does.
        state["compare"] = {}
        state["compare_round"] = False
        state["stats"][side] = None
        refresh_stats_label(side)
        # ...and the sparkline restarts with the stats (v0.47, R9):
        # its samples described the old conversation's turns.
        widgets[side]["sparkline"].clear()
        set_side_state(side, "idle")
        refresh_buttons()
        set_status_message(
            "Transcript imported from %s" % path)

    def do_save_both():
        """Save both transcripts to one combined text file."""
        if state["busy"] or state.get("relay") is not None:
            return
        save_text_to_file(
            format_both_transcript(convo),
            default_both_filename())

    def do_save_full():
        """Save the full transcript: both sides interleaved in the
        order the conversation actually happened."""
        if state["busy"] or state.get("relay") is not None:
            return
        save_text_to_file(
            format_full_transcript(
                state["full_log"],
                trimmed=state["full_log_trimmed"]),
            default_full_filename())

    # --- conversation save/load (v0.17) -----------------------------------
    def do_save_conversation():
        """Save the whole conversation to a JSON file: both sides'
        histories with their labels, both models, both instruction
        fields, and the interleaved full-transcript log. Written
        atomically, so a crash mid-save leaves no half-written file."""
        if state["busy"] or state.get("relay") is not None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            window, "Save conversation", default_conversation_filename(),
            "DUO conversation files (*.json)")
        if not path:
            return
        try:
            save_conversation_file(path, convo, state["full_log"])
        except OSError as exc:
            reason = exc.strerror or str(exc)
            set_status_message(
                "Couldn't save to %s: %s" % (path, reason))
            return
        set_status_message("Conversation saved to %s" % path)

    def _apply_conversation(new_convo, new_log):
        """Swap the loaded conversation into the window: both panes'
        histories, labels, instructions, and models, plus the
        full-transcript log. Models that are no longer installed stay
        unpicked -- the picker is left blank for that side. Returns
        the list of missing model names."""
        # Opening a conversation counts as using the app (v0.39,
        # R5): the first-run guide never shows again, even if the
        # loaded conversation is empty.
        mark_first_run_done()
        missing = []
        for side in ("A", "B"):
            new_side = new_convo.sides[side]
            side_obj = convo.sides[side]
            side_obj.messages = list(new_side.messages)
            side_obj.instruction = new_side.instruction
            widgets[side]["instr_edit"].setPlainText(new_side.instruction)
            # The model is set explicitly, not through the picker's
            # signal: re-picking the model that is already selected
            # fires no change, and the load must not depend on one.
            picker = widgets[side]["picker"]
            model = new_side.model
            # The picker shows "name - size - fit"; the plain name
            # is the item data (v0.33), so look it up by data.
            if model and picker.findData(model) >= 0:
                picker.setCurrentIndex(picker.findData(model))
                side_obj.model = model
            else:
                picker.setCurrentIndex(-1)
                side_obj.model = None
                if model:
                    missing.append(model)
            render_transcript(side)
            refresh_history_list(side)
            # The loaded conversation has no turn stats yet.
            state["stats"][side] = None
            refresh_stats_label(side)
            # ...and no sparkline history either: the samples are
            # live measurement, not conversation content, so the
            # file cannot restore them (v0.47, R9).
            widgets[side]["sparkline"].clear()
            set_side_state(side, "idle")
        state["full_log"] = list(new_log)
        # The loaded log is complete by definition -- it came from
        # the file -- so any earlier trimming no longer applies.
        state["full_log_trimmed"] = False
        # A loaded conversation ends any compare offer: the replies
        # it refers to are gone.
        state["compare"] = {}
        state["compare_round"] = False
        refresh_buttons()
        return missing

    def do_open_conversation():
        """Load a saved conversation back into the window. The file
        is read and validated before anything changes, so a bad file
        leaves the current conversation untouched; and opening
        replaces the conversation on screen, so it asks first when
        either pane already has messages."""
        if state["busy"] or state.get("relay") is not None:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            window, "Open conversation", "",
            "DUO conversation files (*.json)")
        if not path:
            return
        try:
            new_convo, new_log = load_conversation_file(path)
        except (ConversationError, OSError) as exc:
            set_status_message(
                "Couldn't open that file: %s" % exc)
            return
        if convo.sides["A"].messages or convo.sides["B"].messages:
            answer = QtWidgets.QMessageBox.question(
                window, "Open conversation?",
                "Opening a conversation replaces the one on screen.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return
        missing = _apply_conversation(new_convo, new_log)
        if missing:
            set_status_message(
                "Conversation opened, but not installed: %s."
                % ", ".join(missing))
        else:
            set_status_message(
                "Conversation opened from %s" % path)

    save_convo.clicked.connect(do_save_conversation)
    open_convo.clicked.connect(do_open_conversation)

    # --- debug report ------------------------------------------------------
    def do_debug_report():
        """Write the debug report to a file the user picks: app
        version, Ollama status, the session log, and recent errors.
        The report never holds message or attached-file contents, so
        it is safe to email. Cancelling writes nothing."""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            window, "Save debug report", default_debug_filename(),
            "Text files (*.txt)")
        if not path:
            return
        try:
            written = logger.generate_report(path)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            set_status_message(
                "Couldn't save to %s: %s" % (path, reason))
            return
        logger.action("debug report written to %s" % written)
        set_status_message("Debug report saved to %s" % written)

    debug_btn.clicked.connect(do_debug_report)

    attach_btn.clicked.connect(on_attach_clicked)

    # --- sends ------------------------------------------------------------
    def send_message(text, action, attachments=()):
        """Send `text` to side A, B, or Both. Returns None on success
        or a plain error string. The desktop buttons call it through
        do_send (with the pending attachments); the phone deck calls
        it through the phone_send signal (no attachments -- a phone
        send must never pick up files attached on the desktop)."""
        text = text.strip()
        if not text:
            return MSG_EMPTY
        try:
            targets = convo.targets_for(action)
        except NoModelSelected as exc:
            return str(exc)
        # The first message ends the first run (v0.39, R5): the
        # guide leaves both panes before the message lands.
        mark_first_run_done()
        # Attached files ride along with this message: their contents
        # are prepended as plain blocks the model can read. The window
        # shows the typed message plus a short tag, not the contents.
        full = build_attached_message(
            [(a["name"], a["text"]) for a in attachments], text)
        display = None
        if attachments:
            display = "%s\n%s" % (
                text, attachment_tag([a["name"] for a in attachments]))
        state["busy"] = True
        # A new send starts a new round: any earlier compare offer ends.
        state["compare"] = {}
        state["compare_round"] = (action == "Both")
        for side_obj in targets:
            side = side_obj.name
            side_obj.add_user(full, display=display)
            shown = display if display else text
            append_message(side, "You", shown)
            log_event(side, "You", shown)
            refresh_history_list(side)
            state["queued"].add(side)
            set_side_state(side, "queued")
            logger.action(
                "send to %s: model %s, message %d chars, %d attachment(s)"
                % (side, side_obj.model, len(text), len(attachments)))
            runner.submit(side, make_job_fn(
                side, side_obj.model, side_obj.messages_for_model()))
        refresh_buttons()
        return None

    def do_send(action):
        attachments = list(state["attachments"])
        err = send_message(composer.toPlainText(), action, attachments)
        if err is not None:
            set_status_message(
                err)
            return
        composer.clear()
        clear_attachments()
        composer.setFocus()

    send_a.clicked.connect(lambda: do_send("A"))
    send_b.clicked.connect(lambda: do_send("B"))
    send_both.clicked.connect(lambda: do_send("Both"))
    # Send buttons follow the composer (v0.43, T21): typing or
    # clearing text re-runs the enablement funnel at once.
    composer.textChanged.connect(refresh_buttons)

    # --- keyboard send ----------------------------------------------------
    # Ctrl+Enter sends to both models, from the keyboard. With the
    # "Enter to send" box ticked, plain Enter sends too (Shift+Enter
    # still adds a new line). A send key is always swallowed -- it
    # never inserts a newline -- and it only fires while the Send to
    # Both button is enabled (idle, both models picked), so the
    # keyboard path has exactly the button's guards. Empty composer:
    # the button is disabled (v0.43, T21), so the keypress is
    # swallowed and sends nothing. send_message still rejects
    # empty text itself -- that check is the phone deck's backstop.
    class _ComposerKeys(QtCore.QObject):
        def eventFilter(self, obj, event):
            if event.type() == QtCore.QEvent.KeyPress:
                key = event.key()
                if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                    mods = event.modifiers()
                    send = bool(mods & QtCore.Qt.ControlModifier)
                    if not send and enter_send_check.isChecked():
                        send = not bool(mods & QtCore.Qt.ShiftModifier)
                    if send:
                        if send_both.isEnabled():
                            do_send("Both")
                        return True
            return super().eventFilter(obj, event)

    composer.installEventFilter(_ComposerKeys(composer))
    save_both.clicked.connect(do_save_both)
    save_full.clicked.connect(do_save_full)

    # --- summarize --------------------------------------------------------
    def do_summarize(side):
        """Ask this side's own model to summarize its conversation,
        then fold the older turns into the summary entry. One extra
        model turn, run through the same one-at-a-time runner; the
        summary request itself never enters the history."""
        if state["busy"] or state.get("relay") is not None:
            return
        side_obj = convo.sides[side]
        if side_obj.model is None:
            set_status_message(
                MSG_NO_MODEL.format(side="Model " + side))
            return
        if len(side_obj.messages) <= SUMMARY_KEEP_LAST:
            set_status_message("Nothing to summarize yet.")
            return
        state["busy"] = True
        state["summarizing"] = side
        # A summarize turn is not a compare round: the offer is spent.
        state["compare"] = {}
        state["compare_round"] = False
        messages = side_obj.messages_for_model() + [
            {"role": "user", "content": SUMMARIZE_PROMPT}]
        set_side_state(side, "summarizing")
        # Summaries track stats so their tokens count toward the
        # session total (v0.20); on_done never lets them touch the
        # stats line itself.
        runner.submit("sum-" + side, make_job_fn(
            "sum-" + side, side_obj.model, messages, track_stats=True))
        refresh_buttons()

    # --- relay ------------------------------------------------------------
    def do_start_relay():
        if state.get("relay") is not None or state["busy"]:
            return
        text = composer.toPlainText().strip()
        if not text:
            set_status_message(MSG_EMPTY)
            return
        if (convo.sides["A"].model is None
                or convo.sides["B"].model is None):
            set_status_message(MSG_RELAY_NEEDS_BOTH)
            return
        # The seed is a first message too (v0.39, R5): the guide
        # leaves both panes before it lands.
        mark_first_run_done()
        start_side = start_picker.currentText()
        session = RelaySession(
            start_side=start_side,
            turn_cap=cap_spin.value(),
            mode=("aware" if mode_picker.currentText() == "Aware"
                  else "blind"),
            stop_phrases=parse_stop_phrases(stop_edit.text()),
            stop_on_agreement=agree_check.isChecked(),
        )
        state["relay"] = session
        state["busy"] = True
        # A relay is not a compare round.
        state["compare"] = {}
        state["compare_round"] = False
        # Attached files ride with the seed message only -- relay hops
        # are model text and never re-attach.
        attachments = list(state["attachments"])
        full = build_attached_message(
            [(a["name"], a["text"]) for a in attachments], text)
        logger.action(
            "relay started: start %s, cap %d, mode %s, seed %d chars, "
            "%d attachment(s)"
            % (start_side, session.turn_cap, session.mode, len(text),
               len(attachments)))
        display = None
        if attachments:
            display = "%s\n%s" % (
                text, attachment_tag([a["name"] for a in attachments]))
        # The seed lives on the session so a skipped first hop can
        # hand it to the other side (v0.53, P1-1).
        session.seed = full
        session.seed_display = display
        composer.clear()
        clear_attachments()
        side_obj = convo.sides[start_side]
        side_obj.add_user(full, display=display)
        shown = display if display else text
        append_message(start_side, "You", shown)
        log_event(start_side, "You", shown)
        refresh_history_list(start_side)
        state["queued"].add(start_side)
        set_side_state(start_side, "queued")
        set_relay_chip("running", session.status_running())
        runner.submit(start_side, make_job_fn(
            start_side, side_obj.model, side_obj.messages_for_model()))
        refresh_buttons()
        composer.setFocus()

    def stop_relay():
        session = state.get("relay")
        if session is None or not session.active:
            return
        current = session.current_side
        end_relay(MSG_RELAY_STOPPED_BY_YOU)
        # Cancel the in-flight job, if there is one. Its cancel lands in
        # on_cancelled, which finds no active relay and just reports
        # "(Stopped.)". During a review pause no job runs, and
        # cancelling a phantom id would poison the next job submitted
        # for that side.
        if current in state["active"] or current in state["queued"]:
            runner.cancel(current)

    def on_relay_button():
        if state.get("relay") is not None:
            stop_relay()
        else:
            do_start_relay()

    relay_btn.clicked.connect(on_relay_button)
    # The chip's Stop is the same action as the relay row's Stop
    # button: it ends the relay through stop_relay, so the end line
    # reads "Relay stopped: stopped by you." either way. It only
    # shows while a relay runs (set_relay_chip owns that).
    relay_chip_stop.clicked.connect(stop_relay)

    # --- tasks ------------------------------------------------------------
    def do_run_task():
        """Run one task: the worker answers, then the work passes
        back and forth for the rounds picked -- the other side
        reviews or continues, and a review round hands the review
        back to the worker to revise from. "Nothing" is one round."""
        if (state["busy"] or state.get("relay") is not None
                or state.get("task") is not None):
            return
        text = task_edit.toPlainText().strip()
        if not text:
            set_status_message(MSG_TASK_EMPTY)
            return
        if task_worker.currentIndex() < 0:
            set_status_message(MSG_TASK_NEEDS_WORKER)
            return
        if task_follow_up.currentIndex() < 0:
            set_status_message(MSG_TASK_NEEDS_FOLLOW_UP)
            return
        worker = "A" if task_worker.currentIndex() == 0 else "B"
        follow_up = TASK_FOLLOW_UPS[task_follow_up.currentIndex()]
        rounds = task_rounds.value()
        worker_obj = convo.sides[worker]
        if worker_obj.model is None:
            set_status_message(
                MSG_NO_MODEL.format(side="Model " + worker))
            return
        other = RelaySession.other(worker)
        if follow_up != "nothing" and convo.sides[other].model is None:
            set_status_message(
                MSG_NO_MODEL.format(side="Model " + other))
            return
        # A task is a first message too (v0.39, R5): the guide
        # leaves both panes before it lands.
        mark_first_run_done()
        state["busy"] = True
        # A task is not a compare round: the offer is spent.
        state["compare"] = {}
        state["compare_round"] = False
        state["task"] = TaskSession(worker, follow_up, rounds)
        logger.action(
            "task started: worker %s, follow-up %s, %d rounds, "
            "task %d chars"
            % (worker, follow_up, state["task"].rounds, len(text)))
        # Attachments never ride on tasks -- tasks are text-only, and
        # the attached files stay put for the next normal send.
        task_edit.clear()
        worker_obj.add_user(text, label="You [task]")
        append_message(worker, "You [task]", text)
        log_event(worker, "You [task]", text)
        refresh_history_list(worker)
        state["queued"].add(worker)
        set_side_state(worker, "queued")
        show_task_progress(state["task"])
        runner.submit(worker, make_job_fn(
            worker, worker_obj.model, worker_obj.messages_for_model()))
        refresh_buttons()

    task_run.clicked.connect(do_run_task)
    task_edit.textChanged.connect(refresh_buttons)
    task_worker.currentIndexChanged.connect(refresh_buttons)
    task_follow_up.currentIndexChanged.connect(refresh_buttons)

    # --- hop review --------------------------------------------------------
    # Forward sends exactly what is in the review box (v0.54, P1-2):
    # it used to forward the original text and silently throw away
    # any edit, with a second button ("Forward edited") for the
    # edited text. One button, one behavior -- what you see is what
    # the other model gets. An empty box forwards nothing.
    def on_review_forward():
        text = review_edit.toPlainText()
        if not text.strip():
            set_status_message(MSG_REVIEW_EMPTY)
            return
        do_forward(text)

    def on_review_skip():
        hop = state.get("pending_hop")
        if hop is None:
            return
        state["pending_hop"] = None
        review_frame.setVisible(False)
        session = hop["session"]
        if session is None or not session.active:
            refresh_buttons()
            return
        skip_hop(session, hop["from_side"])
        refresh_buttons()

    review_forward.clicked.connect(on_review_forward)
    review_skip.clicked.connect(on_review_skip)
    review_stop.clicked.connect(on_relay_button)

    # --- compare mode -------------------------------------------------------
    def use_this(side):
        """Continue from this side's reply: send it to the other side.

        Only offered after a Send to Both round, once both replies are
        in. The other side gets "You picked this answer: <reply>" as a
        user message and answers it; the picked pane is marked.
        """
        if state.get("busy") or state.get("relay") is not None:
            return
        text = state.get("compare", {}).get(side)
        if text is None:
            return
        other = RelaySession.other(side)
        if convo.sides[other].model is None:
            return
        picked = COMPARE_PREFIX % text
        state["compare"] = {}
        state["compare_round"] = False
        state["busy"] = True
        convo.sides[other].add_user(picked)
        append_message(other, "You", picked)
        log_event(other, "You", picked)
        refresh_history_list(other)
        append_note(side, "(Picked for Model %s.)" % other)
        state["queued"].add(other)
        set_side_state(other, "queued")
        runner.submit(other, make_job_fn(
            other, convo.sides[other].model,
            convo.sides[other].messages_for_model()))
        refresh_buttons()

    # --- history editor -----------------------------------------------------
    def make_hist_move(side, direction):
        def move():
            if state["busy"] or state.get("relay") is not None:
                return
            lst = widgets[side]["hist_list"]
            row = lst.currentRow()
            if row < 0:
                return
            side_obj = convo.sides[side]
            if direction < 0:
                side_obj.move_up(row)
                new_row = max(0, row - 1)
            else:
                side_obj.move_down(row)
                new_row = min(len(side_obj.messages) - 1, row + 1)
            render_transcript(side)
            refresh_history_list(side)
            lst.setCurrentRow(new_row)
            refresh_buttons()
        return move

    def make_hist_delete(side):
        def delete():
            if state["busy"] or state.get("relay") is not None:
                return
            lst = widgets[side]["hist_list"]
            row = lst.currentRow()
            if row < 0:
                return
            # P1-4 (v0.56): deleting a message is irreversible,
            # like Clear -- ask first, defaulting to No.
            answer = QtWidgets.QMessageBox.question(
                window, "Delete message?",
                "Delete this message from Model %s? This can't be "
                "undone." % side,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                return
            convo.sides[side].delete_at(row)
            render_transcript(side)
            refresh_history_list(side)
            if convo.sides[side].messages:
                lst.setCurrentRow(
                    min(row, len(convo.sides[side].messages) - 1))
            refresh_buttons()
        return delete

    def make_instr_toggle(side):
        def toggled(checked):
            widgets[side]["instr_edit"].setVisible(checked)
            widgets[side]["instr_explain"].setVisible(checked)
            widgets[side]["instr_toggle"].setText(
                "Instructions ▾" if checked else "Instructions ▸")
        return toggled

    def restore_hist_split(side):
        """Put a pane's history divider back where it was: the saved
        split, or the 110 px default when nothing is saved."""
        split = widgets[side]["hist_split"]
        saved = prefs.value(SETTING_HIST_SPLITTER_STATE % side)
        if saved is None or not split.restoreState(saved):
            split.setSizes([200, HIST_EDITOR_DEFAULT_HEIGHT])

    def make_hist_toggle(side):
        def toggled(checked):
            if not checked:
                # Save before hiding: a hidden child saves as 0.
                prefs.setValue(
                    SETTING_HIST_SPLITTER_STATE % side,
                    widgets[side]["hist_split"].saveState())
            widgets[side]["hist_frame"].setVisible(checked)
            widgets[side]["hist_toggle"].setText(
                "History ▾" if checked else "History ▸")
            if checked:
                refresh_history_list(side)
                restore_hist_split(side)
        return toggled

    def make_instr_changed(side):
        def changed():
            convo.sides[side].instruction = (
                widgets[side]["instr_edit"].toPlainText())
        return changed

    for side in ("A", "B"):
        w = widgets[side]
        w["use_this"].clicked.connect(
            (lambda s: lambda: use_this(s))(side))
        w["hist_up"].clicked.connect(make_hist_move(side, -1))
        w["hist_down"].clicked.connect(make_hist_move(side, +1))
        w["hist_delete"].clicked.connect(make_hist_delete(side))
        w["hist_list"].currentRowChanged.connect(
            (lambda s: lambda *a: refresh_history_buttons(s))(side))
        w["instr_toggle"].toggled.connect(make_instr_toggle(side))
        w["hist_toggle"].toggled.connect(make_hist_toggle(side))
        w["instr_edit"].textChanged.connect(make_instr_changed(side))
        w["search_edit"].textChanged.connect(
            (lambda s: lambda *a: run_search(s))(side))
        w["search_prev"].clicked.connect(
            (lambda s: lambda: step_search(s, -1))(side))
        w["search_next"].clicked.connect(
            (lambda s: lambda: step_search(s, +1))(side))

    # --- saved pairs + relay presets --------------------------------------
    # v0.56 (P1-12): the dropdown handlers live in duo_pairs now
    # (PairPresetController) -- same eight handlers, same wiring,
    # constructed here with the widgets they touch.
    pair_presets = PairPresetController(
        window, convo, widgets, set_status_message, refresh_buttons,
        pair_combo=pair_combo, pair_name=pair_name,
        pair_save=pair_save, pair_delete=pair_delete,
        preset_combo=preset_combo, preset_name=preset_name,
        preset_save=preset_save, preset_delete=preset_delete,
        start_picker=start_picker, mode_picker=mode_picker,
        cap_spin=cap_spin, stop_edit=stop_edit,
        agree_check=agree_check, review_check=review_check)
    pair_presets.wire()
    refresh_pair_combo = pair_presets.refresh_pair_combo
    refresh_preset_combo = pair_presets.refresh_preset_combo
    on_pair_delete = pair_presets.on_pair_delete
    on_preset_delete = pair_presets.on_preset_delete

    # --- per-side buttons --------------------------------------------------
    def make_stop(side):
        def stop():
            runner.cancel(side)
        return stop

    def make_summarize(side):
        def summarize():
            do_summarize(side)
        return summarize

    def make_clear(side):
        def clear():
            side_obj = convo.sides[side]
            if side_obj.messages:
                # One misclick used to wipe a conversation with no way
                # back. Ask first, and say how much is at stake. An
                # empty pane has nothing to lose, so it clears
                # without asking.
                count = len(side_obj.messages)
                noun = "message" if count == 1 else "messages"
                answer = QtWidgets.QMessageBox.question(
                    window, "Clear Model %s?" % side,
                    "This clears %d %s from Model %s. Keep going?"
                    % (count, noun, side),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No)
                if answer != QtWidgets.QMessageBox.Yes:
                    return
            convo.sides[side].clear()
            widgets[side]["transcript"].clear()
            widgets[side]["reply_body_pos"] = None
            widgets[side]["reply_label_len"] = None
            widgets[side]["guide_shown"] = False
            clear_search(side)
            # A cleared conversation restarts: that side's entries
            # leave the full transcript too.
            state["full_log"] = [entry for entry in state["full_log"]
                                 if entry[0] != side]
            # An emptied log has nothing trimmed left to report.
            if not state["full_log"]:
                state["full_log_trimmed"] = False
            refresh_history_list(side)
            # A cleared side ends any compare offer: the pair is broken.
            state["compare"] = {}
            state["compare_round"] = False
            # ...and its last turn's stats no longer describe anything.
            state["stats"][side] = None
            refresh_stats_label(side)
            # ...and its sparkline restarts too (v0.47, R9).
            widgets[side]["sparkline"].clear()
            set_side_state(side, "idle")
            refresh_buttons()
            # An emptied pane on a first run shows the guide again
            # (v0.39, R5); after the first run it stays the bare
            # placeholder.
            refresh_empty_state(side)
        return clear

    def make_save(side):
        def save():
            do_save(side)
        return save

    def make_import(side):
        def do_import_side():
            do_import(side)
        return do_import_side

    for side in ("A", "B"):
        widgets[side]["stop"].clicked.connect(make_stop(side))
        widgets[side]["summarize"].clicked.connect(make_summarize(side))
        widgets[side]["clear"].clicked.connect(make_clear(side))
        widgets[side]["save"].clicked.connect(make_save(side))
        widgets[side]["import"].clicked.connect(make_import(side))

    # --- model pickers -----------------------------------------------------
    def on_picker_changed(side):
        def changed(index):
            picker = widgets[side]["picker"]
            # The plain model name is the item data (v0.33); the
            # visible text carries size and fit too.
            convo.sides[side].model = (
                picker.currentData() if index >= 0 else None)
            # A different model makes the last turn's stats stale.
            state["stats"][side] = None
            refresh_stats_label(side)
            # ...and its sparkline: the old samples described the
            # old model's rate (v0.47, R9).
            widgets[side]["sparkline"].clear()
            refresh_buttons()
            # The first-run guide's step 1 ticks once both models
            # are picked (v0.39, R5) -- refresh both panes, since
            # the tick depends on both pickers.
            refresh_empty_state("A")
            refresh_empty_state("B")
        return changed

    for side in ("A", "B"):
        widgets[side]["picker"].currentIndexChanged.connect(
            on_picker_changed(side))

    def populate_models(models):
        """Fill both pickers from [(name, size-in-bytes), ...].

        Each entry shows the model's size and whether it fits this
        PC (v0.33, U39); the plain name is the item data, so pairs,
        conversations, sends, relay, tasks and the phone deck all
        keep working with plain names exactly as before."""
        if not alive():
            return
        state["models_loading"] = False
        for side in ("A", "B"):
            picker = widgets[side]["picker"]
            previous = convo.sides[side].model
            picker.blockSignals(True)
            picker.clear()
            for name, size in models:
                picker.addItem(
                    format_model_entry(name, size, state["total_ram"]),
                    name)
            names = [name for name, _ in models]
            if previous in names:
                picker.setCurrentIndex(names.index(previous))
            else:
                # Nothing selected by default, ever.
                picker.setCurrentIndex(-1)
                convo.sides[side].model = None
            picker.setPlaceholderText("Pick a model")
            picker.blockSignals(False)
        if models:
            set_status_message(
                "Ollama connected: %d model(s) found." % len(models))
        else:
            set_status_message(
                "Ollama connected but no models found. Pull a model first.")
        refresh_buttons()

    def on_models_failed(message):
        if not alive():
            return
        state["models_loading"] = False
        for side in ("A", "B"):
            widgets[side]["picker"].setPlaceholderText("Pick a model")
        set_status_message(message)
        refresh_buttons()

    signals.models_loaded.connect(populate_models)
    signals.models_failed.connect(on_models_failed)

    loaders = []

    def _on_probe_result(ok, models_or_message, seconds):
        """Log the model-list probe: up/down plus its latency."""
        if ok:
            logger.info("Ollama probe: up, %d model(s), %.2fs"
                        % (len(models_or_message), seconds))
        else:
            logger.warning("Ollama probe: down (%.2fs): %s"
                           % (seconds, models_or_message))

    def load_models():
        # Loading state (v0.33, entry 011): while the /api/tags
        # fetch runs, the pickers are disabled with a "Loading
        # models..." placeholder -- the shape of what's coming,
        # honestly, never a fake progress bar. The picked model
        # survives the reload: populate_models restores it by name.
        state["models_loading"] = True
        set_status_message("Loading models...")
        for side in ("A", "B"):
            picker = widgets[side]["picker"]
            picker.blockSignals(True)
            picker.clear()
            picker.setCurrentIndex(-1)
            picker.setPlaceholderText("Loading models...")
            picker.setEnabled(False)
            picker.blockSignals(False)
        loader = _ModelLoader(client, signals, on_result=_on_probe_result)
        loaders.append(loader)
        loader.thread.finished.connect(
            lambda: loaders.remove(loader) if loader in loaders else None)
        loader.thread.start()

    # --- update check (v0.28) ---------------------------------------------
    # Two triggers, one check: once automatically at startup (below,
    # next to load_models) and on demand from the button. Both run
    # off the GUI thread, and both report through the same slot --
    # whichever finishes last writes its note, so a slow startup
    # check can never leave a stale result over a manual one. The
    # automatic check only speaks when a newer version is out: up
    # to date or failed (offline, say) it stays silent. The manual
    # check reports either way. Nothing is ever downloaded or
    # installed -- the note just points at the Releases page.
    update_checkers = []

    def run_update_check(manual):
        # check_fn=check_for_update resolves the name in dual_chat's
        # namespace, so tests stubbing dual_chat.check_for_update
        # (the v0.28 seam) still intercept the call -- see
        # duo_workers._UpdateChecker.
        checker = _UpdateChecker(signals, APP_VERSION, manual,
                                 check_fn=check_for_update)
        update_checkers.append(checker)
        checker.thread.finished.connect(
            lambda: update_checkers.remove(checker)
            if checker in update_checkers else None)
        checker.thread.start()

    def on_update_checked(result, manual):
        if not alive():
            return
        status, version = result
        if status == "newer":
            logger.action("update available: DUO %s" % version)
            set_status_message(
                MSG_UPDATE_AVAILABLE % version)
            # The popup asks (v0.55): one at a time, never over
            # another dialog -- the status note stays as backup.
            if offer_update(window, version, state):
                window._dual_chat["run_update_download"](version)
        elif manual and status == "latest":
            logger.info("update check: up to date")
            set_status_message(MSG_UPDATE_LATEST)
        elif manual and status == "failed":
            logger.info("update check failed")
            set_status_message(MSG_UPDATE_FAILED)
        elif status == "latest":
            logger.info("update check: up to date")
        else:
            logger.info("update check failed")

    signals.update_checked.connect(on_update_checked)
    update_btn.clicked.connect(lambda: run_update_check(manual=True))

    for side in ("A", "B"):
        widgets[side]["refresh"].clicked.connect(load_models)

    # --- phone deck ------------------------------------------------------
    def _deck_get_state():
        return build_deck_state(convo, state)

    def _deck_send(text, target):
        """Phone send: marshal onto the GUI thread and wait for the
        result (a plain error string, or None on success). The wait
        is bounded: if the GUI thread never answers, the phone gets
        a plain "try again" instead of hanging."""
        holder = {"done": threading.Event(), "error": None}
        try:
            signals.phone_send.emit(text, target, holder)
        except RuntimeError:
            return "DUO is closing. Try again in a moment."
        if not holder["done"].wait(timeout=15):
            return "DUO is busy. Try again in a moment."
        return holder["error"]

    def _deck_get_transcript():
        return format_full_transcript(
            list(state["full_log"]), trimmed=state["full_log_trimmed"])

    def on_phone_send(text, target, holder):
        """Runs on the GUI thread: validate + send, then wake the
        waiting deck thread with the result."""
        try:
            if not alive():
                holder["error"] = "DUO is closing. Try again in a moment."
            elif state.get("relay") is not None:
                holder["error"] = MSG_PHONE_RELAY
            elif state.get("task") is not None:
                holder["error"] = MSG_PHONE_TASK
            elif state["busy"] or state.get("summarizing") is not None:
                holder["error"] = MSG_PHONE_BUSY
            else:
                holder["error"] = send_message(text, target)
        finally:
            holder["done"].set()

    signals.phone_send.connect(on_phone_send)

    def _deck_port():
        if deck_port is not None:
            return deck_port
        try:
            return int(os.environ.get(DECK_PORT_ENV, "") or DECK_PORT)
        except ValueError:
            return DECK_PORT

    deck_server = DeckServer(_deck_get_state, _deck_send,
                             _deck_get_transcript, port=_deck_port())
    if deck_server.start():
        logger.action("phone deck serving at %s" % deck_server.url)
    else:
        logger.action("phone deck could not start (no free port)")
        deck_server = None

    def show_phone_deck():
        """The QR dialog: point the phone's camera at the code and
        the deck opens. The URL is shown as text too, and the dialog
        says plainly that the page is open to the WiFi."""
        dialog = QtWidgets.QDialog(window)
        dialog.setWindowTitle("Phone deck")
        layout = QtWidgets.QVBoxLayout(dialog)
        if deck_server is None or not deck_server.running:
            layout.addWidget(QtWidgets.QLabel(
                "The phone deck couldn't start -- no free port. "
                "Close DUO and try again."))
        else:
            url = deck_server.url
            pixmap = render_qr_pixmap(url)
            if pixmap is not None:
                qr_label = QtWidgets.QLabel()
                qr_label.setPixmap(pixmap)
                qr_label.setAlignment(QtCore.Qt.AlignCenter)
                layout.addWidget(qr_label)
            url_label = QtWidgets.QLabel(url)
            url_label.setTextInteractionFlags(
                QtCore.Qt.TextSelectableByMouse)
            url_label.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(url_label)
            note = QtWidgets.QLabel(
                "Point your phone's camera at the code. Anyone on your "
                "WiFi can open this page and send messages -- it only "
                "works on your home network.")
            note.setWordWrap(True)
            layout.addWidget(note)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)
        window._phone_deck_dialog = dialog
        dialog.show()

    phone_btn.clicked.connect(show_phone_deck)

    # Micro-interactions (v0.45, R7): the focus ring, the button
    # dip, the completion flash, and the reduced-motion setting --
    # all in duo_motion.py, installed here once every button exists.
    motion = install_motion(window, widgets, side_state,
                            SIDE_STATES["idle"][0], prefs)

    # Accessibility (v0.48, R10): the Big text preset -- the app
    # font grows by 2 pt, the window stylesheet gains a font-size
    # rule, and every label that sets its own size is re-styled,
    # so the text gets bigger everywhere at once. The self-sized
    # labels are collected here, once every widget exists, as
    # (label, style_builder) pairs; the title builder closes over
    # the side's color (the default arg pins it per side -- the
    # loop variable alone would bind late).
    small_labels = (
        [(widgets[side][key], small_label_style)
         for side in ("A", "B")
         for key in ("stats", "activity", "session_tokens")]
        + [(widgets[side][key], explanation_style)
           for side in ("A", "B")
           for key in ("temp_explain", "instr_explain")]
        + [(attach_note, small_label_style),
           (relay_status, small_label_style),
           (meter_label, small_label_style),
           (relay_explain, explanation_style),
           (stop_explain, explanation_style),
           (task_explain, explanation_style),
           (task_row_explain, explanation_style)]
        + [(widgets[side]["title"],
            lambda big, color=SIDE_COLORS[side]:
            title_style(big, color))
           for side in ("A", "B")])
    big_text = install_accessibility(window, small_labels, prefs)

    # --- menu bar (v0.30) -------------------------------------------------
    # File / Relay / Help, plus shortcuts for the main actions. Every
    # item calls the same handler as its button -- access, not
    # capability. The menu itself lives in duo_menu.py, so this file
    # stays under the 4,000-line bound; its enabled states are
    # mirrored in refresh_buttons, above.
    menu_bar, menu_actions = build_menu_bar(window, {
        "save_conversation": do_save_conversation,
        "open_conversation": do_open_conversation,
        "save_full": do_save_full,
        "save_both": do_save_both,
        "debug_report": do_debug_report,
        "quit": window.close,
        "start_relay": do_start_relay,
        "stop_relay": stop_relay,
        "check_updates": lambda: run_update_check(manual=True),
    }, APP_VERSION, reduce_motion=(motion.reduced_motion.set,
                                   motion.reduced_motion.value),
       big_text=(big_text.set, big_text.value))

    window._dual_chat = {  # test hooks (not part of the UI)
        "convo": convo, "runner": runner, "signals": signals,
        "widgets": widgets, "composer": composer, "panes": panes,
        "enter_send_check": enter_send_check,
        "send_a": send_a, "send_b": send_b, "send_both": send_both,
        "do_send": do_send, "load_models": load_models, "state": state,
        "relay_btn": relay_btn, "start_picker": start_picker,
        "cap_spin": cap_spin, "mode_picker": mode_picker,
        "stop_edit": stop_edit, "agree_check": agree_check,
        "relay_explain": relay_explain, "stop_explain": stop_explain,
        "relay_status": relay_status,
        "relay_chip": relay_chip, "relay_dot": relay_dot,
        "relay_chip_stop": relay_chip_stop,
        "set_relay_chip": set_relay_chip,
        "do_start_relay": do_start_relay, "stop_relay": stop_relay,
        "review_check": review_check, "review_frame": review_frame,
        "review_label": review_label, "review_edit": review_edit,
        "review_forward": review_forward,
        "review_skip": review_skip, "review_stop": review_stop,
        "pair_combo": pair_combo, "pair_name": pair_name,
        "pair_save": pair_save, "pair_delete": pair_delete,
        "on_pair_delete": on_pair_delete,
        "refresh_pair_combo": refresh_pair_combo,
        "preset_combo": preset_combo, "preset_name": preset_name,
        "preset_save": preset_save, "preset_delete": preset_delete,
        "on_preset_delete": on_preset_delete,
        "refresh_preset_combo": refresh_preset_combo,
        "use_this": use_this, "do_forward": do_forward,
        "render_transcript": render_transcript,
        "refresh_empty_state": refresh_empty_state,
        "mark_first_run_done": mark_first_run_done,
        "refresh_history_list": refresh_history_list,
        "do_summarize": do_summarize, "refresh_buttons": refresh_buttons,
        "attach_btn": attach_btn, "attach_frame": attach_frame,
        "attach_note": attach_note, "add_attachments": add_attachments,
        "remove_attachment": remove_attachment,
        "clear_attachments": clear_attachments,
        "task_edit": task_edit, "task_worker": task_worker,
        "task_follow_up": task_follow_up, "task_rounds": task_rounds,
        "task_run": task_run,
        "task_explain": task_explain,
        "task_row_explain": task_row_explain,
        "do_run_task": do_run_task,
        "save_both": save_both, "do_save": do_save,
        "do_save_both": do_save_both,
        "do_import": do_import,
        "clear_search": clear_search, "run_search": run_search,
        "step_search": step_search,
        "save_full": save_full, "do_save_full": do_save_full,
        "save_convo": save_convo, "open_convo": open_convo,
        "do_save_conversation": do_save_conversation,
        "do_open_conversation": do_open_conversation,
        "save_text_to_file": save_text_to_file,
        "debug_btn": debug_btn, "do_debug_report": do_debug_report,
        "debug_logger": logger,
        "send_message": send_message,
        "log_event": log_event,
        "set_side_state": set_side_state, "side_state": side_state,
        "meter_label": meter_label, "refresh_meter": refresh_meter,
        "meter_timer": meter_timer,
        "status_label": status_label,
        "set_status_message": set_status_message,
        "phone_btn": phone_btn, "deck_server": deck_server,
        "show_phone_deck": show_phone_deck,
        "on_phone_send": on_phone_send,
        "update_btn": update_btn, "run_update_check": run_update_check,
        "on_update_checked": on_update_checked,
        "update_checkers": update_checkers,
        "menu_bar": menu_bar, "menu_actions": menu_actions,
        "motion": motion, "big_text": big_text, "prefs": prefs,
        "director": director, "director_toggle": director_toggle,
        "director_scroll": director_scroll,
        "set_director_collapsed": _set_director_collapsed,
        "director_spring": director_spring,
        "relay_section": relay_body,
        "relay_section_toggle": relay_toggle,
        "set_relay_collapsed": _set_relay_collapsed,
        "pairs_section": pairs_body,
        "pairs_section_toggle": pairs_toggle,
        "set_pairs_collapsed": _set_pairs_collapsed,
        "tasks_section": tasks_body,
        "tasks_section_toggle": tasks_toggle,
        "set_tasks_collapsed": _set_tasks_collapsed,
    }

    # The download half of the update flow (v0.55): the worker,
    # its progress and result slots. The close handler below
    # waits for the downloaders it returns.
    update_downloaders = install_update_wiring(
        window, alive, download_fn=download_and_stage)

    # Closing the window stops only DUO: Ollama keeps running, the
    # way DUO found it -- DUO never starts or stops a server.
    _orig_close_event = window.closeEvent

    def _on_close(event):
        # P1-5 (v0.56): closing loses an unsaved conversation, and
        # there is no autosave. Ask first when either pane has
        # messages or work is in flight, defaulting to No. The ask
        # comes before anything is cancelled, waited on, or saved:
        # saying No leaves the window exactly as it was.
        if CLOSE_CONFIRM_ENABLED and close_needs_confirm(convo, state):
            answer = QtWidgets.QMessageBox.question(
                window, "Close DUO?",
                "You have an unsaved conversation. Close anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
        # Threads this window started (model loads, update checks)
        # must finish before the window goes away: destroying a
        # running QThread aborts the whole process ("QThread:
        # Destroyed while thread '' is still running", exit 134 in
        # the full suite). Each worker is bounded (the model load by
        # its retries, the update check by its 5s timeout), so the
        # wait always ends; the workers only do network I/O and
        # emit one signal, so waiting here cannot deadlock.
        # A download in flight is cancelled first: without this,
        # the wait below could last as long as the download.
        for _holder in list(update_downloaders):
            _holder.cancel()
        for _holders in (loaders, update_checkers,
                         update_downloaders):
            for _holder in list(_holders):
                if _holder.thread.isRunning():
                    _holder.thread.wait()
        # Remember the window for next time: size/position and the
        # pane split. Saved on close (not on every move/resize) so a
        # crash mid-session keeps the last clean state. The geometry
        # is stored as a plain rect (not saveGeometry bytes): Qt's
        # own restore clamps to the screen behind our backs, which
        # makes the round-trip untestable; a rect plus the clamp
        # below is fully deterministic. A maximized window saves its
        # normal geometry, so it reopens at its old size.
        geom = (window.normalGeometry() if window.isMaximized()
                else window.geometry())
        prefs.setValue(SETTING_WINDOW_GEOMETRY, geom)
        prefs.setValue(SETTING_SPLITTER_STATE, panes.saveState())
        for _side in ("A", "B"):
            # Only a visible editor saves: a hidden child saves as 0,
            # which would crush the editor on the next open. A
            # hidden editor keeps the size its last drag saved.
            if widgets[_side]["hist_frame"].isVisible():
                prefs.setValue(
                    SETTING_HIST_SPLITTER_STATE % _side,
                    widgets[_side]["hist_split"].saveState())
        prefs.sync()
        # Closing mid-generation must stop the work: without this,
        # the runner thread keeps a relay going in the closed window,
        # still calling Ollama after DUO is gone (and, in the tests,
        # still appending to the shared fake client's call list long
        # after the window closed). Cancel every job and deactivate
        # the relay/task so a done signal already queued can't submit
        # another hop.
        for side in list(state["active"] | state["queued"]):
            runner.cancel(side)
        if state.get("summarizing") is not None:
            runner.cancel("sum-" + state["summarizing"])
        if state.get("relay") is not None:
            state["relay"].active = False
        if state.get("task") is not None:
            state["task"].active = False
        if deck_server is not None:
            deck_server.stop()
        # The meter timer, the error-light timers, and the
        # sparkline timers are parented to the window, but close()
        # only hides a top-level window -- the timers would keep
        # firing into it. Stop them here so nothing keeps working
        # after DUO is gone.
        meter_timer.stop()
        for _timer in error_timers.values():
            _timer.stop()
        for _timer in spark_timers.values():
            _timer.stop()
        motion.stop()
        director_spring.stop()
        logger.action("DUO closed")
        _orig_close_event(event)

    window.closeEvent = _on_close

    # --- remember the window (v0.15) -----------------------------------
    # Restore the saved size/position and pane split. A stale restore
    # is clamped to the available screen: the size is capped at the
    # screen when the screen can hold the window's minimum size, and
    # the position keeps at least 100 px of width and 50 px of height
    # on screen, so the title bar is always reachable and the window
    # can be dragged back. A missing or corrupt saved value keeps the
    # defaults above.
    def _clamp_rect(rect, avail, min_w, min_h):
        width = rect.width()
        height = rect.height()
        if avail.width() >= min_w:
            width = max(min_w, min(width, avail.width()))
        if avail.height() >= min_h:
            height = max(min_h, min(height, avail.height()))
        x = max(avail.left(), min(rect.x(), avail.right() - 100))
        y = max(avail.top(), min(rect.y(), avail.bottom() - 50))
        return QtCore.QRect(x, y, width, height)

    saved_geometry = prefs.value(SETTING_WINDOW_GEOMETRY)
    if isinstance(saved_geometry, QtCore.QRect):
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            clamped = _clamp_rect(
                saved_geometry, screen.availableGeometry(),
                window.minimumWidth(), window.minimumHeight())
            window.setGeometry(clamped)
        else:
            window.setGeometry(saved_geometry)
    saved_splitter = prefs.value(SETTING_SPLITTER_STATE)
    if saved_splitter is not None:
        panes.restoreState(saved_splitter)

    refresh_pair_combo()
    refresh_preset_combo()
    load_models()
    # The update check runs once per launch, on a background thread,
    # and never blocks the window opening. It is not re-run on
    # refreshes or pane actions -- only the button runs it again.
    run_update_check(manual=False)
    refresh_buttons()
    # First run (v0.39, R5): both panes empty and the user has never
    # sent a message -- the transcripts teach the loop.
    refresh_empty_state("A")
    refresh_empty_state("B")
    return window


def _show_missing_pyside6():
    """First-run guard: PySide6 is missing. Print a plain-language
    message to stderr and, when tkinter is available, show it in a
    small dialog too (the app may have been double-clicked, so stderr
    alone may never be seen). Never raises -- a missing GUI toolkit
    must not crash with a traceback."""
    print(MSG_PYSIDE6_MISSING, file=sys.stderr)
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("DUO needs PySide6", MSG_PYSIDE6_MISSING)
        root.destroy()
    except Exception:
        pass


def main(argv=None):
    # A staged update from the last run (v0.55): apply it before
    # anything starts, then restart so the new version runs.
    if apply_pending_update()[0] == "applied":
        restart_quietly()
    argv = list(sys.argv[1:] if argv is None else argv)
    base_url = None
    if "--ollama-url" in argv:
        i = argv.index("--ollama-url")
        if i + 1 < len(argv):
            base_url = argv[i + 1]
    try:
        from PySide6 import QtWidgets
    except ImportError:
        # First-run guard (v0.14): PySide6 is the one package DUO
        # needs, and a fresh install may not have it yet. Say so in
        # plain language and exit cleanly -- never a raw traceback.
        _show_missing_pyside6()
        return 1
    app = QtWidgets.QApplication(argv)
    # Single instance (v0.44, U27): a second launch asks the DUO
    # already running to come to the front and exits cleanly --
    # acquire() already sent the show message when it found one,
    # so there is nothing more to do here.
    guard = SingleInstance()
    if not guard.acquire():
        return 0
    logger = make_debug_logger()
    logger.install_crash_handler()
    window = build_window(OllamaClient(base_url), logger=logger)
    guard.set_show_callback(
        lambda: (window.show(), window.raise_(),
                 window.activateWindow()))
    window.show()
    # First-boot offer (v0.48, R10): on a first run, offer Big
    # text once -- the offer itself records that it was made, so
    # it never appears again. A Yes turns the preset on without
    # going through the menu, so the menu item is synced from the
    # setting afterwards (the menu only tracks clicks itself).
    if not window._dual_chat["state"]["first_run_done"]:
        window._dual_chat["big_text"].offer(window)
        window._dual_chat["menu_actions"]["big_text"].setChecked(
            window._dual_chat["big_text"].value)
    code = app.exec()
    # Restart for update (v0.55): the Restart-now path set the
    # flag and closed the window; re-exec on the new files.
    if window._dual_chat["state"].get("restart_for_update"):
        restart_quietly()
    return code


if __name__ == "__main__":
    sys.exit(main())
