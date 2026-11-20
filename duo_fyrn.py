"""The FYRN backend: DUO's engine, in-process (v0.59, item 139).

DUO used to talk to Ollama over localhost HTTP (duo_ollama, gone
in this version). It now runs on FYRN, Joshua's own engine: the
model loads into this process and generates there, so there is
no server to start, watch, or lose. FYRN is another app on the
same PC, installed in its own folder with its own venv; DUO
finds that install (the Fyrn Chat Test finder pattern), imports
the installed fyrn package lazily, and drives it through
fyrn.core.ModelController and fyrn.runner.LlamaRunner.

This module is stdlib-only at the top level: the fyrn package is
imported lazily inside the client, so DUO still opens -- and says
in plain words that Fyrn is not installed -- on a PC without
Fyrn. Finding Fyrn never writes anything: DUO reads Fyrn's
install and its settings, and never modifies either.

The client keeps the exact contract the window and the runner
were built against (the old OllamaClient's): list_models,
list_models_with_sizes, chat_stream with on_metrics/on_retry
callbacks, running_models. The timeout and retry knobs are
accepted and unused -- an in-process engine has no server to
time out and nothing to retry.
"""

import configparser
import importlib
import os
import re
import sys
import time
from pathlib import Path


# --- Plain-language messages ----------------------------------------------
# Shown in the panes and the status bar. No tracebacks, no jargon,
# no em-dashes.

MSG_FYRN_MISSING = (
    "Fyrn is not installed on this PC. DUO runs on Fyrn, so "
    "install Fyrn and try again.")

MSG_FYRN_AMBIGUOUS = (
    "More than one Fyrn folder was found, so DUO does not know "
    "which one to use. Run the Fyrn you want from its own folder "
    "once, then try again.")

MSG_MODEL_FAILED = "That model couldn't be loaded. Pick another model and try again."

MSG_MODEL_GONE = (
    "That model's file is gone. It may have been moved or deleted.")

# The too-big message names the model: the user picked it, and
# "that model" alone would not say which of the two panes failed.
MSG_MODEL_TOO_BIG = (
    "%s is too big for this PC's free memory. Pick a smaller "
    "model and try again.")

# v0.58 (P2-52): an unexpected failure is not a model load
# failure. A turn that fails after the model loaded and started
# answering gets these honest, general words instead.
MSG_TURN_FAILED = "Something went wrong with that turn. Try again."

# v0.60 (P1-13): a generation that ends with no reply at all is
# a failed turn, never a silent success -- an empty assistant
# message in the history (or forwarded by the relay) helps no
# one and reads as if the model answered.
MSG_EMPTY_REPLY = (
    "That model sent back an empty reply. Try again, or pick "
    "another model.")


# The old client's chat timeout, kept as the signature default so
# the call shape the window was built against is unchanged. The
# in-process engine has no socket to time out; a turn runs until
# it finishes or Stop lands.
CHAT_TIMEOUT = 1800


class EngineError(Exception):
    """A plain-language engine failure. ``message`` is safe to show."""


class Cancelled(Exception):
    """Raised inside a generation when the user stops it."""


# --- Finding the Fyrn install ----------------------------------------------
# Adapted from Fyrn Chat Test's finder (fyrnchattest/finder.py),
# with DUO's wording. A Fyrn install is a folder with install.sh,
# the fyrn package, and a venv. The search order:
#   1. FYRN_INSTALL_DIR, when set (DUO's launcher passes it, and
#      it is the escape hatch when more than one Fyrn folder
#      exists).
#   2. The ~/.local/bin/fyrn launcher, which names its folder in
#      an APP_DIR line.
#   3. A search of the home folder (hidden folders pruned, 6
#      levels deep, the same bounds as Fyrn's own launcher).

FYRN_INSTALL_DIR_ENV = "FYRN_INSTALL_DIR"

# How deep the home-folder search goes (Fyrn's launcher uses the
# same bound: deep enough for a folder inside Documents, shallow
# enough to stay quick).
SEARCH_MAX_DEPTH = 6

# The launcher's APP_DIR line: APP_DIR="/home/joshua/fyrn"
_APP_DIR_RE = re.compile(r'^APP_DIR="([^"]*)"\s*$', re.MULTILINE)


class FyrnNotFoundError(Exception):
    """No Fyrn install could be found on this PC."""


