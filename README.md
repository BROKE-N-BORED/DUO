# DUO

Two Ollama models side by side in one desktop window. Pick a model for
the left pane and one for the right, type a message, and send it to A,
to B, or to both at once. Each side keeps its own conversation.

Or start a relay: type a seed message, press Start Relay, and the two
models talk to each other — each reply is handed to the other model,
one turn at a time, until the turn cap or a stop condition.

License: MIT (see LICENSE). Version history lives in CHANGELOG.md.

Everything in one app:

- **Chat** — two panes, each with its own model picker and
  conversation; send to A, to B, or to both. One model generates at
  a time, so the PC never runs two models at once. Each pane also
  has a Temperature control (0.0–2.0, default 0.7, which is
  Ollama's own default): lower is more focused, higher is more
  random.
- **Relay** — the two models talk to each other, blind (each thinks
  it's talking to a person) or aware (they know it's another AI),
  with a turn cap, stop phrases, stop on agreement, and an option to
  review each hop before it forwards.
- **Director controls** — reorder or delete messages in a side's
  history, per-side instructions, saved model pairs, and compare
  mode (send to both, pick the better answer, it carries into the
  other side).
- **Long loops** — Summarize folds a long conversation into a short
  summary so it keeps fitting a small model's context, a nudge
  suggests it when a history gets long, and every turn shows its
  speed, its token counts, and the model's RAM.
- **Files** — attach text files to a message and the model reads
  them with it.
- **Tasks** — give one model a job and have the other review it or
  continue it, for as many rounds as you pick.
- **Transcripts** — save either side, or both, to a plain text file,
  or save the full transcript: the whole conversation as it happened,
  both sides interleaved in time order. Each pane also has a search
  row for finding text in its conversation, and an Import button
  that loads a saved single-side transcript back into the pane.
- **Conversation save** — Save conversation keeps the whole
  conversation in a file you can open again later: both sides'
  histories, both models, and both instruction fields. Open
  conversation loads it back exactly as it was.
- **Readable chat** — messages in the window are separated by a blank
  line, with the sender's name in bold, so a long reply never runs
  into the next message. Model replies render their Markdown —
  bold, code, headers, lists, links — while your own messages stay
  exactly as typed.
- **Stays connected** — if Ollama stalls briefly while the computer
  is busy, a turn retries a few times instead of giving up, and a
  slow Ollama is never reported as "not running". If Ollama really
  is down, a Start Ollama button appears in the bottom bar and
  starts it from inside DUO.
- **Debug report** — DUO keeps a log of what it does (events and
  timings only, never your messages). The Debug report button
  writes one file with the app version, Ollama's status, the
  session log, and recent errors — ready to email when something
  breaks.
- **Phone deck** — DUO serves a small page for your phone: press
  Phone deck, point your phone's camera at the QR code, and the
  conversation opens on your phone. The page follows whichever
  model is thinking, you can send messages from the phone, and one
  Export button downloads the full transcript. It only works on
  your home WiFi, and anyone on that WiFi can open it.
- **Remembers the window** — the window's size and position and
  the split between the panes are remembered between runs, so it
  opens where you left it. The title bar shows the version, and
  the window has its own icon.
- **Update check** — DUO checks GitHub once at startup and says in
  the status bar when a newer Release is out (silently saying
  nothing when offline), and a "Check for updates" button runs the
  same check whenever you like. It never downloads or installs
  anything — you download from GitHub yourself.

## What you need

- Linux with Python 3.10 or newer
- Ollama running on this computer, with at least one model pulled
  (for example: `ollama pull qwen3.5:2b`)
- Nothing else. No accounts, no keys, no cost.

## Install (one time)

```sh
bash install.sh
```

This creates a private `.venv` folder in the app folder with
PySide6-Essentials, pinned to the version DUO was built and verified
against (6.12.0). Nothing is installed system-wide, no sudo needed.
The installer checks the install before calling it done: if the
package is missing it says so and stops, and if the machine's Qt
libraries can't start the window it warns and names the missing
library. If you start DUO before installing, it tells you to run
the installer first — no traceback, no guessing.

The installer also adds DUO to the app menu, with an icon drawn from
the app's own window icon. If the icon can't be drawn on your
machine, the menu entry still appears, just without an icon. To
remove DUO later, run `bash uninstall.sh` — it asks first, then
removes the menu entry, the `.venv` folder, and the files DUO created
(saved pairs, debug log, menu icon).

If you cloned the repo instead of downloading a Release, the scripts
may not be executable — run them with `bash`, as above. The Release
zip is the install path and carries the executable bits.

## Run

```sh
bash run.sh
```

## How it works

- Each pane has its own model picker. Nothing is picked for you — choose
  a model in a pane before you can send to it. The Refresh button
  reloads the list from Ollama. Under the picker is a Temperature
  control (0.0–2.0, default 0.7, which is Ollama's own default):
  lower is more focused, higher is more random. Each side's
  temperature is remembered between runs.
- Send to A talks only to the left model. Send to B talks only to the
  right model. Send to Both sends the same message to each side, one
  after the other — A first, then B — so the PC never runs two models
  at the same time.
- Keyboard: Ctrl+Enter sends to both models. Tick "Enter to send"
  (under the message box) and plain Enter sends to both too, with
  Shift+Enter still adding a new line. The tick is remembered between
  runs. Long pastes stay whole — the message box never drops text.
- Menu bar: File, Relay, and Help sit across the top of the window.
  File holds the saves, Debug report, and Quit; Relay holds Start
  and Stop relay; Help holds Check for updates, How to use, and
  About. Shortcuts: Ctrl+S saves the conversation, Ctrl+O opens
  one, Ctrl+Shift+S saves the full transcript, Ctrl+Q quits. How
  to use is a built-in index explaining everything DUO does, in
  plain language and with no web.
- Stop ends the turn in progress on that side. Clear wipes that side's
  conversation, and asks first — it tells you how many messages you
  are about to lose, and No is the default. Conversations are not
  saved automatically — Save conversation (next to the save buttons) keeps the whole
  conversation in a file you can open again later, and the Save
  buttons keep a transcript as a text file.
- The window remembers its size, position, and the split between
  the panes, so it opens where you left it.
- The "Director" toggle between the panes and the controls below
  hides the whole director area so the panes take the whole window;
  click it again to bring everything back. The toggle stays visible
  either way, your choice is remembered between runs, and a relay
  or task keeps running while the controls are hidden.
- DUO checks for a newer Release once at startup and says so in
  the status bar when one is out; the "Check for updates" button
  next to Phone deck runs the same check on demand. It never
  downloads or installs anything.
- If Ollama isn't running, the pane says so in plain words.

## Relay

- Pick a model on both sides, type a seed message, choose which side
  starts, and press Start Relay. The seed goes to the starting side;
  its reply goes to the other side; that reply comes back — one model
  generating at a time, so the PC never runs two models at once.
- Turns caps the relay at that many replies (default 10, up to 50).
  Stop Relay ends it whenever you want.
- Blind (default): each model gets the bare reply text as a plain
  message, so it thinks it's talking to a person. Aware: the reply is
  framed so the models know they're talking to another AI.
- Stop on ends the relay when a reply uses one of your words or
  phrases (separated by commas) as a whole word -- "cap" won't stop
  on "capital". Stop on agreement ends it when a reply says something
  like "sounds good" or "I agree" -- "I disagreed" doesn't count.
- Relayed messages carry a [relay] tag in the window so you can always
  tell what was relayed. The models never see the tag.
- Presets: the Preset row saves the whole relay row — start side,
  mode, turn cap, stop phrases, agreement, review — under a name,
  and loads it back from the dropdown. Presets live in
  duo-relay-presets.json next to the app. Nothing is saved or
  loaded unless you ask for it. Delete preset removes the
  selected preset, after asking first.

## Director controls

- History: each pane has a History button that opens its message list.
  Select a message and use Up / Down to move it, or Delete to remove
  it. The model sees the edited order on its next turn. Editing is
  only available while nothing is generating.
- Review each hop: tick this before starting a relay and the relay
  pauses before every hop, showing you the exact text the other model
  would get. Forward sends it as-is, Forward edited sends your edited
  version, Skip this hop ends the relay without forwarding.
- Instructions: the Instructions button above each pane opens a
  system-prompt field for that side. Whatever you type there is sent
  as the system message for that model on every turn. Leave it empty
  for no system message.
- Pairs: the Pair row saves the current setup — both models plus both
  instruction fields — under a name, and loads it back from the
  dropdown. Pairs live in duo-pairs.json next to the app. Nothing is
  saved or loaded unless you ask for it. Delete pair removes the
  selected pair, after asking first.
- Compare: after Send to Both, each pane gets a Use this button. Tap
  it and that reply is sent to the other side ("You picked this
  answer: ...") so the conversation continues from the better answer.
  The picked reply is marked in its pane.

## Long loops

- Summarize: each pane has a Summarize button. It asks that side's
  own model to summarize the conversation so far, then replaces the
  older turns with that summary — kept as a visible "Summary of
  earlier messages" entry — while the last 4 messages stay verbatim.
  It is one extra model turn, so it takes a while on a CPU.
- Budget nudge: when a side's history passes about 12,000 characters,
  the pane shows "History is long — Summarize?" next to the button.
  The nudge never calls the model on its own — you decide.
- Stats: under each pane, the last completed turn's speed — tokens
  per second, time to first token, total turn time — plus how many
  tokens the turn used ("42 out / 128 in": generated and prompt,
  straight from Ollama) and the model's RAM from Ollama ("1.9 GB in
  RAM", or "not loaded" when it isn't running). Dashes until a turn
  has run; "n/a" when Ollama doesn't report a number. Underneath,
  each pane keeps a running session total ("Session: 42 out /
  128 in") across every generation since the app started; Clear
  never resets it.
- Activity lights: each pane has a small colored light and small
  words under its stats line saying what that side is doing right
  now — grey Idle, amber while it waits (queued, waiting for
  Ollama to answer, or a relay hop waiting for your review),
  green while it thinks or summarizes, red for a few seconds when
  a turn fails.
- System meter: the status bar shows the whole machine's CPU %
  and RAM ("CPU 42% · RAM 3.1 / 7.7 GB"), re-read once a second.
  On a machine without /proc the meter stays empty and silent.

## Files

- Attach: the Attach button next to the message box opens a file
  picker (pick more than one at once). Attached files show in a list
  above the message box; the x drops one.
- When you send, each attached text file's contents are sent with
  your message as a plain block ("File: name" plus the contents),
  so the model can read it. Your window shows the message with a
  short "(Attached: name)" tag, not the full contents.
- Text files only, up to 100 KB each, max 5 at once. A file that
  isn't text, or is too big, gets a plain note naming it and is
  skipped — nothing crashes.
- Attachments are one-shot: they clear after a send. In a relay they
  ride with the seed message only, never with the hops.
- You still need to type a message to send — attachments go with a
  message, they don't replace it.

## Tasks

- Task: describe a job in the task box, pick which model does it,
  and pick what the other model does with the result — Review it,
  Continue it, or Nothing — then press Run Task. Nothing is
  pre-selected; Run Task stays off until all three are set.
- Rounds says how many rounds the task runs (1–10, default 1).
  Each round passes the latest result to the other side: Continue
  it keeps continuing, and a review chain alternates review turns
  with revise turns, handing each review back to the worker to
  revise from. With Review it or Continue it the task always runs
  at least 2 rounds, so Rounds 1 is the same task as before: the
  worker, then one follow-up round. Nothing is always one round,
  so its Rounds box stays off. The status bar shows which round a
  chain is on.
- Review it sends the worker's full reply to the other model framed
  as "Here is the result of a task. Review it and say what is good,
  what is wrong, and what you would change: ...". Continue it frames
  it as "Continue this work from where it left off: ...". A revise
  turn frames the review as "Here is a review of your work. Revise
  your work based on it: ...". Nothing
  makes no second call — the task ends after the worker's reply.
- In your window the task shows as "You [task]: ..." in the
  worker's pane and the handoff as "You [task handoff]: ..." in the
  other pane. The tags never reach the models — the worker gets the
  bare task text.
- The per-side Stop button cancels a running task, ending the chain
  after the current round. A round that fails ends the chain with a
  plain note; the earlier results stay in the panes. Attachments never
  ride on tasks — tasks are text-only; attached files stay put for
  the next normal send.

## Transcripts

- Save: each pane has a Save button that saves that side's
  conversation to a plain text file you pick. Save both, next to the
  send buttons, saves both sides in one file — the Model A section
  first, then Model B. Save full transcript, next to Save both,
  saves the whole conversation as it happened: both sides interleaved
  in time order, each message prefixed with its side ("A -- You: ...",
  "B -- <model>: ..."). Reordering or deleting messages never changes
  the full transcript — it is what actually happened. Clearing a side
  removes that side from it, since the conversation restarts. The
  full transcript keeps the most recent 2,000 messages; if a very
  long session passes that, the oldest drop off and the saved file
  says so at the top, so a file never silently loses old messages.
- The file reads like the window: a header line naming DUO, the model,
  and when it was saved, then each message in order with its labels
  ([relay], [task], [task handoff], the summary entry), one blank
  line between messages. Attached file contents are never pasted in —
  the file carries the same short "(Attached: ...)" tag the window
  shows.
- An empty transcript still saves: the header plus "No messages yet.".
  Saving is only available while nothing is generating — the
  transcript is moving mid-turn, so a save then would catch it
  half-written.
- Search: under each pane's transcript is a search box. Type to find
  text in that pane's conversation — case-insensitive plain substring
  matching, no regex. Every match is highlighted, the current one in
  a different colour, and Previous / Next step between matches,
  wrapping at the ends. Searching is display-only: it never changes
  the conversation, and any new message, re-render, or clear drops
  the search.
- Markdown: model replies show formatted in the panes — bold, italic,
  code, code blocks, headers, lists, quotes, and links (clicking a
  link opens it in the browser). While a reply streams it stays plain
  text, and the moment the turn finishes it formats in place. Your
  own messages stay exactly as typed — only model replies render.
  Saved transcripts still carry the raw text, exactly as the model
  sent it.
- Import: each pane has an Import button next to Save that loads a
  saved single-pane transcript back into the pane. Importing replaces
  the pane's conversation, so it asks first when the pane already has
  messages. The imported messages join the pane's history, so the
  next send includes them in the model's context. The picked model
  is untouched — the file's model names are display labels only. The
  imported entries go at the end of the full transcript, since the
  file has no timestamps to interleave them by; the other side's
  entries are untouched. "Save both" files and full transcripts are
  refused plainly — import takes a single-side transcript only. Note
  that a transcript file carries what the window showed, so an
  imported message is the window's short text (the "(Attached: ...)"
  tag, not the file contents; the handoff note, not the full framed
  text).
- Save conversation, next to Save both, saves the whole conversation
  to a file you can open again later: both sides' histories with
  their labels, both models, both instruction fields, and the
  interleaved full transcript — including the full text of any
  attached files, so the conversation comes back exactly as it was.
  Open conversation loads it back. Opening replaces the conversation
  on screen, so it asks first when either pane already has messages.
  If a saved model isn't installed anymore, that side stays
  unpicked and the window says so in plain words. A file that isn't
  a DUO conversation gets a plain error and changes nothing.

## Phone deck

Press Phone deck (next to the save buttons) and a QR code appears.
Point your phone's camera at it and the deck opens in your phone's
browser — or type the address shown under the code. The page is big
and simple: the model that is thinking fills the screen, the other
one sits small underneath, and it swaps on its own when the turn
changes. Type a message, pick A, B, or Both, and Send — it goes to
the models exactly like a message typed on the PC. Export transcript
downloads the full transcript, the same file as Save full
transcript on the desktop.

Two things to know. The page only works while DUO is open on the
computer, and only on your home WiFi — your phone and the computer
have to be on the same network. And anyone on that WiFi can open
the page and send messages, so it is as private as your WiFi is.
There is no login and no account. Sending from the phone is blocked
while the window is busy or a relay is running, and the page says
so in plain words.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

GUI tests need a display or offscreen Qt plus the flag:

```sh
DUAL_CHAT_GUI_TESTS=1 QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v
```

## When Ollama stops answering

On a slow computer Ollama can stall for a moment while it is busy
generating. DUO handles that: a turn retries a few times with a
pause between tries before it fails, and a relay keeps going when a
hop succeeds on a retry. If a reply was already streaming when the
connection dropped, the partial reply stays in the pane with a plain
note — DUO never retries a half-streamed turn, because that would
repeat the part you already saw.

If Ollama has genuinely stopped, the panes say so in plain words and
a Start Ollama button appears in the bottom bar. Press it and DUO
starts Ollama itself — no terminal, no extra steps. When DUO closes,
it stops only the server it started; a server you started yourself
is left alone.

If something keeps going wrong, press Debug report (next to the save
buttons) and send the file it writes. It holds the app version,
Ollama's status, what DUO was doing, and the errors — never your
messages or your attached files. DUO also keeps a rotating log next
to the app (`duo-debug.log`) so the last few runs are on hand.

## Testing knob

Point the app at a different Ollama address (for testing only):

```sh
DUAL_CHAT_OLLAMA_URL=http://127.0.0.1:11435 bash run.sh
```

Or: `bash run.sh --ollama-url http://127.0.0.1:11435`
