# DUO

Two Ollama models side by side in one desktop window. Pick a model for
the left pane and one for the right, type a message, and send it to A,
to B, or to both at once. Each side keeps its own conversation.

Or start a relay: type a seed message, press Start Relay, and the two
models talk to each other — each reply is handed to the other model,
one turn at a time, until the turn cap or a stop condition.

This is Item 6 of the project: the shell, the model pickers,
send-to-A/B/both, the auto-relay loop, the director controls
(history editing, hop review, instructions, saved pairs, compare
mode), long-loop support (summarize, budget nudge, per-turn
speed and RAM readout), file upload, and tasks. The rest
comes later.

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
PySide6-Essentials. Nothing is installed system-wide, no sudo needed.

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
- Stop ends the turn in progress on that side. Clear wipes that side's
  conversation. Conversations are not saved between runs yet.
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
- Stop on ends the relay when a reply contains one of your words or
  phrases (separated by commas). Stop on agreement ends it when a
  reply says something like "sounds good" or "I agree".
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
  saved or loaded unless you ask for it.
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

## Tests

```sh
python3 -m unittest discover -s tests -v
```

GUI tests need a display or offscreen Qt plus the flag:

```sh
DUAL_CHAT_GUI_TESTS=1 QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v
```

## Testing knob

Point the app at a different Ollama address (for testing only):

```sh
DUAL_CHAT_OLLAMA_URL=http://127.0.0.1:11435 bash run.sh
```

Or: `bash run.sh --ollama-url http://127.0.0.1:11435`