class FyrnAmbiguousError(Exception):
    """More than one Fyrn install was found, and DUO will not
    guess which one to use."""

    def __init__(self, message, candidates):
        super().__init__(message)
        self.candidates = list(candidates)


class FyrnInstall:
    """A Fyrn install: its folder, its venv python, its version."""

    def __init__(self, folder, python, version):
        self.folder = Path(folder)
        self.python = Path(python)
        self.version = version

    def __repr__(self):
        return "FyrnInstall(%s, version=%s)" % (self.folder,
                                                self.version)


def _read_version(folder):
    """Fyrn's version, read from its __init__.py without importing
    it (importing needs the install on the path, which the finder
    does not assume). "unknown" when the file cannot be read."""
    try:
        text = (Path(folder) / "fyrn" / "__init__.py").read_text(
            encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "unknown"


def _is_fyrn_install(folder):
    """True when a folder is a usable Fyrn install: install.sh,
    the fyrn package, and a venv python that runs."""
    folder = Path(folder)
    return (
        (folder / "install.sh").is_file()
        and (folder / "fyrn" / "__init__.py").is_file()
        and os.access(folder / ".venv" / "bin" / "python", os.X_OK)
    )


def _install_from(folder):
    """A FyrnInstall for a folder, or None when it is not one."""
    folder = Path(folder)
    if not _is_fyrn_install(folder):
        return None
    return FyrnInstall(folder, folder / ".venv" / "bin" / "python",
                       _read_version(folder))


def _launcher_app_dir(home):
    """The folder named in the ~/.local/bin/fyrn launcher, or
    None when there is no launcher or no APP_DIR line in it."""
    launcher = Path(home) / ".local" / "bin" / "fyrn"
    try:
        text = launcher.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _APP_DIR_RE.search(text)
    return match.group(1) if match else None


def _search_home(home):
    """Every Fyrn install under the home folder, in a stable
    order: hidden folders pruned, 6 levels deep. A folder that
    cannot be listed is skipped, never fatal."""
    home = Path(home)
    found = []
    base_depth = len(home.parts)
    for root, dirs, _files in os.walk(home):
        depth = len(Path(root).parts) - base_depth
        # Prune hidden folders in place, and stop descending
        # past the depth bound.
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if depth >= SEARCH_MAX_DEPTH:
            dirs[:] = []
        if _is_fyrn_install(root):
            found.append(Path(root))
            # A Fyrn install never contains another one: do not
            # descend into it.
            dirs[:] = []
    return sorted(found)


def find_fyrn_install(home=None, environ=None):
    """Find the Fyrn install on this PC.

    home defaults to the user's home folder; environ defaults to
    os.environ (both injectable so tests can use a fake home).
    Raises FyrnNotFoundError when nothing is found, and
    FyrnAmbiguousError when the search finds more than one.
    """
    home = Path(home) if home is not None else Path.home()
    env = environ if environ is not None else os.environ
    # 1. The explicit override: the launcher passes it, and it is
    # the way out when more than one Fyrn folder exists.
    override = env.get(FYRN_INSTALL_DIR_ENV)
    if override:
        install = _install_from(override)
        if install is not None:
            return install
        raise FyrnNotFoundError(
            "FYRN_INSTALL_DIR is set to %s, but that folder is "
            "not a Fyrn install (it needs install.sh, the fyrn "
            "package, and a venv)." % override)
    # 2. The Fyrn launcher names its folder.
    app_dir = _launcher_app_dir(home)
    if app_dir:
        install = _install_from(app_dir)
        if install is not None:
            return install
        # A stale launcher (Fyrn moved, never re-launched) falls
        # through to the search instead of failing outright.
    # 3. Search the home folder.
    candidates = _search_home(home)
    if len(candidates) == 1:
        return _install_from(candidates[0])
    if len(candidates) > 1:
        raise FyrnAmbiguousError(
            "More than one Fyrn folder was found, so DUO does "
            "not know which Fyrn to use:\n%s\n\nRun the Fyrn "
            "you want from its own folder once (its launcher "
            "then points at it), or set FYRN_INSTALL_DIR to the "
            "folder to use."
            % "\n".join("  %s" % c for c in candidates),
            candidates)
    raise FyrnNotFoundError(
        "Fyrn is not installed on this PC. DUO runs on Fyrn, so "
        "there is nothing to run the models with. Install Fyrn "
        "first, then start DUO again.")


def check_fyrn():
    """Fyrn's status for the debug report: where the install is
    and its version, or that none was found. Never raises -- a
    failing check is itself a finding, reported as words."""
    try:
        install = find_fyrn_install()
    except FyrnAmbiguousError:
        return "more than one install found"
    except FyrnNotFoundError:
        return "not installed"
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return "check failed: %s" % exc
    return "installed at %s (version %s)" % (install.folder,
                                             install.version)


# --- Fyrn's settings (read-only) --------------------------------------------
# Fyrn keeps its models folder in its QSettings (org BrokeNBored,
# app fyrn, key models_folder). DUO reads the settings file
# directly with configparser: this module never imports Qt, and
# reading never writes. FYRN_SETTINGS_FILE is Fyrn's own override
# for the settings file location; it is honored the same way.

FYRN_SETTINGS_ORG = "BrokeNBored"
FYRN_SETTINGS_APP = "fyrn"
FYRN_SETTINGS_FILE_ENV = "FYRN_SETTINGS_FILE"


def _read_setting(path, key):
    """One value from a QSettings INI file, or None. QSettings
    writes ungrouped keys under [General]; any section holding
    the key is accepted, so a layout change in Fyrn's file does
    not silently lose the setting."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return None
    for section in parser.sections():
        if parser.has_option(section, key):
            value = parser.get(section, key).strip()
            # QSettings quotes a value that needs it; a models
            # folder path never does, but strip a matched pair of
            # quotes rather than hand the model scan a quoted path.
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1]
            if value:
                return value
    return None


def fyrn_models_folder(home=None, environ=None):
    """Fyrn's models folder: its settings value when one is set,
    else Fyrn's own default (~/Models, fyrn.core's
    default_models_folder). Read-only: the folder is not created
    here; Fyrn's scan of a missing folder simply finds nothing.
    """
    home = Path(home) if home is not None else Path.home()
    env = environ if environ is not None else os.environ
    override = env.get(FYRN_SETTINGS_FILE_ENV)
    if override:
        settings_path = Path(override)
    else:
        settings_path = (home / ".config" / FYRN_SETTINGS_ORG
                         / (FYRN_SETTINGS_APP + ".conf"))
    value = _read_setting(settings_path, "models_folder")
    if value:
        return Path(value).expanduser()
    return home / "Models"


# --- The fyrn package, imported lazily ---------------------------------------

class _FyrnModules:
    """The installed fyrn package's three submodules DUO uses."""

    def __init__(self, core, runner, discover):
        self.core = core
        self.runner = runner
        self.discover = discover


