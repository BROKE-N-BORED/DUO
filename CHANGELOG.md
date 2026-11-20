# DUO changelog

Every version, newest first. v0.1–v0.17 were built on 2026-11-12;
v0.18–v0.35 on 2026-11-13; v0.36–v0.49 on 2026-11-14; v0.50 on
2026-11-15; v0.51–v0.52 on 2026-11-16; v0.53–v0.56 on
2026-11-17; v0.58 on 2026-11-19; v0.59 on 2026-11-19; v0.60 on
2026-11-19.

## v0.60 — 2026-11-19

A hardening pass under the hood; nothing about the window
changes. A turn that comes back empty is now reported as a
failed turn with plain words, instead of landing in the
conversation as a blank reply (and being forwarded by the
relay as one). Opening a conversation or transcript file that
is not readable text now gets the same plain "not a DUO file"
answer as a corrupt one, instead of failing silently. A saved
pairs file that has gone bad is now backed up before anything
overwrites it, and the pairs row says so when it can't be read,
the same protection saved relay presets already had. If part
of the window's plumbing ever throws an error mid-turn, the
generation queue now logs it and keeps going instead of
stalling every later turn. Crashes in DUO's background threads
are now written into the debug report too, not just crashes in
the window itself. Small print: the How to Use page now says
Ctrl+Enter sends from the message box (it never worked from
anywhere else), and the README's test commands point at the
interpreter that actually has what the tests need.

## v0.59 — 2026-11-19

DUO runs on a new engine: Fyrn, the local model engine
installed on this PC. The model now loads into DUO itself and
generates there, so there is no Ollama server to start, watch,
or lose, and turns start faster. DUO finds the Fyrn install on
its own when it starts. The model list still shows every model
Fyrn sees: the models in Fyrn's models folder, plus any models
already sitting in Ollama's store on this PC, which Fyrn reads
itself. Embedding models no longer appear in the pickers,
because they cannot write replies. If Fyrn is not installed,
the window opens anyway and the status bar says so in plain
words, and the Start Ollama button from v0.58 is gone, because
there is no server left to start. The Temperature control
under each picker is gone too: Fyrn does not offer a
temperature setting, and DUO does not keep controls that do
nothing. Saved pairs still load; a pair saved by an older
version keeps its models and instructions, and its old
temperature values are simply ignored. The stats line keeps
its speed and timing numbers; the prompt token count now
shows n/a, because Fyrn does not report one, and DUO would
rather show n/a than invent a number.

## v0.58 — 2026-11-19

The fixes from a hands-on drive of v0.57, using the app the way
a person would. The Tab key no longer gets trapped in the
message box or the other text editors; it moves focus onward
like it should, so the whole window can be driven from the
keyboard. A failed turn no longer blames the model unless the
model really failed to load: a failure partway through a reply
and an unexpected hiccup now get honest words of their own.
When Ollama is not running, the status bar offers a Start
Ollama button that starts it and checks the model list again,
no terminal needed. The small status word beside each pane's
buttons uses short forms, so "Something went wrong" no longer
clips to "Something w". And the stats line no longer ends a
good turn with "not loaded"; when the memory reading cannot be
trusted it says "RAM n/a" instead.

## v0.57 — 2026-11-17

Stop on agreement finally says what counts. The relay section
now explains that a reply saying "I agree" or "agreed" ends the
relay, and that "Sounds good" and "That works" only count when
the whole reply is just that, so polite filler in the middle of
a longer reply no longer ends a relay early. Screen readers get
real names on the controls that used to read as bare words: the
per-pane Stop, Save, and Refresh buttons now say which model
they belong to, and the history editor's small buttons say what
they do. And closing DUO while the model list is still loading
no longer freezes the window; it waits a few seconds and then
closes anyway.

## v0.56 — 2026-11-17

The window gets room to breathe, and room to grow. The send row
used to hold ten buttons and four labels clipped at the narrowest
window size; the save and open buttons now sit on a second row,
and every label fits. The director's sections used to run off
the bottom of a short window with no way to reach them; they now
scroll under the composer and the send rows, which stay pinned.
The panes keep a guaranteed minimum height, so the transcript
holds its floor even with the director fully open. Start Relay
greys out on an empty composer, the way the send buttons already
do. Deleting a message from history asks first, and closing DUO
with an unsaved conversation asks first too, with No as the
safe default both times. Underneath, the saved-pair and relay
preset handlers move to their own module, duo_pairs.py, which
buys the window file two hundred lines of headroom.

