#!/usr/bin/env python3
"""DUO 0.28 - two Ollama models side by side in one desktop window.

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
"""

import html
import json
import os
import re
import shutil  # noqa: F401 -- unused here, but the tests patch
# dual_chat.shutil.which (the Ollama fallback lookup lives in
# duo_ollama; shutil is one shared module, so the patch lands).
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Debug log layer: the BNB T21 standard, vendored as debug_log.py next
# to this file. Logging must never break the app, so a missing module
# falls back to a do-nothing logger instead of crashing the import.
try:
    import debug_log as _debug_log
except ImportError:  # pragma: no cover - the zip always ships it
    _debug_log = None

# QR codes for the phone deck: Nayuki's QR Code generator (MIT),
# vendored as qrcodegen.py next to this file. A missing module only
# costs the QR picture -- the deck URL is still shown as text.
try:
    import qrcodegen as _qrcodegen
except ImportError:  # pragma: no cover - the zip always ships it
    _qrcodegen = None


# The code used to live in this one file (5,165 lines). v0.25
# (audit T14) splits it into focused sibling modules; this file
# keeps the window, the deck, and the entry point, and
# re-exports the moved names so `from dual_chat import ...`
# keeps working unchanged -- the tests, the installer, and the
# deck all import through here. build_window stays a closure
# in this file: splitting the window itself into a class is a
# bigger redesign and not this task.
from duo_icon import make_app_icon
from duo_markdown import markdown_to_html
from duo_model import (
    ChatMessage, ChatSide, Conversation, MSG_NO_MODEL, NoModelSelected,
    SUMMARY_KEEP_LAST, SUMMARY_LABEL,)
from duo_ollama import (
    CHAT_MAX_ATTEMPTS, CHAT_RETRY_DELAYS, CHAT_TIMEOUT, DEFAULT_OLLAMA_URL,
    MSG_MODEL_FAILED, MSG_OLLAMA_DOWN, MSG_OLLAMA_NOT_INSTALLED,
    MSG_OLLAMA_SLOW, MSG_OLLAMA_START_FAILED, OLLAMA_URL_ENV,
    UPDATE_CHECK_TIMEOUT, UPDATE_CHECK_URL, UPDATE_CHECK_URL_ENV, Cancelled,
    OllamaClient, OllamaError, OllamaServerManager, check_for_update,
    parse_ollama_list_output, parse_ps_payload, parse_tags_payload,
    parse_version,)
from duo_runner import SerialRunner
from duo_stats import (
    STATS_EMPTY_LINE, compute_turn_stats, format_ram, format_session_line,
    format_stats_line,)
from duo_storage import (
    CONVERSATION_FORMAT_VERSION, ConversationError, conversation_to_data,
    data_to_conversation, delete_pair, delete_preset, is_valid_preset,
    load_conversation_file, load_pairs, load_presets, pairs_path,
    presets_file_corrupt, presets_path, save_conversation_file, save_pair,
    save_preset, write_text_atomic,)
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


class _NullLogger:
    """Drop-in for DebugLogger when the module is missing: every call
    is a no-op, and a report request reports that logging is off."""

    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass

    def action(self, *a, **k):
        pass

    def set_setting(self, *a, **k):
        pass

    def register_service(self, *a, **k):
        pass

    def register_dependency(self, *a, **k):
        pass

    def build_report(self):
        return "DEBUG REPORT — %s %s\n\nLogging is not available.\n" % (
            APP_NAME, APP_VERSION)

    def generate_report(self, path=None):
        if path is None:
            path = "debug-report-unavailable.txt"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.build_report())
        return path

    def install_crash_handler(self):
        pass

    def uninstall_crash_handler(self):
        pass


def make_debug_logger(log_dir=None):
    """A DebugLogger for this app, or a null logger when the vendored
    module is missing. The log file lives next to the app
    (duo-debug.log), rotating so it never grows without bound."""
    if _debug_log is None:
        return _NullLogger()
    if log_dir is None:
        log_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        return _debug_log.DebugLogger("duo-debug", APP_VERSION,
                                      log_dir=log_dir)
    except OSError:
        return _NullLogger()

APP_NAME = "DUO"
APP_VERSION = "0.28"

# Phone deck (v0.11): DUO serves a small local page for his phone.
# Fixed port with a fallback chain if it is busy; DUAL_CHAT_DECK_PORT
# overrides it (0 means "any free port" -- used by the tests).
DECK_PORT = 8765
DECK_PORT_TRIES = 5
DECK_PORT_ENV = "DUAL_CHAT_DECK_PORT"

# Plain-language messages shown in the panes. No tracebacks in the UI.
MSG_STOPPED = "Stopped."
MSG_EMPTY = "Type a message first."
MSG_RELAY_NEEDS_BOTH = "Pick a model for both sides to start a relay."
MSG_RELAY_STOPPED_BY_YOU = "stopped by you"
MSG_PHONE_BUSY = "DUO is busy right now. Wait for the current turn to finish, then send again."
MSG_PHONE_RELAY = "A relay is running. Stop the relay before sending from your phone."
MSG_PHONE_TASK = "A task is running. Wait for it to finish, then send again."
MSG_PHONE_TARGET = "Pick who to send to: A, B, or Both."
# Update check (v0.28): the note when a newer Release is out
# ("DUO 0.29 is out ..."), what a manual check says when this copy
# is current, and what it says when the check itself failed. The
# automatic startup check only ever shows the first one -- when
# this copy is current or the check fails, it stays silent.
MSG_UPDATE_AVAILABLE = "DUO %s is out — see the Releases page on GitHub."
MSG_UPDATE_LATEST = "You're on the latest version."
MSG_UPDATE_FAILED = "Couldn't check for updates — are you online?"
MSG_PYSIDE6_MISSING = (
    "DUO needs PySide6 to open its window, and it isn't installed.\n"
    "Run the installer first:\n"
    "\n"
    "    bash install.sh\n"
    "\n"
    "Then start DUO again with:\n"
    "\n"
    "    bash run.sh\n")

# Persisted preferences (QSettings): the "Enter to send" tickbox is
# remembered between runs. Explicit org/app names so the setting
# lands in the same place no matter how the app was launched.
SETTINGS_ORG = "BrokeNBored"
SETTINGS_APP = "DUO"
SETTING_ENTER_TO_SEND = "composer/enter_to_send"
# Window finish (v0.15): the window's size/position and the pane
# split are remembered the same way, saved when the window closes.
SETTING_WINDOW_GEOMETRY = "window/geometry"
SETTING_SPLITTER_STATE = "window/splitter"
# Model parameters (v0.19): each side's temperature is remembered
# per side the same way. %s is the side ("A"/"B").
SETTING_TEMPERATURE = "temperature/%s"
TEMPERATURE_DEFAULT = 0.7  # Ollama's own default, made visible.
TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0

# Agreement stop: simple case-insensitive substring matching on this
# fixed list. No LLM judge -- the 2B models are slow enough already.
AGREEMENT_PHRASES = ("i agree", "agreed", "sounds good", "that works")
AWARE_TEMPLATE = ("You are talking with another AI assistant (Model %s). "
                  "It said: %s")
RELAY_CAP_MIN = 1
RELAY_CAP_MAX = 50
RELAY_CAP_DEFAULT = 10

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


# Attachments (Item 5): text files only, each at most 100 KB, at most
# 5 attached at once. Contents are read at attach time, so a file
# deleted before the send still goes through.
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 100 * 1024