def _import_fyrn():
    """Import the installed fyrn package and return its modules.

    The install folder goes on sys.path first (the Fyrn Chat Test
    pattern): the app imports the Fyrn that is actually installed,
    never a stray copy. Raises EngineError with the plain missing
    or ambiguous message when there is no usable Fyrn: a missing
    engine is a state the window shows, never a traceback.
    """
    try:
        install = find_fyrn_install()
    except FyrnAmbiguousError:
        raise EngineError(MSG_FYRN_AMBIGUOUS)
    except FyrnNotFoundError:
        install = None
    if install is not None:
        folder = str(install.folder)
        if folder not in sys.path:
            sys.path.insert(0, folder)
    try:
        import fyrn  # noqa: F401  (binds the package for its submodules)
        core = importlib.import_module("fyrn.core")
        runner = importlib.import_module("fyrn.runner")
        discover = importlib.import_module("fyrn.discover")
    except ImportError:
        raise EngineError(MSG_FYRN_MISSING)
    return _FyrnModules(core, runner, discover)


# GGUF architectures that are embedding-only models (encoder
# only, no decoder): they load fine but cannot write replies.
# The list is Fyrn Chat Test's (fyrnchattest/models.py): Fyrn's
# core lists vision architectures but no embedding ones.
EMBEDDING_ARCHITECTURES = frozenset(("bert", "nomic-bert"))