## v0.55 — 2026-11-17

DUO updates itself now, when you say so. It still checks
silently at startup and says nothing when you are up to date,
but when a newer Release is out a popup asks whether to update.
Only if you tap Update does it pull the new version, and it
checks the download against the release's checksum before
installing anything, so a broken or tampered download never
touches your copy. Once the download is checked, a second popup
asks whether to restart now to finish: Yes restarts DUO on its
own with the update applied, Later waits and the update finishes
the next time DUO starts. Your saved pairs, transcripts and
settings are untouched by an update.

## v0.54 — 2026-11-17

Forward in the review box sends what is actually in the box.
It used to send the original reply and silently throw away
anything you had typed, with a second button, Forward edited,
as the only way to send your changes. Editing and then clicking
the leftmost, most obvious button lost your edit with no
warning. Now there is one Forward button and it sends exactly
what you see in the box, edited or not. Clearing the box and
clicking Forward sends nothing and tells you so, and the relay
stays paused until you put text back. The guide is fixed too:
it still said Skip this hop ends the relay, which stopped being
true in v0.53.

## v0.53 — 2026-11-17

"Skip this hop" no longer ends the whole relay. It used to stop
everything, which made it a second Stop button wearing a
different label. Now skipping a hop drops that one reply and the
relay carries on: the other model takes its turn without ever
seeing the skipped reply, and a short note in its pane says the
hop was skipped. If you skip the very first hop, the other model
answers your first message instead, since it has nothing else to
answer yet. The skipped reply stays in the pane of the model
that wrote it, and it still counts toward the turn cap, since
the turn did run.

## v0.52 — 2026-11-16

Stop can interrupt a stalled generation. If Ollama went quiet
in the middle of a turn — no tokens coming, nothing arriving —
Stop used to do nothing: the app sat there for up to 30 minutes
per try, three tries in a row, looking dead. Now Stop lands
within a few seconds of a stall, because the app checks for it
every few seconds while it waits for the next piece of a reply.
A slow model is unaffected: as long as words keep arriving, the
turn keeps going, and a genuine stall still fails the turn after
the same 30 minutes of silence as before.

## v0.51 — 2026-11-16

DUO is readable on a dark theme. Three things were painted a
fixed near-white no matter the theme, so on a dark desktop the
white text vanished into them: the message box went solid white
the moment you clicked in it (and what you typed was invisible),
code blocks were white text on a pale grey block, and the How to
Use window opened as a white rectangle. The same white tint also
blanked the default button in dialogs. All three now follow the
theme: the code-block background, the focused field's tint, and
the search highlight colors are worked out from the desktop's own
colors when DUO starts, so a dark theme gets dark surfaces and a
light theme keeps exactly the look it always had. The blue focus
ring itself is unchanged. A theme change while DUO is running
needs a restart to take effect.

## v0.50 — 2026-11-15

Installing DUO no longer needs the terminal. The Release zip now
carries an Install DUO launcher: unzip, double-click it, and the
installer runs with visible progress, then puts a DUO icon on the
desktop and adds DUO to the app menu. Double-click the desktop icon
to run DUO. The installer also fixes missing pieces itself instead
of handing you a command: if python3-venv is missing it tries
`sudo apt install python3-venv` and retries, and if the Qt system
libraries are missing it tries `sudo apt install libegl1 libgl1`
and checks again. If either install cannot happen, it says the
command plainly and carries on — the install never fails over it.
The desktop icon is marked trusted where the machine allows it; if
the marking does not take, the installer says to right-click the
icon and choose Allow Launching. Uninstall removes the desktop icon
too. The terminal commands still work as the fallback: `bash
install.sh`, then `bash run.sh`.

## v0.49 — 2026-11-14

The Ollama connection fixed, and the Start Ollama button gone.
DUO used to drop Ollama where Code Forge never did: a turn that
hit Ollama's transient "timed out waiting for llama-server to
start" died on the spot instead of retrying, models unloaded
after five idle minutes because DUO sent no keep-alive, the
model list got one short attempt before giving up, and a slow
probe under load could call a busy Ollama "not running". All
four are fixed in the client now: the start timeout is retried
like a dropped connection, every chat request carries keep-alive
"30m" so models stay loaded for the session, the model list gets
three attempts at eight seconds, and the down-or-slow error comes
from what the attempts actually failed with — a refused
connection means down, anything else means slow or busy. The
Start Ollama button is removed entirely — DUO no longer starts
Ollama, it stays connected to the one that is running. The
plain-language Ollama errors stay: they only fire when Ollama is
genuinely unreachable now, and the remedy is starting Ollama
yourself (`ollama serve` in a terminal).

