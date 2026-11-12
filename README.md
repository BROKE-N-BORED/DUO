# DUO

Two Ollama models side by side in one desktop window. Pick a model for
the left pane and one for the right, type a message, and send it to A,
to B, or to both at once. Each side keeps its own conversation.

Or start a relay: type a seed message, press Start Relay, and the two
models talk to each other — each reply is handed to the other model,
one turn at a time, until the turn cap or a stop condition.

This is Item 2 of the project: the shell, the model pickers,
send-to-A/B/both, and the auto-relay loop. Files, tasks, and the rest
come later.

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
