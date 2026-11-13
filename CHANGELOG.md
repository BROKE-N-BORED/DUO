# DUO changelog

Every version, newest first. All of v0.1–v0.17 were built on
2026-11-12.

## v0.17 — 2026-11-12

Conversation persistence. Save conversation writes the whole
conversation to a JSON file — both sides' histories with their
labels, both models, both instruction fields, and the interleaved
full-transcript log — and Open conversation loads it back exactly.
A saved model that isn't installed anymore stays unpicked, with a
plain note naming it. Opening replaces the conversation on screen,
so it asks first.

## v0.16 — 2026-11-12

The front door: this changelog, a .gitignore, a CI workflow that runs
the test suite on every push, a .desktop launcher the installer puts
in the app menu (with an icon drawn from the window icon), and an
uninstaller that asks first. The license is still to be decided by
the project owner.

## v0.15 — 2026-11-12

The window finished: its size, position, and the pane split are
remembered between runs (clamped to the screen, so a stale restore
can never strand it off-screen); it has a window icon, drawn in
code; the title bar shows the version; and saved pairs can be
deleted, with a confirmation first.

## v0.14 — 2026-11-12

The first-run path guarded. If PySide6 is missing, DUO says so in
plain language and exits cleanly — never a raw traceback.
install.sh pins PySide6-Essentials and checks the install before
calling it done; run.sh no longer falls back to system Python.

## v0.13 — 2026-11-12

Keyboard send and the composer cap gone. Ctrl+Enter sends to both
models, and an "Enter to send" tickbox (remembered between runs)
makes plain Enter send too, with Shift+Enter still adding a new
line. The composer no longer drops pasted paragraphs — the 5-block
cap is removed entirely.

## v0.12 — 2026-11-12

The agreement matcher fixed. Stop phrases and agreement phrases now
match whole words only — "I disagreed" no longer stops a relay that
was waiting for "agreed", and "cap" no longer stops on "capital".

## v0.11 — 2026-11-12

The phone deck. DUO serves a small local page on the PC: a QR code
in the window opens it on your phone, the page follows whichever
model is thinking, you can send messages from the phone, and one
Export button downloads the full transcript. LAN-only, no accounts.

## v0.10 — 2026-11-12

Ollama reliability. Chat turns retry transient failures with a pause
between attempts and wait as long as Code Forge does; a Start Ollama
button starts the server from inside DUO when it is down; and a
debug log layer records events and writes a report to email when
something breaks. The log never holds message or file contents.

## v0.9 — 2026-11-12

Chat layout fixed (blank line between messages, bold sender labels,
explicit word wrap) and the full transcript export: both sides
interleaved in the order the conversation actually happened.

## v0.8 — 2026-11-12

Final pass on the original plan: the stale "Attached: ..." status
note after a send fixed, and the README rewritten for the complete
app.

## v0.7 — 2026-11-12

Transcripts. Save one side's conversation, or both sides in one
file, as plain text that reads like the window.

## v0.6 — 2026-11-12

Tasks. Hand a job to one model: it answers, and then the other model
reviews the result, continues it, or nothing happens — one round,
no loop.

## v0.5 — 2026-11-12

File upload. Attach up to 5 text files (100 KB each) to a message:
their contents ride along with the typed message as plain text
blocks the model can read.

## v0.4 — 2026-11-12

Long-loop support. Summarize folds a long conversation into a short
summary so it keeps fitting a small model's context, a nudge
suggests it when a history gets long, and every turn shows its speed
and the model's RAM.

## v0.3 — 2026-11-12

Director controls. Reorder or delete messages in a side's history,
review each relay hop before it forwards, per-side instructions,
saved model pairs, and compare mode.

## v0.2 — 2026-11-12

The auto-relay AI-to-AI loop, blind or aware, with a turn cap and
stop conditions. The app is named DUO from this version on.

## v0.1 — 2026-11-12

The first version: two Ollama models side by side in one desktop
window, each with its own model picker, sending to A, to B, or to
both at once.