## v0.48 — 2026-11-14

A Big text preset, in the Help menu next to "Reduce motion":
one tap makes all of DUO's text bigger right away, no restart,
and DUO remembers the choice. The app's text grows by 2 points
everywhere — the chat, the composer, the buttons, the menus —
and the small labels grow with it, so the stats, the activity
words, and the explanations underneath the fields get bigger
too, and the pane titles stay headers. On a first run DUO asks
once, "DUO can make its text bigger. Turn on Big text?", with
Yes and No and No as the default — it never asks again after
that, either way. The How to Use index says where the preset
lives. Housekeeping: the preset lives in duo_accessibility.py,
so dual_chat.py stays under its line bound.

## v0.47 — 2026-11-14

Each pane has a live tok/s sparkline now — a small graph on
the activity row, in the side's color, between the activity
label and the session total, so it costs no height at all. It
samples the live rate every half second while a side
generates, and keeps the last 60 samples — the last 30
seconds of generation — across turns, so you can see how the
rate moved during a turn and across them, not just one number
at the end. When the side is idle the graph freezes on its
recent history. The last sample of a turn is the turn's
official tok/s, the same number the stats line shows, from the
done chunk. The mid-turn samples are estimated from the
characters streamed so far (four characters per token), because
Ollama's stream carries no token count until the done chunk —
they shape the graph only; the stats line's counts stay exact.
The sparkline resets whenever the pane's stats reset: Clear,
loading a conversation, import, or picking a different model.
It keeps updating with "Reduce motion" on — it is data, not
decoration. Housekeeping: the module-level constants moved
into duo_constants.py, and the sparkline lives in
duo_sparkline.py, so dual_chat.py stays under its line bound.

## v0.46 — 2026-11-14

The Director toggle springs now instead of snapping. Opening
it runs 350 ms with an overshoot — the director grows a touch
past its full height, then settles back, the way a spring
does — and closing it eases out over 250 ms. The split is
deliberate: an overshoot on the way down would mean going past
zero, which is meaningless, so only the opening springs.
Clicking mid-flight reverses from wherever the director
actually is; the chevron flips the moment you click, before
the motion finishes. With "Reduce motion" on, the toggle is
instant again, like the button dip. The Relay, Pairs, and Tasks
section toggles stay instant on purpose: this is the Director
toggle only, the one that moves the whole window layout. A
probe settled the mechanism before it was built: the
director's height tracks its maximumHeight exactly, its
sizeHint stays valid while hidden, and the overshoot lands at
330 against a full height of 301.

## v0.45 — 2026-11-14

The window answers back now. Pressing a button dips it
slightly, so a press is felt, not just seen. When a turn
finishes, the side's light flashes lime for a moment — a small
finish mark on top of the light going back to grey. And
keyboard focus is visible at last: whichever field or button
has it wears a clear blue ring, so tabbing through the window
never leaves you guessing where you are. All three are cheap
on purpose — the motion is a short geometry shift, nothing
heavier — and a new \"Reduce motion\" item in the Help menu
turns the dip off while keeping the flash and the ring, which
are state changes, not motion. Two probes settled the design
before it was built: the dip's geometry animation does not
fight the layout, and the focus stylesheet leaves unfocused
widgets looking exactly as they did.

## v0.44 — 2026-11-14

One DUO at a time now. Launching DUO twice no longer opens a
second window: the DUO already running comes to the front, and
the second launch exits quietly. That also means no second
phone-deck server fighting over the port, and no two copies
writing to the same saved pairs and presets. If DUO ever
crashes, the next launch still starts cleanly instead of
tripping over the socket the crashed copy left behind.

## v0.43 — 2026-11-14

The send buttons stay greyed out until there is something to
send. Send to A, Send to B, and Send to Both are disabled while
the message box is empty — spaces alone do not count — and they
light up the moment you type. Attaching files alone does not
enable them: a typed message is still required. The keyboard
send (Ctrl+Enter, or plain Enter with "Enter to send" ticked)
follows the same rule, since it only fires while Send to Both
is enabled. The phone deck is unchanged: it already turns away
an empty send with a plain message.

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
