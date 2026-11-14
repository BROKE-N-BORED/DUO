# DUO changelog

Every version, newest first. v0.1–v0.17 were built on 2026-11-12;
v0.18–v0.35 on 2026-11-13; v0.36–v0.42 on 2026-11-14.

## v0.42 — 2026-11-14

The fields explain themselves now, in plain words next to the
field — not hidden in tooltips. Temperature reads "Lower is more
focused, higher is more creative." next to the number. The relay
rows say what Start with, Turns, and Blind mean, and what a stop
phrase does. The instructions editor says it is sent to that
side's model with every message, and the task controls say what
the worker, the follow-up, and the rounds each do. The labels
sit beside a field where its row has empty space, and underneath
where the layout has room — inside sections that start closed —
so the window stays the same height and the first-run guide
still fits. The model picker, the composer, the search boxes,
and the pair and preset name fields carry no explanation line:
their placeholders (and, for the picker, the size and fit
labels) already say what they are for. Under the hood, the debug
logger fallback and the attachment helpers moved into their own
small modules (duo_debug.py, duo_attachments.py).

## v0.41 — 2026-11-14

Saved pairs carry the whole setup now. A pair used to remember
only the two models (and the instructions); it now remembers each
side's temperature too, so loading a pair puts the whole setup
back exactly as it was. Pairs saved before this version still
load fine: anything the old pair doesn't hold is left exactly as
it is, never reset. And picking a pair or a relay preset is no
longer a blind commit — hovering over one shows what loading it
would set: both models, both temperatures, and a preview of the
instructions for a pair; all six relay fields for a preset.

## v0.40 — 2026-11-14

Each side has its own color now. Model A is blue, Model B is
purple: the pane titles, the model names in the chat, and the
history lists all carry the side's color, so you can tell the two
sides apart at a glance. A relayed message keeps the color of the
side it came from — when a message from A lands in B's pane, its
label is blue, so you always know where it came from. The same
goes for task handoffs. Your own messages stay the plain text
color, and nothing about the colors is ever saved or exported:
transcripts and conversation files stay plain text. Under the
hood, the relay session moved into duo_model.py, next to the
conversation model it belongs with.

## v0.39 — 2026-11-14

First run teaches the loop now. When you open DUO with no
conversation yet, each chat pane shows three steps: pick a model
for each side, send a message to both, start a relay and watch
them talk. Step 1 gets a checkmark once both models are picked.
The guide disappears the moment you send your first message (or
open a conversation, or import one) and never comes back. It is
only ever on screen — it never gets saved, exported, or sent to
a model. Under the hood, the background workers (the model
loader, the Ollama starter, the update checker) moved into their
own file, duo_workers.py, so the main window file stays under
its size limit. Fixed along the way: at the old default window
size the chat panes were too short for the transcript, so the
bottom of a reply hid behind the search row — the window now
opens a little taller (1100x700, still fits a 1366x768 screen)
with tighter spacing and the session total sharing the activity
row, and the whole guide and the whole reply stay visible.

## v0.38 — 2026-11-14

The history editor is resizable now. Each pane's message list
used to be a fixed height — now there is a divider between the
chat and the history list, and you drag it to make the list
taller or shorter. DUO remembers the split per pane. The divider
cannot crush either side: the chat never drops below 100 pixels
and the list never below 60. Closing the editor keeps your size,
so it opens back where you left it.

## v0.37 — 2026-11-14

The director's controls live in three collapsible sections now,
under the message box and the button row, which stay visible:
Relay, Pairs, and Tasks. Each section has its own header you
click to open or close it, and DUO remembers your choice. Relay
starts open, Pairs and Tasks start closed. The relay controls
sit in two rows instead of one crammed row, so the stop-words
field keeps a proper width even at the narrowest window size.
Closing a section never stops running work — a relay running
with its section closed still shows on the chip above the
Director toggle.

## v0.36 — 2026-11-14

Status-bar notes stay now. They used to vanish after three to five
seconds, so looking away meant missing them — now a note stays
until the next note replaces it. The words are all the same, and
the CPU/RAM meter on the right of the bar keeps updating as
before.

## v0.35 — 2026-11-13

The Start Ollama button moved out of the status bar into the bottom
button row, next to Phone deck and Check for updates. It used to
hide there and only appear after a failure, so it was hard to find
exactly when you needed it — now it is always in the row, and it is
greyed out while Ollama answers, so it can never start a second
server while one is already running. When Ollama is down or
unresponsive the button is ready to press, and it starts Ollama
from inside DUO exactly as before. The status bar now holds only
the status message and the CPU/RAM meter.

## v0.34 — 2026-11-13

The relay's status moved out of the director into a chip that sits
under the panes, above the Director toggle: a dot, the status words,
and a Stop button. It used to live at the bottom of the director, so
collapsing the director hid the relay's state entirely — now the
chip stays visible either way, and it is the only relay status
display. Grey means the relay is off, green means it is running
(with the turn count and which side is answering), amber means it is
paused for your review. The Stop button only shows while a relay is
running, and it stops the relay exactly like the relay row's Stop
button. When a relay ends, the chip holds the reason until the next
relay starts.