def is_embedding_model(model):
    """True when a model looks like an embedding-only model: its
    header architecture is a BERT-family embedding arch, or its
    name says "embed". An embedding model turns text into numbers
    for search; it cannot write replies, so it never belongs in a
    chat picker. A model the header says nothing about, with no
    "embed" in its name, is treated as a chat model: the check
    never blocks a model it cannot identify."""
    facts = model.get("facts") or {}
    arch = facts.get("architecture")
    if isinstance(arch, str) \
            and arch.lower() in EMBEDDING_ARCHITECTURES:
        return True
    return "embed" in (model.get("name") or "").lower()


def _split_messages(messages):
    """Split DUO's message list for Fyrn's generate().

    Returns (instructions, history, prompt): the system messages
    join into the instructions text (None when there are none),
    the last user message is the prompt, and every other
    non-system turn before it is the history, in order. When the
    last turn is not a user message there is no prompt ("") --
    DUO's sends always end with one, so that path is a guard,
    not a flow."""
    instructions = []
    turns = []
    for message in messages or []:
        role = message.get("role")
        content = message.get("content") or ""
        if role == "system":
            if content.strip():
                instructions.append(content)
        else:
            turns.append({"role": role, "content": content})
    prompt = ""
    if turns and turns[-1]["role"] == "user":
        prompt = turns.pop()["content"]
    return ("\n\n".join(instructions) if instructions else None,
            turns, prompt)