class AttachmentError(Exception):
    """A plain-language reason a file couldn't be attached."""


def load_attachment(path):
    """Read a file as an attachment: (name, text).

    Raises AttachmentError with a plain message when the file is too
    big, isn't decodable UTF-8, or can't be read. The size is checked
    before reading, so a huge file is refused without loading it.
    """
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
    except OSError:
        raise AttachmentError("Couldn't read '%s'." % name)
    if size > MAX_ATTACHMENT_BYTES:
        raise AttachmentError(
            "'%s' is over 100 KB, so it wasn't attached." % name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        raise AttachmentError(
            "'%s' isn't a text file, so it wasn't attached." % name)
    return name, text


def build_attached_message(files, text):
    """Prepend file blocks to a typed message.

    files is [(name, contents), ...]. Each file becomes a block
    "File: <name>\n<contents>"; blocks and the typed message are
    separated by a blank line. No files -> the message unchanged.
    """
    if not files:
        return text
    blocks = ["File: %s\n%s" % (name, contents) for name, contents in files]
    return "\n\n".join(blocks) + "\n\n" + text


def attachment_tag(names):
    """The short transcript tag for attached files."""
    return "(Attached: %s)" % ", ".join(names)


def _open_link_in_browser(url):
    """Open a link clicked in a transcript pane in the default
    browser. The import is lazy: Qt lives in build_window, not at
    module level."""
    from PySide6 import QtGui
    QtGui.QDesktopServices.openUrl(url)


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


# ---------------------------------------------------------------------------
# Phone deck (v0.11)
# ---------------------------------------------------------------------------

def get_lan_ip():
    """This computer's LAN address, for the deck URL. A UDP connect
    learns the address without sending any traffic; with no route at
    all it falls back to loopback."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return "127.0.0.1"


def build_deck_state(convo, state):
    """Snapshot of the conversation for the phone deck: both sides'
    transcripts (labels + display text, exactly as the window shows
    them), model names, which side is thinking, relay status. The
    lists are copied: the deck thread reads while the GUI thread
    writes, so a snapshot must never see a half-appended message."""
    sides = {}
    for side in ("A", "B"):
        side_obj = convo.sides[side]
        # Copy first: the deck thread reads while the GUI thread
        # writes, so the snapshot must never see a half-appended
        # message. One list of messages, so the three can never
        # drift out of sync the way the old parallel arrays could.
        messages = [{"label": msg.label, "text": msg.display_text()}
                    for msg in list(side_obj.messages)]
        sides[side] = {"model": side_obj.model, "messages": messages}
    active = sorted(state.get("active") or [])
    queued = sorted(state.get("queued") or [])
    if active:
        active_side = active[0]
    elif queued:
        active_side = queued[0]
    elif state.get("summarizing"):
        active_side = state["summarizing"]
    else:
        active_side = None
    relay = state.get("relay")
    return {
        "sides": sides,
        "active_side": active_side,
        "busy": bool(state.get("busy")),
        "relay": relay.status_running() if relay is not None else None,
        "version": APP_VERSION,
    }


DECK_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DUO</title>
<style>
body { margin: 0; padding: 12px; background: #1c1c1e; color: #f2f2f7;
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif; font-size: 18px; }
#top { display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 10px; }
#title { font-size: 22px; font-weight: bold; }
#relay { font-size: 14px; color: #a1a1a6; }
.panel { background: #2c2c2e; border-radius: 10px; padding: 10px 12px;
  margin-bottom: 10px; }
#big-msgs { max-height: 45vh; overflow-y: auto; }
#small-msgs { max-height: 18vh; overflow-y: auto; font-size: 15px; color: #d1d1d6; }
.head { display: flex; justify-content: space-between; font-weight: bold;
  margin-bottom: 8px; }
.thinking { color: #0a84ff; font-weight: normal; font-size: 15px; }
.msg { margin-bottom: 14px; }
.lbl { font-weight: bold; }
.txt { white-space: pre-wrap; word-wrap: break-word; }
#composer { margin-top: 4px; }
#msg { width: 100%; box-sizing: border-box; font-size: 18px; padding: 10px;
  border-radius: 10px; border: none; background: #2c2c2e; color: #f2f2f7; }
#targets { display: flex; gap: 8px; margin-top: 8px; }
#targets button, #send { flex: 1; min-height: 48px; font-size: 18px;
  border-radius: 10px; border: none; background: #3a3a3c; color: #f2f2f7; }
#targets button.on { background: #0a84ff; }
#send { background: #30d158; color: #000; font-weight: bold; }
#err { color: #ff6961; min-height: 22px; margin-top: 6px; font-size: 15px; }
#foot { margin-top: 10px; text-align: center; }
#export { color: #0a84ff; font-size: 16px; }
</style>
</head>
<body>
<div id="top"><span id="title">DUO</span><span id="relay"></span></div>
<div class="panel"><div class="head"><span id="big-name"></span><span id="big-thinking" class="thinking"></span></div><div id="big-msgs"></div></div>
<div class="panel"><div class="head"><span id="small-name"></span><span></span></div><div id="small-msgs"></div></div>
<div id="composer">
<textarea id="msg" rows="2" placeholder="Type a message..."></textarea>
<div id="targets">
<button type="button" data-t="A">A</button>
<button type="button" data-t="B">B</button>
<button type="button" data-t="Both">Both</button>
<button type="button" id="send">Send</button>
</div>
<div id="err"></div>
</div>
<div id="foot"><a id="export" href="/transcript">Export transcript</a></div>
<script>
var bigSide = "A";
var lastState = "";
var target = "";
function show(el, msgs) {
  while (el.firstChild) { el.removeChild(el.firstChild); }
  for (var i = 0; i < msgs.length; i++) {
    var block = document.createElement("div");
    block.className = "msg";
    var lbl = document.createElement("div");
    lbl.className = "lbl";
    lbl.textContent = msgs[i].label + ":";
    var txt = document.createElement("div");
    txt.className = "txt";
    txt.textContent = msgs[i].text;
    block.appendChild(lbl);
    block.appendChild(txt);
    el.appendChild(block);
  }
}
function atBottom(el) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 60;
}
function render(state) {
  var active = state.active_side;
  if (active && active !== bigSide) { bigSide = active; }
  var smallSide = bigSide === "A" ? "B" : "A";
  var big = state.sides[bigSide];
  var small = state.sides[smallSide];
  document.getElementById("big-name").textContent =
    "Model " + bigSide + (big.model ? " (" + big.model + ")" : "");
  document.getElementById("small-name").textContent =
    "Model " + smallSide + (small.model ? " (" + small.model + ")" : "");
  document.getElementById("big-thinking").textContent =
    (active === bigSide) ? "thinking..." : "";
  document.getElementById("relay").textContent = state.relay || "";
  var bigEl = document.getElementById("big-msgs");
  var stick = atBottom(bigEl);
  show(bigEl, big.messages);
  if (stick) { bigEl.scrollTop = bigEl.scrollHeight; }
  var smallMsgs = small.messages.slice(-3);
  show(document.getElementById("small-msgs"), smallMsgs);
}
function poll() {
  fetch("/state").then(function (r) { return r.json(); }).then(function (state) {
    var raw = JSON.stringify(state);
    if (raw !== lastState) { lastState = raw; render(state); }
  }).catch(function () {});
}
document.querySelectorAll("#targets button[data-t]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    document.querySelectorAll("#targets button[data-t]").forEach(function (b) {
      b.classList.remove("on");
    });
    btn.classList.add("on");
    target = btn.getAttribute("data-t");
    document.getElementById("err").textContent = "";
  });
});
document.getElementById("send").addEventListener("click", function () {
  var box = document.getElementById("msg");
  var err = document.getElementById("err");
  if (!target) { err.textContent = "Pick who to send to: A, B, or Both."; return; }
  if (!box.value.trim()) { err.textContent = "Type a message first."; return; }
  err.textContent = "";
  fetch("/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: box.value, target: target })
  }).then(function (r) { return r.json(); }).then(function (res) {
    if (res.ok) { box.value = ""; poll(); }
    else { err.textContent = res.error || "Couldn't send that."; }
  }).catch(function () {
    err.textContent = "Couldn't reach DUO. Try again.";
  });
});
poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""


class _DeckRequestHandler(BaseHTTPRequestHandler):
    """One deck request. The page, the live state, phone sends, and
    the transcript download -- nothing else."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # No request logging: the deck stays quiet.

    @property
    def _deck(self):
        return self.server.deck

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, body, content_type="text/html; charset=utf-8",
                   filename=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if filename is not None:
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % filename)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send_text(DECK_PAGE_HTML)
        elif path == "/state":
            try:
                self._send_json(self._deck.get_state())
            except Exception:
                self._send_json({"error": "DUO is busy. Try again."},
                                status=500)
        elif path == "/transcript":
            try:
                text = self._deck.get_transcript()
            except Exception:
                self._send_json(
                    {"error": "Couldn't build the transcript."}, status=500)
                return
            self._send_text(text, "text/plain; charset=utf-8",
                            filename=default_full_filename())
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/send":
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        length = max(0, min(length, 1024 * 1024))  # 1 MB cap
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(
                {"ok": False,
                 "error": "That message didn't arrive right. Try again."})
            return
        if not isinstance(payload, dict):
            self._send_json(
                {"ok": False,
                 "error": "That message didn't arrive right. Try again."})
            return
        text = payload.get("text", "")
        target = payload.get("target", "")
        if not isinstance(text, str) or not text.strip():
            self._send_json({"ok": False, "error": MSG_EMPTY})
            return
        if target not in ("A", "B", "Both"):
            self._send_json({"ok": False, "error": MSG_PHONE_TARGET})
            return
        error = self._deck.send_message(text, target)
        if error is None:
            self._send_json({"ok": True})
        else:
            self._send_json({"ok": False, "error": error})