## v0.33 — 2026-11-13

The model pickers show size and fit now. Each model lists its size
and whether it fits this PC in plain words — "lfm3:1.7b - 1.1 GB -
fits this PC" — so you never have to guess at sizes or specs. A
model that is too big for this PC is still listed and still
selectable; the label only tells you what to expect. While the
list loads, the pickers show "Loading models..." instead of sitting
empty.

## v0.32 — 2026-11-13

Clear asks before it wipes. Clicking Clear on a pane that has
messages now shows a plain question — "This clears 2 messages from
Model A. Keep going?" — with Yes and No, and No is the default, so
a misclick or an Escape keypress loses nothing. A pane with no
messages still clears straight away, with no question, since there
is nothing to lose.

## v0.31 — 2026-11-13

The director bar collapses now. A "Director" toggle sits on the
boundary between the panes and the director rows: click it and the
controls hide so the panes take the whole window; click it again
and everything comes back exactly as it was. The toggle stays
visible either way, so the controls are always one click away, and
your choice is remembered between runs. Collapsing never touches
running work: a relay or task in flight keeps running while the
rows are hidden, and the activity lights and the CPU/RAM meter
stay visible. Behind the scenes, the phone deck moved into its own
file (duo_deck.py) to make room: the window file was 16 lines under
its 4,000-line bound. Nothing about the deck changed. Also fixed:
closing the window while a background check was still running
could crash the app on the way out; closing now waits for those
threads to finish first.

## v0.30 — 2026-11-13

DUO has a menu bar now, across the top of the window. File holds
Save conversation (Ctrl+S), Open conversation (Ctrl+O), Save full
transcript (Ctrl+Shift+S), Save both, Debug report, and Quit
(Ctrl+Q); Relay holds Start relay and Stop relay; Help holds Check
for updates, How to use, and About. Nothing new is behind the menu
except the How to use index itself — every other item calls the
same thing its button already calls, and the menu items grey out
exactly when their buttons do. How to use is a built-in index that
explains the panes, sending, the relay, tasks, files, transcripts,
saved pairs and presets, the phone deck, and the update check, in
plain language and with no web. The shortcuts work from anywhere in
the window and don't touch the composer keys: Ctrl+Enter still
sends to both, and Enter still starts a new line unless "Enter to
send" is ticked. Also in this version, the visible text drops the
em-dash: the update notes, the temperature tooltip, the long-history
nudge, and the empty-summary note all use plain punctuation now.

## v0.29 — 2026-11-13

You can see what DUO is doing now. Each pane has a small colored
light and small words under its stats line saying what that side
is doing right now: grey "Idle" when it isn't doing anything,
amber "Queued…" while it waits its turn, amber "Waiting for
Ollama…" once its turn has started but no reply has come back
yet, green "Thinking…" while its reply streams in, green
"Summarizing…" during a summarize, amber "Waiting for you to
review…" while a relay hop is parked for your review, and red
"Something went wrong" for a few seconds when a turn fails. And
the status bar shows the whole machine's CPU % and RAM, re-read
once a second from /proc — plain numbers like "CPU 42% · RAM 3.1
/ 7.7 GB", with no new dependency. On a machine without /proc
the meter stays empty and silent instead of complaining.

## v0.28 — 2026-11-13

DUO tells you when a newer version is out. Once at startup, on a
background thread that never holds the window up, it asks GitHub
for the latest Release and, when one is newer than the copy
running, says so in the status bar — "DUO 0.29 is out — see the
Releases page on GitHub." When the copy is current, or when there
is no network, the startup check says nothing. A "Check for
updates" button next to Phone deck runs the same check whenever
you like and reports either way: the same note when a newer
version is out, "You're on the latest version." when there
isn't, and a plain note when the check itself fails. DUO never
downloads or installs anything — the note just points at the
Releases page, and you download from GitHub yourself.

## v0.27 — 2026-11-13

The full transcript log is bounded now. It used to grow without
limit across a long session — every send, relay hop, and task
appended forever. It now keeps the most recent 2,000 entries and
drops the oldest first when a session passes that. Nothing you
can see changes in a normal session: 2,000 is far above any real
conversation. If a session ever does pass it, the saved full
transcript says so at the top, so a file never silently loses old
messages. Your per-pane conversations are untouched — only the
combined log is capped.

## v0.26 — 2026-11-13

Messages are one structure now. Each side used to keep three
separate lists — the message contents, the sender labels, and the
short display texts — and the three had to stay in step by hand.
Each message is now one object holding its role, content, label,
and display text together, so a label or display text can never
end up on the wrong message when a conversation is reordered,
summarized, saved, or loaded. Nothing you can see changed: same
window, same features, same files — conversations saved by older
versions still open exactly as before. DUO is now MIT-licensed
(see LICENSE).

## v0.25 — 2026-11-13

The code is split into modules. The program used to live in one
file of 5,165 lines; it now lives in focused modules — one for
Ollama, one for the conversation model, one for transcripts, one
for saved files, and so on — with the window and the entry point
still in dual_chat.py. Nothing you can see changed: same window,
same features, same files, same behavior. The split only makes
the code easier to work on next.

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