class FyrnClient:
    """DUO's engine client: the installed Fyrn, in-process.

    One ModelController (and its one LlamaRunner) per client: the
    controller holds one loaded model, and a turn for a different
    model loads it first -- DUO already generates one turn at a
    time, so the switch cost lands exactly where the old server
    client's reload cost did. The model list is cached from the
    last list call for name-to-path resolution; a chat for an
    unlisted name lists again before giving up.
    """

    def __init__(self):
        self._modules = None
        self._controller = None
        self._models = {}
        self._loaded_name = None

    # -- engine access --------------------------------------------------

    def _engine(self):
        """The fyrn modules, imported on first use. Raises
        EngineError(MSG_FYRN_MISSING) when Fyrn is absent."""
        if self._modules is None:
            self._modules = _import_fyrn()
        return self._modules

    def _get_controller(self):
        modules = self._engine()
        if self._controller is None:
            runner = modules.runner.LlamaRunner()
            self._controller = modules.core.ModelController(runner)
        return self._controller

    # -- the model list ---------------------------------------------------

    def _list_model_dicts(self):
        """Every chat model Fyrn sees, as its model dicts: Fyrn's
        models folder plus the models Fyrn discovers in Ollama's
        store (Fyrn reads that store itself; DUO never does),
        de-duplicated by path the way Fyrn Chat Test merges them,
        with embedding-only models left out -- they cannot chat.
        Refreshes the name-to-path cache as a side effect."""
        modules = self._engine()
        folder = fyrn_models_folder()
        models, _skipped = modules.core.scan_models(folder)
        discovered, _warnings = modules.discover.discover_ollama_models()
        seen = set()
        for model in models:
            seen.add(str(model.get("path")))
        for model in discovered:
            key = str(model.get("path"))
            if key in seen:
                continue
            seen.add(key)
            models.append(model)
        chat_models = [m for m in models if not is_embedding_model(m)]
        self._models = {}
        for model in chat_models:
            name = model.get("name")
            if name:
                self._models[name] = model
        return chat_models

    def list_models(self, timeout=8):
        """Return the chat models' names, sorted, or raise
        EngineError. ``timeout`` is accepted for the old client's
        call shape and unused: the scan is local disk reads."""
        models = self._list_model_dicts()
        names = [m["name"] for m in models if m.get("name")]
        return sorted(names, key=str.lower)

    def list_models_with_sizes(self, timeout=8):
        """Return the chat models as [(name, size-in-bytes), ...],
        sorted by name, or raise EngineError. A model whose size
        Fyrn does not report carries None -- it still lists, just
        without a size."""
        models = self._list_model_dicts()
        pairs = []
        for model in models:
            name = model.get("name")
            if not name:
                continue
            size = model.get("size")
            if not isinstance(size, (int, float)):
                size = None
            pairs.append((name, size))
        return sorted(pairs, key=lambda pair: pair[0].lower())

    # -- chat ---------------------------------------------------------------

    def _resolve(self, model):
        """The model dict for a picker name, listing again on a
        cache miss. Raises EngineError(MSG_MODEL_FAILED) when
        Fyrn has no such model."""
        if model not in self._models:
            self._list_model_dicts()
        entry = self._models.get(model)
        if entry is None:
            raise EngineError(MSG_MODEL_FAILED)
        return entry

    def chat_stream(self, model, messages, on_chunk, cancel_event,
                    timeout=CHAT_TIMEOUT, on_metrics=None, on_retry=None,
                    options=None):
        """Stream one chat turn. Calls on_chunk(text) per delta.

        Returns the full reply text. Raises Cancelled if
        cancel_event is set, EngineError with a plain message on
        failure -- including a generation that ends with an
        empty reply (v0.60, P1-13), which is a failed turn,
        never a silent success.

        The model loads first when it is not the one already
        loaded (a load failure is a load failure: the model
        message, or the too-big message naming the model). A
        failure before the first streamed chunk is also the model
        message; a failure after chunks have streamed is the
        turn message -- the model demonstrably loaded, so the
        words must not blame it (v0.58, P2-52).

        on_metrics (optional) is called once at the end with the
        turn's timing: eval_count and eval_duration_ns from the
        runner's stats, ttft_s, and total_s measured here (load
        plus generation -- what the user waited).
        prompt_eval_count is None: Fyrn does not report prompt
        tokens, and the stats line shows n/a for a number it
        doesn't have -- never an invented one.

        ``timeout``, ``on_retry`` and ``options`` are accepted for
        the old client's call shape and unused: there is no
        server to time out or retry against, and Fyrn exposes no
        per-turn options (it has no temperature knob; that is why
        the panes' Temperature controls are gone in this version).
        """
        started = time.monotonic()
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()
        modules = self._engine()
        entry = self._resolve(model)
        controller = self._get_controller()
        if self._loaded_name != model or not controller.is_loaded():
            try:
                controller.load(
                    entry["path"], cancel_event=cancel_event,
                    display_name=model)
            except modules.core.ModelTooBigError:
                raise EngineError(MSG_MODEL_TOO_BIG % model)
            except modules.core.ModelNotFoundError:
                raise EngineError(MSG_MODEL_GONE)
            except modules.runner.LoadCancelled:
                raise Cancelled()
            except modules.runner.RunnerError:
                raise EngineError(MSG_MODEL_FAILED)
            self._loaded_name = model
        instructions, history, prompt = _split_messages(messages)
        chunks = []

        def _on_token(text):
            chunks.append(text)
            on_chunk(text)

        try:
            stats = controller.runner.generate(
                prompt, on_token=_on_token,
                cancel_event=cancel_event, history=history,
                instructions=instructions)
        except modules.runner.GenerationCancelled:
            raise Cancelled()
        except modules.runner.RunnerError:
            if chunks:
                raise EngineError(MSG_TURN_FAILED)
            raise EngineError(MSG_MODEL_FAILED)
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()
        if not "".join(chunks):
            # v0.60 (P1-13): the generation ended without a single
            # token of reply. That is a failed turn, not a reply --
            # returning "" would put an empty message in the
            # history and the relay would forward the emptiness
            # onward.
            raise EngineError(MSG_EMPTY_REPLY)
        if on_metrics is not None:
            stats = stats or {}
            elapsed_s = stats.get("elapsed_s")
            on_metrics({
                "eval_count": stats.get("tokens"),
                "eval_duration_ns": (
                    int(elapsed_s * 1e9)
                    if isinstance(elapsed_s, (int, float)) else None),
                "prompt_eval_count": None,
                "ttft_s": stats.get("ttft_s"),
                "total_s": time.monotonic() - started,
            })
        return "".join(chunks)

    def running_models(self, timeout=5):
        """The model Fyrn currently holds: {name: size in bytes},
        or {} when none is loaded. Raises EngineError with the
        missing message when Fyrn is absent -- the same contract
        the old client's down state had. ``timeout`` is accepted
        for the old call shape and unused."""
        self._engine()
        controller = self._controller
        if (controller is None or not controller.is_loaded()
                or self._loaded_name is None):
            return {}
        size = None
        entry = self._models.get(self._loaded_name)
        if entry is not None:
            size = entry.get("size")
        if not isinstance(size, (int, float)):
            try:
                size = os.path.getsize(controller.current_path)
            except (OSError, TypeError):
                size = None
        if size is None:
            return {}
        return {self._loaded_name: size}