class DeckServer:
    """The phone deck's HTTP server: the page, the live state, phone
    sends, and the transcript download. Runs on its own thread; the
    callbacks reach into the window (get_state snapshots are copied,
    send_message marshals onto the GUI thread in build_window)."""

    def __init__(self, get_state, send_message, get_transcript,
                 host="0.0.0.0", port=DECK_PORT):
        self.get_state = get_state
        self.send_message = send_message
        self.get_transcript = get_transcript
        self.host = host
        self.port = port
        self.httpd = None
        self.thread = None

    @property
    def running(self):
        return self.httpd is not None

    @property
    def url(self):
        if not self.running:
            return None
        return "http://%s:%d/" % (get_lan_ip(), self.port)

    def start(self):
        """Bind and serve. Tries port, port+1, ... (DECK_PORT_TRIES
        ports); port 0 means any free port. Returns True on success."""
        if self.port == 0:
            tries = [0]
        else:
            tries = range(self.port, self.port + DECK_PORT_TRIES)
        for port in tries:
            try:
                httpd = ThreadingHTTPServer(
                    (self.host, port), _DeckRequestHandler)
            except OSError:
                continue
            httpd.daemon_threads = True
            httpd.deck = self
            self.httpd = httpd
            self.port = httpd.server_address[1]
            self.thread = threading.Thread(
                target=httpd.serve_forever, daemon=True)
            self.thread.start()
            return True
        return False

    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.thread = None


def render_qr_pixmap(url, scale=6, border=4):
    """A QR code pixmap for `url`, or None when the encoder is missing.
    Drawn with QPainter: black modules on white, with a quiet zone."""
    if _qrcodegen is None:
        return None
    from PySide6 import QtCore, QtGui
    qr = _qrcodegen.QrCode.encode_text(url, _qrcodegen.QrCode.Ecc.MEDIUM)
    size = qr.get_size()
    pixels = (size + border * 2) * scale
    image = QtGui.QImage(pixels, pixels, QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(image)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor("black"))
    for y in range(size):
        for x in range(size):
            if qr.get_module(x, y):
                painter.drawRect((x + border) * scale,
                                 (y + border) * scale, scale, scale)
    painter.end()
    return QtGui.QPixmap.fromImage(image)


# ---------------------------------------------------------------------------
# Qt GUI
# ---------------------------------------------------------------------------

def _qt():
    from PySide6 import QtCore, QtWidgets  # noqa: F401  (imported on demand)
    return QtCore, QtWidgets


class _Signals:
    """QObject bridge: runner callbacks (worker thread) -> Qt signals."""

    def __init__(self):
        QtCore, _ = _qt()
        self._obj = _SignalObject()

    def __getattr__(self, name):
        return getattr(self._obj, name)


class _SignalObject:
    def __init__(self):
        from PySide6.QtCore import QObject, Signal

        class _Obj(QObject):
            started = Signal(str)
            chunk = Signal(str, str)
            done = Signal(str, str)
            failed = Signal(str, str)
            cancelled = Signal(str)
            idle = Signal()
            models_loaded = Signal(list)
            models_failed = Signal(str)
            ollama_started = Signal(bool, str)
            # Update check: (result, manual) where result is
            # check_for_update's ("newer"/"latest"/"failed", version)
            # and manual says whether the button asked for it.
            update_checked = Signal(object, bool)
            # Phone deck send: (text, target, holder dict with a
            # "done" Event and an "error" slot the GUI thread fills).
            phone_send = Signal(str, str, object)

        self._inner = _Obj()
        self.started = self._inner.started
        self.chunk = self._inner.chunk
        self.done = self._inner.done
        self.failed = self._inner.failed
        self.cancelled = self._inner.cancelled
        self.idle = self._inner.idle
        self.models_loaded = self._inner.models_loaded
        self.models_failed = self._inner.models_failed
        self.ollama_started = self._inner.ollama_started
        self.update_checked = self._inner.update_checked
        self.phone_send = self._inner.phone_send


class _ModelLoader:
    """Loads the model list off the GUI thread.

    on_result (optional) is called in the loader thread with
    (ok, models_or_message, seconds) -- the debug log uses it to
    record the probe outcome and its latency.
    """

    def __init__(self, client, signals, on_result=None):
        from PySide6.QtCore import QThread

        class _Thread(QThread):
            def run(self):
                started = time.monotonic()
                try:
                    models = client.list_models()
                    if on_result is not None:
                        on_result(True, models,
                                  time.monotonic() - started)
                    try:
                        signals.models_loaded.emit(models)
                    except RuntimeError:
                        pass  # Window gone; nothing left to update.
                except OllamaError as exc:
                    if on_result is not None:
                        on_result(False, str(exc),
                                  time.monotonic() - started)
                    try:
                        signals.models_failed.emit(str(exc))
                    except RuntimeError:
                        pass  # Window gone; nothing left to update.

        self.thread = _Thread()


