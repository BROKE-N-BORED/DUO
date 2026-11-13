# DUO

Two Ollama models side by side in one desktop window. Pick a model for
the left pane and one for the right, type a message, and send it to A,
to B, or to both at once. Each side keeps its own conversation.

Or start a relay: type a seed message, press Start Relay, and the two
models talk to each other — each reply is handed to the other model,
one turn at a time, until the turn cap or a stop condition.

License: not decided yet — the project owner will pick one. Version
history lives in CHANGELOG.md.

Everything in one app:

- **Chat** — two panes, each with its own model picker and
  conversation; send to A, to B, or to both. One model generates at
  a time, so the PC never runs two models at once.
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
  speed and the model's RAM.
- **Files** — attach text files to a message and the model reads
  them with it.
- **Tasks** — give one model a job and have the other review it or
  continue it.
- **Transcripts** — save either side, or both, to a plain text file,
  or save the full transcript: the whole conversation as it happened,
  both sides interleaved in time order.
- **Readable chat** — messages in the window are separated by a blank
  line, with the sender's name in bold, so a long reply never runs
  into the next message.
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
  reloads the list from Ollama.
- Send to A talks only to the left model. Send to B talks only to the
  right model. Send to Both sends the same message to each side, one
  after the other — A first, then B — so the PC never runs two models
  at the same time.
- Keyboard: Ctrl+Enter sends to both models. Tick "Enter to send"
  (under the message box) and plain Enter sends to both too, with
  Shift+Enter still adding a new line. The tick is remembered between
  runs. Long pastes stay whole — the message box never drops text.
- Stop ends the turn in progress on that side. Clear wipes that side's
  conversation. Conversations are not saved between runs — use the
  Save buttons to keep a transcript as a text file.
- The window remembers its size, position, and the split between
  the panes, so it opens where you left it.
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
  per second, time to first token, total turn time — plus the model's
  RAM from Ollama ("1.9 GB in RAM", or "not loaded" when it isn't
  running). Dashes until a turn has run; "n/a" when Ollama doesn't
  report a number.

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
- One round only: the worker answers, then the other side reviews
  or continues the result. The relay covers loops.
- Review it sends the worker's full reply to the other model framed
  as "Here is the result of a task. Review it and say what is good,
  what is wrong, and what you would change: ...". Continue it frames
  it as "Continue this work from where it left off: ...". Nothing
  makes no second call — the task ends after the worker's reply.
- In your window the task shows as "You [task]: ..." in the
  worker's pane and the handoff as "You [task handoff]: ..." in the
  other pane. The tags never reach the models — the worker gets the
  bare task text.
- The per-side Stop button cancels a running task. Attachments never
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
  removes that side from it, since the conversation restarts.
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
