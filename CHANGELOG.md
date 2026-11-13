# DUO changelog

Every version, newest first. v0.1–v0.17 were built on 2026-11-12;
v0.18–v0.24 on 2026-11-13.

## v0.24 — 2026-11-13

Streaming Markdown rendering. Model replies now show formatted in
the panes instead of raw markup: bold, italic, inline code, fenced
code blocks, headers, lists, blockquotes, and links (clicking one
opens it in the browser). While a reply streams it stays plain text,
and the moment the turn finishes it formats in place; a failed or
stopped turn stays plain. Only model replies render — your own
messages stay exactly as typed, and so do the relay and task notes.
Nothing stored changes: history, saved transcripts, conversation
files, and the phone deck all keep the raw text, and search still
finds words inside a formatted reply. A reply containing raw HTML
shows it as text, never as markup, and only http/https links
become clickable.

## v0.23 — 2026-11-13

Transcript search and import. Each pane has a search row under its
transcript: type to find text in that conversation (case-insensitive,
plain substring, no regex), with every match highlighted and Previous
/ Next stepping between them, wrapping at the ends. Searching never
changes the conversation — any new message, re-render, or clear drops
the search. Each pane also has an Import button that loads a saved
single-pane transcript back into the pane, replacing its conversation
(it asks first when the pane already has messages). The imported
messages join the pane's history, so the next send includes them.
The picked model is untouched — the file's model names are display
labels only — and the imported entries go at the end of the full
transcript, since the file has no timestamps to interleave by.
"Save both" and full-transcript files are refused plainly: import
takes a single-side transcript only. Also in this version, the
.gitignore now covers secret files (.env, providers.json, key
files, local config) so a key or token can never be committed by
accident — the tree and repo history were audited clean first.

## v0.22 — 2026-11-13

Multi-round tasks. A task no longer stops after one follow-up: the
Rounds box (1–10, default 1) says how many rounds it runs, and each
round passes the work to the other side. "Continue it" keeps
continuing; a review chain alternates review turns with revise
turns, handing each review back to the worker to revise from.
"Nothing" is always one round, and its Rounds box stays off. With
Review it or Continue it the task always runs at least 2 rounds,
so Rounds 1 is the same task as before: the worker, then one
follow-up round. The
status bar shows which round a chain is on, Stop still ends it
mid-chain, and a failed round ends the chain with a plain note
while the earlier results stay put.

## v0.21 — 2026-11-13

Relay presets. The whole relay row — start side, mode, turn cap,
stop phrases, stop-on-agreement, review-each-hop — can be saved
under a name and loaded back from a dropdown, the same way saved
model pairs work. Presets live in duo-relay-presets.json next to
the app, written atomically; nothing loads by itself, and deleting
one asks first. A preset file that won't parse earns a plain note
instead of a crash, and a preset with bad fields is skipped.

## v0.20 — 2026-11-13

Token counts. The stats line under each pane now shows how many
tokens the last turn used — generated and prompt, straight from
Ollama's done chunk, never estimated. Underneath it, each pane
keeps a running session total across every generation since the
app started — sends, relay hops, tasks, and summaries. A failed
turn shows n/a and adds nothing; Clear never resets the session
total.

## v0.19 — 2026-11-13

Temperature controls. Each pane has a Temperature control under its
model picker (0.0–2.0, default 0.7 — that is Ollama's own default,
now visible instead of silent). The value travels with every turn
that side generates — direct sends, relay hops, task runs, and
summaries — and is remembered per side between runs.

## v0.18 — 2026-11-13

Atomic writes. The saved pairs file and all three transcript saves
(one side, both sides, full transcript) now write to a temp file
first and rename it over the target — the same step the conversation
save already used. A crash or a failed write can never leave a
half-written file behind: the old file stays intact, and a failed
save says so plainly instead of crashing.

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