class _OllamaStarter:
    """Starts Ollama off the GUI thread (the wait for the probe can
    take seconds). Emits (ok, message) through signals.ollama_started."""

    def __init__(self, manager, signals):
        from PySide6.QtCore import QThread

        class _Thread(QThread):
            def run(self):
                ok, message = manager.start()
                try:
                    signals.ollama_started.emit(ok, message)
                except RuntimeError:
                    pass  # Window gone; nothing left to update.

        self.thread = _Thread()


class _UpdateChecker:
    """Runs the update check off the GUI thread (the network call
    can take up to the 5s timeout). Emits (result, manual) through
    signals.update_checked, where result is check_for_update's
    ("newer"/"latest"/"failed", version) tuple."""

    def __init__(self, signals, current_version, manual):
        from PySide6.QtCore import QThread

        class _Thread(QThread):
            def run(self):
                result = check_for_update(current_version)
                try:
                    signals.update_checked.emit(result, manual)
                except RuntimeError:
                    pass  # Window gone; nothing left to update.

        self.thread = _Thread()


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
    ollama_manager = OllamaServerManager(client)
    signals = _Signals()
    convo = Conversation()

    window = QtWidgets.QMainWindow()
    window.setWindowTitle("%s %s" % (APP_NAME, APP_VERSION))
    window.setWindowIcon(make_app_icon())
    window.resize(1100, 680)  # Fits a 1366x768 screen with room to spare.
    window.setMinimumSize(900, 540)

    def alive():
        # Queued runner signals can land after the window is gone (e.g.
        # closing right as a relay stops). Never touch widgets then.
        return shiboken6.isValid(window)

    central = QtWidgets.QWidget()
    window.setCentralWidget(central)
    root = QtWidgets.QVBoxLayout(central)
    root.setContentsMargins(8, 8, 8, 8)
    root.setSpacing(8)

    panes = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
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
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        title_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Model " + side)
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
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
            "0.7 is Ollama's default — lower is more focused, "
            "higher is more random.")
        temp_spin.setValue(prefs.value(
            SETTING_TEMPERATURE % side, TEMPERATURE_DEFAULT,
            type=float))

        def _save_temperature(value):
            prefs.setValue(SETTING_TEMPERATURE % side, float(value))
            prefs.sync()

        temp_spin.valueChanged.connect(_save_temperature)
        temp_row.addWidget(temp_spin)
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
        layout.addWidget(transcript, stretch=1)

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
        layout.addLayout(search_row)

        # Stats line: the last completed turn's speed and RAM. Dashes
        # until a turn has run.
        stats = QtWidgets.QLabel(format_stats_line(None))
        stats.setStyleSheet("font-size: 11px;")
        layout.addWidget(stats)

        # Session token total: every token this side has generated
        # or been prompted with since the app started. Clear never
        # resets it -- only restarting the app does.
        session_tokens = QtWidgets.QLabel(
            format_session_line({"out": 0, "in": 0}))
        session_tokens.setStyleSheet("font-size: 11px;")
        layout.addWidget(session_tokens)

        # Summarize row: the nudge label appears when this side's
        # history passes the budget; the button is always here.
        summarize_row = QtWidgets.QHBoxLayout()
        nudge = QtWidgets.QLabel("History is long — Summarize?")
        nudge.setVisible(False)
        summarize_row.addWidget(nudge)
        summarize_btn = QtWidgets.QPushButton("Summarize")
        summarize_btn.setEnabled(False)
        summarize_row.addWidget(summarize_btn)
        summarize_row.addStretch(1)
        layout.addLayout(summarize_row)

        # History editor: reorder or delete any message. Hidden by
        # default; the model sees the edited order on the next turn.
        hist_frame = QtWidgets.QFrame()
        hist_layout = QtWidgets.QVBoxLayout(hist_frame)
        hist_layout.setContentsMargins(0, 0, 0, 0)
        hist_layout.setSpacing(4)
        hist_list = QtWidgets.QListWidget()
        hist_list.setFixedHeight(110)
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
        layout.addWidget(hist_frame)

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
            "picker": picker, "refresh": refresh, "transcript": transcript,
            "temperature": temp_spin,
            "status": status, "stop": stop, "clear": clear,
            "save": save, "import": import_btn,
            "search_edit": search_edit, "search_count": search_count,
            "search_prev": search_prev, "search_next": search_next,
            "search_matches": [], "search_current": -1,
            "instr_toggle": instr_toggle, "instr_edit": instr_edit,
            "use_this": use_this, "hist_toggle": hist_toggle,
            "hist_frame": hist_frame, "hist_list": hist_list,
            "hist_up": hist_up, "hist_down": hist_down,
            "hist_delete": hist_delete,
            "stats": stats, "session_tokens": session_tokens,
            "nudge": nudge, "summarize": summarize_btn,
            "reply_open": False,
            # Document position where the streaming reply's body
            # starts (just after its label). on_done uses it to
            # replace the streamed plain text with the Markdown
            # rendering; None when no reply is open.
            "reply_body_pos": None,
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
    composer.setFixedHeight(64)
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
    # No hint label here: the row holds nine buttons now, and a
    # label gets clipped to a stub ("Send to A, B, or bc", v0.28).
    # The buttons say what they do; the stretch keeps them
    # right-aligned, where they were.
    btn_row.addStretch(1)
    send_a = QtWidgets.QPushButton("Send to A")
    send_b = QtWidgets.QPushButton("Send to B")
    send_both = QtWidgets.QPushButton("Send to Both")
    for btn in (send_a, send_b, send_both):
        btn.setEnabled(False)
        btn_row.addWidget(btn)
    save_both = QtWidgets.QPushButton("Save both")
    btn_row.addWidget(save_both)
    save_full = QtWidgets.QPushButton("Save full transcript")
    btn_row.addWidget(save_full)
    save_convo = QtWidgets.QPushButton("Save conversation")
    btn_row.addWidget(save_convo)
    open_convo = QtWidgets.QPushButton("Open conversation")
    btn_row.addWidget(open_convo)
    debug_btn = QtWidgets.QPushButton("Debug report")
    btn_row.addWidget(debug_btn)
    phone_btn = QtWidgets.QPushButton("Phone deck")
    btn_row.addWidget(phone_btn)
    update_btn = QtWidgets.QPushButton("Check for updates")
    btn_row.addWidget(update_btn)
    d_layout.addLayout(btn_row)

    # --- relay row ---------------------------------------------------------
    relay_row = QtWidgets.QHBoxLayout()
    relay_btn = QtWidgets.QPushButton("Start Relay")
    relay_btn.setEnabled(False)
    relay_row.addWidget(relay_btn)
    relay_row.addWidget(QtWidgets.QLabel("Start with:"))
    start_picker = QtWidgets.QComboBox()
    start_picker.addItems(["A", "B"])
    relay_row.addWidget(start_picker)
    relay_row.addWidget(QtWidgets.QLabel("Turns:"))
    cap_spin = QtWidgets.QSpinBox()
    cap_spin.setRange(RELAY_CAP_MIN, RELAY_CAP_MAX)
    cap_spin.setValue(RELAY_CAP_DEFAULT)
    relay_row.addWidget(cap_spin)
    mode_picker = QtWidgets.QComboBox()
    mode_picker.addItems(["Blind", "Aware"])
    relay_row.addWidget(mode_picker)
    relay_row.addWidget(QtWidgets.QLabel("Stop on:"))
    stop_edit = QtWidgets.QLineEdit()
    stop_edit.setPlaceholderText("words, separated by commas")
    relay_row.addWidget(stop_edit, stretch=1)
    agree_check = QtWidgets.QCheckBox("Stop on agreement")
    relay_row.addWidget(agree_check)
    review_check = QtWidgets.QCheckBox("Review each hop")
    relay_row.addWidget(review_check)
    d_layout.addLayout(relay_row)

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
    d_layout.addLayout(preset_row)

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
    d_layout.addLayout(pair_row)

    # --- task row ---------------------------------------------------------
    # Tasks (Item 6): hand a job to one model, and optionally have the
    # other model review or continue the result. Nothing pre-selected.
    task_edit = QtWidgets.QPlainTextEdit()
    task_edit.setPlaceholderText("Describe a task for one model...")
    task_edit.setFixedHeight(44)
    d_layout.addWidget(task_edit)

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
    d_layout.addLayout(task_row)

    # --- hop review (shown only while a relay waits on a review) ----------
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
    review_forward_edited = QtWidgets.QPushButton("Forward edited")
    review_skip = QtWidgets.QPushButton("Skip this hop")
    review_stop = QtWidgets.QPushButton("Stop relay")
    for btn in (review_forward, review_forward_edited,
                review_skip, review_stop):
        review_btn_row.addWidget(btn)
    review_btn_row.addStretch(1)
    review_layout.addLayout(review_btn_row)
    review_frame.setVisible(False)
    d_layout.addWidget(review_frame)

    relay_status = QtWidgets.QLabel("Relay off.")
    d_layout.addWidget(relay_status)
    root.addWidget(director)

    statusbar = window.statusBar()
    statusbar.showMessage("Loading models...")
    # Start Ollama lives in the status bar, where the Ollama-down note
    # appears -- and only shows when Ollama is actually down or
    # unresponsive, so the window stays uncrowded the rest of the time.
    start_ollama_btn = QtWidgets.QPushButton("Start Ollama")
    start_ollama_btn.setVisible(False)
    statusbar.addPermanentWidget(start_ollama_btn)

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
             "full_log_trimmed": False}

    # --- helpers ----------------------------------------------------------
    # Transcript formats: sender labels are bold, everything else
    # plain. Defined here (Qt lives in this function, not the module).
    _PLAIN_FMT = QtGui.QTextCharFormat()
    _BOLD_FMT = QtGui.QTextCharFormat()
    _BOLD_FMT.setFontWeight(QtGui.QFont.Weight.Bold)

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

    def append_message(side, label, text, markdown=False):
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
        same either way."""
        t = widgets[side]["transcript"]
        cursor = t.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        _block_gap(cursor)
        cursor.insertText(label + ": ", _BOLD_FMT)
        if markdown:
            cursor.insertHtml(markdown_to_html(text))
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
        plain text with the Markdown rendering."""
        t = widgets[side]["transcript"]
        cursor = t.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        _block_gap(cursor)
        cursor.insertText(label + ": ", _BOLD_FMT)
        widgets[side]["reply_body_pos"] = cursor.position()
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
        search is cleared, as for any transcript change."""
        body_pos = widgets[side].get("reply_body_pos")
        widgets[side]["reply_body_pos"] = None
        if body_pos is None:
            return
        t = widgets[side]["transcript"]
        doc = t.document()
        end = doc.characterCount() - 1
        if body_pos > end:
            return
        cursor = QtGui.QTextCursor(doc)
        cursor.setPosition(body_pos)
        cursor.setPosition(end, QtGui.QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertHtml(markdown_to_html(text))
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
        for msg in side_obj.messages:
            markdown = (msg.role == "assistant"
                        or msg.label == SUMMARY_LABEL)
            append_message(side, msg.label, msg.display_text(),
                           markdown=markdown)
        scroll_to_end(widgets[side]["transcript"])
        # A rebuild drops the highlights with the old document, so
        # the search state goes too (empty histories never reach
        # append_message, which clears it on its own).
        clear_search(side)

    # --- transcript search ---------------------------------------------------
    # Search is display-only: it highlights matches in the pane's
    # transcript and jumps between them, and never touches the
    # messages or the saved file. Matching is a case-insensitive plain
    # substring search over the displayed text -- no regex. Matches
    # are found through the document itself (QTextDocument.find), so
    # positions are always document positions, whatever characters
    # the text holds.
    _SEARCH_FMT = QtGui.QTextCharFormat()
    _SEARCH_FMT.setBackground(QtGui.QColor("yellow"))
    _SEARCH_CURRENT_FMT = QtGui.QTextCharFormat()
    _SEARCH_CURRENT_FMT.setBackground(QtGui.QColor("orange"))

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
        """Rebuild a pane's history-editor list from its history."""
        lst = widgets[side]["hist_list"]
        lst.blockSignals(True)
        lst.clear()
        side_obj = convo.sides[side]
        for msg in side_obj.messages:
            text = msg.display_text().replace("\n", " ")
            if len(text) > 70:
                text = text[:67] + "..."
            lst.addItem("%s: %s" % (msg.label, text))
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

    def refresh_buttons():
        busy = state["busy"]
        relay = state.get("relay")
        relay_active = relay is not None and relay.active
        a_ready = convo.sides["A"].model is not None
        b_ready = convo.sides["B"].model is not None
        send_a.setEnabled(not busy and a_ready)
        send_b.setEnabled(not busy and b_ready)
        send_both.setEnabled(not busy and a_ready and b_ready)
        # Attaching mid-generation or mid-relay makes no sense: the
        # attachments clear at the send, so there is nothing to add to.
        attach_idle = not busy and not relay_active
        attach_btn.setEnabled(attach_idle)
        for btn in attach_frame.findChildren(QtWidgets.QPushButton):
            btn.setEnabled(attach_idle)
        relay_btn.setText("Stop Relay" if relay_active else "Start Relay")
        relay_btn.setEnabled(
            relay_active or (not busy and a_ready and b_ready))
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
            w["picker"].setEnabled(not busy)
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

    def set_status(side, text):
        widgets[side]["status"].setText(text)

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

    def show_start_ollama(show):
        """Show the Start Ollama button when Ollama is down or
        unresponsive, hide it once Ollama is confirmed up again."""
        start_ollama_btn.setVisible(show)

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
            set_status(side, "Summarizing...")
            refresh_buttons()
            return
        state["queued"].discard(side)
        state["active"].add(side)
        set_status(side, "Thinking...")
        widgets[side]["reply_open"] = True
        begin_reply(side, convo.sides[side].model)
        refresh_buttons()

    def on_chunk(job_id, text):
        if not alive():
            return
        side, is_summary = _job_side(job_id)
        if is_summary:
            return
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
        # If nothing is still generating or queued (e.g. the relay ended
        # from a review pause, where no job runs), the window is idle
        # again -- no on_idle signal will come to say so.
        if not state["active"] and not state["queued"]:
            state["busy"] = False
        relay_status.setText("Relay stopped: %s." % reason)
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
            framed, label="You [relay from Model %s]" % from_side)
        append_message(next_side,
                       "You [relay from Model %s]" % from_side, framed)
        log_event(next_side, "You [relay from Model %s]" % from_side,
                  framed)
        refresh_history_list(next_side)
        session.current_side = next_side
        session.hop_from = from_side
        state["queued"].add(next_side)
        set_status(next_side, "Queued...")
        relay_status.setText(session.status_running())
        runner.submit(next_side, make_job_fn(
            next_side, convo.sides[next_side].model,
            convo.sides[next_side].messages_for_model()))

    def end_task(status_message=None):
        """End the active task. The statusbar says how it ended."""
        if state.get("task") is None:
            return
        state["task"].active = False
        state["task"] = None
        logger.action("task ended: %s" % (status_message or "done"))
        if status_message:
            statusbar.showMessage(status_message, 4000)
        refresh_buttons()

    def show_task_progress(session):
        """The status bar names the round a task chain is on. A
        one-round task shows nothing -- it is over before a progress
        line would mean anything."""
        if session.rounds > 1:
            statusbar.showMessage(
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
            display=note)
        append_message(next_side, "You [task handoff]", note)
        log_event(next_side, "You [task handoff]", note)
        refresh_history_list(next_side)
        session.phase = "followup"
        state["queued"].add(next_side)
        set_status(next_side, "Queued...")
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
        relay_status.setText("Relay paused for review: turn %d/%d."
                             % (session.turns_done + 1, session.turn_cap))
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
        set_status(side, "Idle")
        if not text.strip():
            statusbar.showMessage(
                "Summarize came back empty — history unchanged.", 4000)
            refresh_buttons()
            return
        if convo.sides[side].summarize(text):
            render_transcript(side)
            refresh_history_list(side)
            statusbar.showMessage("History summarized.", 3000)
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
        set_status(side, "Idle")
        # Stats for this turn (every turn, relay turns included).
        stats = _take_job_stats(job_id, side)
        if stats is not None:
            state["stats"][side] = stats
            refresh_stats_label(side)
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
        if is_summary:
            state["summarizing"] = None
            # A failed summarize contributes nothing -- no counts,
            # and the stats line is not a summarize turn's to touch.
            job_stats.pop(job_id, None)
            append_note(side, "(Summarize failed: %s)" % message)
            set_status(side, "Idle")
            logger.error("summarize failed: side %s: %s" % (side, message))
            refresh_buttons()
            return
        finish_reply(side)
        widgets[side]["reply_body_pos"] = None
        append_note(side, "(%s)" % message)
        state["active"].discard(side)
        set_status(side, "Idle")
        # The failed turn has no numbers: its stats show n/a, and
        # nothing is added to the session total.
        stats = _take_job_stats(job_id, side)
        if stats is not None:
            state["stats"][side] = stats
            refresh_stats_label(side)
        logger.error("turn failed: side %s, model %s: %s"
                     % (side, convo.sides[side].model, message))
        # Ollama down or unresponsive: offer the Start Ollama button.
        # A slow Ollama gets the offer too -- restarting it is the
        # sensible remedy for both, and the button reports its own
        # outcome honestly.
        if message in (MSG_OLLAMA_DOWN, MSG_OLLAMA_SLOW):
            show_start_ollama(True)
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
        if is_summary:
            # No button cancels a summarize turn, but stay safe if one
            # ever does: the history is simply left as it was.
            state["summarizing"] = None
            set_status(side, "Idle")
            refresh_buttons()
            return
        finish_reply(side)
        widgets[side]["reply_body_pos"] = None
        append_note(side, "(%s)" % MSG_STOPPED)
        state["active"].discard(side)
        state["queued"].discard(side)
        set_status(side, "Idle")
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
            statusbar.showMessage(" ".join(notes), 5000)
        elif added:
            statusbar.showMessage(
                "Attached: %s." % ", ".join(added), 3000)
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
            statusbar.clearMessage()

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
            statusbar.showMessage(
                "Couldn't save to %s: %s" % (path, reason), 5000)
            return False
        statusbar.showMessage("Saved to %s" % path, 5000)
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
            statusbar.showMessage(
                "Couldn't import that file: %s" % exc, 5000)
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
        set_status(side, "Idle")
        refresh_buttons()
        statusbar.showMessage(
            "Transcript imported from %s" % path, 5000)

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
            statusbar.showMessage(
                "Couldn't save to %s: %s" % (path, reason), 5000)
            return
        statusbar.showMessage("Conversation saved to %s" % path, 5000)

    def _apply_conversation(new_convo, new_log):
        """Swap the loaded conversation into the window: both panes'
        histories, labels, instructions, and models, plus the
        full-transcript log. Models that are no longer installed stay
        unpicked -- the picker is left blank for that side. Returns
        the list of missing model names."""
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
            if model and picker.findText(model) >= 0:
                picker.setCurrentIndex(picker.findText(model))
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
            set_status(side, "Idle")
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
            statusbar.showMessage(
                "Couldn't open that file: %s" % exc, 5000)
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
            statusbar.showMessage(
                "Conversation opened, but not installed: %s."
                % ", ".join(missing), 5000)
        else:
            statusbar.showMessage(
                "Conversation opened from %s" % path, 5000)

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
            statusbar.showMessage(
                "Couldn't save to %s: %s" % (path, reason), 5000)
            return
        logger.action("debug report written to %s" % written)
        statusbar.showMessage("Debug report saved to %s" % written, 5000)

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
            set_status(side, "Queued...")
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
            statusbar.showMessage(
                err, 3000 if err == MSG_EMPTY else 4000)
            return
        composer.clear()
        clear_attachments()
        composer.setFocus()

    send_a.clicked.connect(lambda: do_send("A"))
    send_b.clicked.connect(lambda: do_send("B"))
    send_both.clicked.connect(lambda: do_send("Both"))

    # --- keyboard send ----------------------------------------------------
    # Ctrl+Enter sends to both models, from the keyboard. With the
    # "Enter to send" box ticked, plain Enter sends too (Shift+Enter
    # still adds a new line). A send key is always swallowed -- it
    # never inserts a newline -- and it only fires while the Send to
    # Both button is enabled (idle, both models picked), so the
    # keyboard path has exactly the button's guards. Empty composer:
    # do_send shows the same "Type a message first." note as a click.
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
            statusbar.showMessage(
                MSG_NO_MODEL.format(side="Model " + side), 4000)
            return
        if len(side_obj.messages) <= SUMMARY_KEEP_LAST:
            statusbar.showMessage("Nothing to summarize yet.", 3000)
            return
        state["busy"] = True
        state["summarizing"] = side
        # A summarize turn is not a compare round: the offer is spent.
        state["compare"] = {}
        state["compare_round"] = False
        messages = side_obj.messages_for_model() + [
            {"role": "user", "content": SUMMARIZE_PROMPT}]
        set_status(side, "Summarizing...")
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
            statusbar.showMessage(MSG_EMPTY, 3000)
            return
        if (convo.sides["A"].model is None
                or convo.sides["B"].model is None):
            statusbar.showMessage(MSG_RELAY_NEEDS_BOTH, 4000)
            return
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
        composer.clear()
        clear_attachments()
        side_obj = convo.sides[start_side]
        side_obj.add_user(full, display=display)
        shown = display if display else text
        append_message(start_side, "You", shown)
        log_event(start_side, "You", shown)
        refresh_history_list(start_side)
        state["queued"].add(start_side)
        set_status(start_side, "Queued...")
        relay_status.setText(session.status_running())
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
            statusbar.showMessage(MSG_TASK_EMPTY, 3000)
            return
        if task_worker.currentIndex() < 0:
            statusbar.showMessage(MSG_TASK_NEEDS_WORKER, 3000)
            return
        if task_follow_up.currentIndex() < 0:
            statusbar.showMessage(MSG_TASK_NEEDS_FOLLOW_UP, 3000)
            return
        worker = "A" if task_worker.currentIndex() == 0 else "B"
        follow_up = TASK_FOLLOW_UPS[task_follow_up.currentIndex()]
        rounds = task_rounds.value()
        worker_obj = convo.sides[worker]
        if worker_obj.model is None:
            statusbar.showMessage(
                MSG_NO_MODEL.format(side="Model " + worker), 4000)
            return
        other = RelaySession.other(worker)
        if follow_up != "nothing" and convo.sides[other].model is None:
            statusbar.showMessage(
                MSG_NO_MODEL.format(side="Model " + other), 4000)
            return
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
        set_status(worker, "Queued...")
        show_task_progress(state["task"])
        runner.submit(worker, make_job_fn(
            worker, worker_obj.model, worker_obj.messages_for_model()))
        refresh_buttons()

    task_run.clicked.connect(do_run_task)
    task_edit.textChanged.connect(refresh_buttons)
    task_worker.currentIndexChanged.connect(refresh_buttons)
    task_follow_up.currentIndexChanged.connect(refresh_buttons)

    # --- hop review --------------------------------------------------------
    def on_review_forward():
        hop = state.get("pending_hop")
        if hop is not None:
            do_forward(hop["framed"])

    def on_review_forward_edited():
        text = review_edit.toPlainText()
        if not text.strip():
            statusbar.showMessage(MSG_REVIEW_EMPTY, 3000)
            return
        do_forward(text)

    def on_review_skip():
        hop = state.get("pending_hop")
        from_side = hop["from_side"] if hop is not None else None
        end_relay("hop skipped by you", note_side=from_side)

    review_forward.clicked.connect(on_review_forward)
    review_forward_edited.clicked.connect(on_review_forward_edited)
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
        set_status(other, "Queued...")
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
            widgets[side]["instr_toggle"].setText(
                "Instructions ▾" if checked else "Instructions ▸")
        return toggled

    def make_hist_toggle(side):
        def toggled(checked):
            widgets[side]["hist_frame"].setVisible(checked)
            widgets[side]["hist_toggle"].setText(
                "History ▾" if checked else "History ▸")
            if checked:
                refresh_history_list(side)
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

    # --- saved pairs ---------------------------------------------------------
    def refresh_pair_combo(select=None):
        """Reload the pair dropdown from the JSON file. Nothing is
        loaded by default -- the dropdown just lists what is saved."""
        pairs = load_pairs(pairs_path())
        pair_combo.blockSignals(True)
        pair_combo.clear()
        names = sorted(pairs)
        pair_combo.addItems(names)
        if select in names:
            pair_combo.setCurrentIndex(names.index(select))
        else:
            pair_combo.setCurrentIndex(-1)
        pair_combo.blockSignals(False)
        # The Delete button follows the selection, and this rebuild
        # runs with signals blocked, so refresh the buttons here.
        refresh_buttons()
        return pairs

    def on_pair_save():
        name = pair_name.text().strip()
        if not name:
            statusbar.showMessage(MSG_PAIR_NEEDS_NAME, 3000)
            return
        setup = {
            "model_a": convo.sides["A"].model,
            "model_b": convo.sides["B"].model,
            "instruction_a": widgets["A"]["instr_edit"].toPlainText(),
            "instruction_b": widgets["B"]["instr_edit"].toPlainText(),
        }
        try:
            save_pair(pairs_path(), name, setup)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            statusbar.showMessage(
                "Couldn't save pair '%s': %s" % (name, reason), 5000)
            return
        refresh_pair_combo(select=name)
        statusbar.showMessage("Pair '%s' saved." % name, 3000)

    def on_pair_selected(index):
        if index < 0:
            return
        name = pair_combo.currentText()
        setup = load_pairs(pairs_path()).get(name)
        if setup is None:
            return
        missing = []
        for side, key in (("A", "model_a"), ("B", "model_b")):
            model = setup.get(key)
            if not model:
                continue
            picker = widgets[side]["picker"]
            found = picker.findText(model)
            if found >= 0:
                picker.setCurrentIndex(found)
            else:
                missing.append(model)
        widgets["A"]["instr_edit"].setPlainText(
            setup.get("instruction_a") or "")
        widgets["B"]["instr_edit"].setPlainText(
            setup.get("instruction_b") or "")
        if missing:
            statusbar.showMessage(
                "Pair '%s' loaded, but not installed: %s."
                % (name, ", ".join(missing)), 5000)
        else:
            statusbar.showMessage("Pair '%s' loaded." % name, 3000)
        refresh_buttons()

    def on_pair_delete():
        index = pair_combo.currentIndex()
        if index < 0:
            return
        name = pair_combo.currentText()
        answer = QtWidgets.QMessageBox.question(
            window, "Delete pair?",
            "Delete the saved pair '%s'? This can't be undone." % name,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            delete_pair(pairs_path(), name)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            statusbar.showMessage(
                "Couldn't delete pair '%s': %s" % (name, reason), 5000)
            return
        refresh_pair_combo()
        statusbar.showMessage("Pair '%s' deleted." % name, 3000)

    pair_save.clicked.connect(on_pair_save)
    pair_delete.clicked.connect(on_pair_delete)
    # Both signals: currentIndexChanged covers picking a different pair,
    # activated covers re-picking the pair that is already selected
    # (choosing the same item again fires no index change, but the user
    # still expects the load). Loading twice is harmless -- it applies
    # the same setup.
    pair_combo.currentIndexChanged.connect(on_pair_selected)
    pair_combo.activated.connect(on_pair_selected)

    # --- relay presets -------------------------------------------------------
    # Same shape as the saved pairs above: reload the dropdown from
    # the JSON file, save the row as it stands, load a preset back
    # onto the row, delete with a confirmation first.
    def refresh_preset_combo(select=None):
        """Reload the preset dropdown from the JSON file. Nothing is
        loaded by default -- the dropdown just lists what is saved.
        A file that exists but won't parse earns a plain note; the
        row itself is left exactly as it is."""
        presets = load_presets(presets_path())
        preset_combo.blockSignals(True)
        preset_combo.clear()
        names = sorted(presets)
        preset_combo.addItems(names)
        if select in names:
            preset_combo.setCurrentIndex(names.index(select))
        else:
            preset_combo.setCurrentIndex(-1)
        preset_combo.blockSignals(False)
        if presets_file_corrupt(presets_path()):
            statusbar.showMessage(
                "Couldn't read the saved relay presets.", 5000)
        # The Delete button follows the selection, and this rebuild
        # runs with signals blocked, so refresh the buttons here.
        refresh_buttons()
        return presets

    def on_preset_save():
        name = preset_name.text().strip()
        if not name:
            statusbar.showMessage(MSG_PRESET_NEEDS_NAME, 3000)
            return
        config = {
            "start_side": start_picker.currentText(),
            "mode": ("aware" if mode_picker.currentText() == "Aware"
                     else "blind"),
            "turn_cap": cap_spin.value(),
            "stop_phrases": parse_stop_phrases(stop_edit.text()),
            "stop_on_agreement": agree_check.isChecked(),
            "review_each_hop": review_check.isChecked(),
        }
        try:
            save_preset(presets_path(), name, config)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            statusbar.showMessage(
                "Couldn't save relay preset '%s': %s" % (name, reason),
                5000)
            return
        refresh_preset_combo(select=name)
        statusbar.showMessage("Relay preset '%s' saved." % name, 3000)

    def on_preset_selected(index):
        if index < 0:
            return
        name = preset_combo.currentText()
        config = load_presets(presets_path()).get(name)
        if config is None:
            # Gone (or gone bad) since the dropdown was filled --
            # say so plainly and leave the row alone.
            statusbar.showMessage(
                "Relay preset '%s' couldn't be loaded." % name, 5000)
            return
        start_picker.setCurrentText(config["start_side"])
        mode_picker.setCurrentText(
            "Aware" if config["mode"] == "aware" else "Blind")
        cap_spin.setValue(config["turn_cap"])
        stop_edit.setText(", ".join(config["stop_phrases"]))
        agree_check.setChecked(config["stop_on_agreement"])
        review_check.setChecked(config["review_each_hop"])
        statusbar.showMessage("Relay preset '%s' loaded." % name, 3000)
        refresh_buttons()

    def on_preset_delete():
        index = preset_combo.currentIndex()
        if index < 0:
            return
        name = preset_combo.currentText()
        answer = QtWidgets.QMessageBox.question(
            window, "Delete relay preset?",
            "Delete the saved relay preset '%s'? This can't be "
            "undone." % name,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            delete_preset(presets_path(), name)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            statusbar.showMessage(
                "Couldn't delete relay preset '%s': %s" % (name, reason),
                5000)
            return
        refresh_preset_combo()
        statusbar.showMessage("Relay preset '%s' deleted." % name, 3000)

    preset_save.clicked.connect(on_preset_save)
    preset_delete.clicked.connect(on_preset_delete)
    # Both signals, as with pairs: currentIndexChanged covers picking
    # a different preset, activated covers re-picking the preset that
    # is already selected.
    preset_combo.currentIndexChanged.connect(on_preset_selected)
    preset_combo.activated.connect(on_preset_selected)

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
            convo.sides[side].clear()
            widgets[side]["transcript"].clear()
            widgets[side]["reply_body_pos"] = None
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
            set_status(side, "Idle")
            refresh_buttons()
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
            convo.sides[side].model = (
                picker.currentText() if index >= 0 else None)
            # A different model makes the last turn's stats stale.
            state["stats"][side] = None
            refresh_stats_label(side)
            refresh_buttons()
        return changed

    for side in ("A", "B"):
        widgets[side]["picker"].currentIndexChanged.connect(
            on_picker_changed(side))

    def populate_models(models):
        if not alive():
            return
        for side in ("A", "B"):
            picker = widgets[side]["picker"]
            previous = convo.sides[side].model
            picker.blockSignals(True)
            picker.clear()
            picker.addItems(models)
            if previous in models:
                picker.setCurrentIndex(models.index(previous))
            else:
                # Nothing selected by default, ever.
                picker.setCurrentIndex(-1)
                convo.sides[side].model = None
            picker.blockSignals(False)
        if models:
            statusbar.showMessage(
                "Ollama connected: %d model(s) found." % len(models))
        else:
            statusbar.showMessage(
                "Ollama connected but no models found. Pull a model first.")
        show_start_ollama(False)
        refresh_buttons()

    def on_models_failed(message):
        if not alive():
            return
        statusbar.showMessage(message)
        if message in (MSG_OLLAMA_DOWN, MSG_OLLAMA_SLOW):
            show_start_ollama(True)
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
        statusbar.showMessage("Loading models...")
        loader = _ModelLoader(client, signals, on_result=_on_probe_result)
        loaders.append(loader)
        loader.thread.finished.connect(
            lambda: loaders.remove(loader) if loader in loaders else None)
        loader.thread.start()

    # --- Start Ollama ------------------------------------------------------
    starters = []

    def do_start_ollama():
        """Start Ollama from inside DUO (off the GUI thread -- the
        wait for the probe can take seconds)."""
        logger.action("Start Ollama pressed")
        start_ollama_btn.setEnabled(False)
        statusbar.showMessage("Starting Ollama...")
        starter = _OllamaStarter(ollama_manager, signals)
        starters.append(starter)
        starter.thread.finished.connect(
            lambda: starters.remove(starter) if starter in starters else None)
        starter.thread.start()

    def on_ollama_started(ok, message):
        if not alive():
            return
        start_ollama_btn.setEnabled(True)
        logger.action("Start Ollama finished: %s" % message)
        statusbar.showMessage(message, 5000)
        if ok:
            show_start_ollama(False)
            load_models()
        else:
            show_start_ollama(True)
        refresh_buttons()

    signals.ollama_started.connect(on_ollama_started)
    start_ollama_btn.clicked.connect(do_start_ollama)

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
        checker = _UpdateChecker(signals, APP_VERSION, manual)
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
            statusbar.showMessage(
                MSG_UPDATE_AVAILABLE % version, 5000)
        elif manual and status == "latest":
            logger.info("update check: up to date")
            statusbar.showMessage(MSG_UPDATE_LATEST, 5000)
        elif manual and status == "failed":
            logger.info("update check failed")
            statusbar.showMessage(MSG_UPDATE_FAILED, 5000)
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

    window._dual_chat = {  # test hooks (not part of the UI)
        "convo": convo, "runner": runner, "signals": signals,
        "widgets": widgets, "composer": composer, "panes": panes,
        "enter_send_check": enter_send_check,
        "send_a": send_a, "send_b": send_b, "send_both": send_both,
        "do_send": do_send, "load_models": load_models, "state": state,
        "relay_btn": relay_btn, "start_picker": start_picker,
        "cap_spin": cap_spin, "mode_picker": mode_picker,
        "stop_edit": stop_edit, "agree_check": agree_check,
        "relay_status": relay_status,
        "do_start_relay": do_start_relay, "stop_relay": stop_relay,
        "review_check": review_check, "review_frame": review_frame,
        "review_label": review_label, "review_edit": review_edit,
        "review_forward": review_forward,
        "review_forward_edited": review_forward_edited,
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
        "refresh_history_list": refresh_history_list,
        "do_summarize": do_summarize, "refresh_buttons": refresh_buttons,
        "attach_btn": attach_btn, "attach_frame": attach_frame,
        "attach_note": attach_note, "add_attachments": add_attachments,
        "remove_attachment": remove_attachment,
        "clear_attachments": clear_attachments,
        "task_edit": task_edit, "task_worker": task_worker,
        "task_follow_up": task_follow_up, "task_rounds": task_rounds,
        "task_run": task_run,
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
        "start_ollama_btn": start_ollama_btn,
        "ollama_manager": ollama_manager,
        "do_start_ollama": do_start_ollama,
        "show_start_ollama": show_start_ollama,
        "send_message": send_message,
        "log_event": log_event,
        "phone_btn": phone_btn, "deck_server": deck_server,
        "show_phone_deck": show_phone_deck,
        "on_phone_send": on_phone_send,
        "update_btn": update_btn, "run_update_check": run_update_check,
        "on_update_checked": on_update_checked,
    }

    # Closing the window stops a server DUO itself started -- no orphan
    # `ollama serve` processes left behind. A server started any other
    # way is untouched.
    _orig_close_event = window.closeEvent

    def _on_close(event):
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
        ollama_manager.stop()
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
    logger = make_debug_logger()
    logger.install_crash_handler()
    window = build_window(OllamaClient(base_url), logger=logger)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
