"""Single instance for DUO (v0.44, redesign U27).

Only one DUO window runs at a time. A second launch finds the
running instance through a local socket, asks it to come to the
front, and exits -- it never opens a second window, never starts
a second phone-deck server, and never writes to the pairs or
presets files alongside the first.

PySide6 is imported lazily inside the functions (the duo_menu.py
precedent), so this module -- and dual_chat.py, which imports it
-- still import cleanly on a machine without PySide6 (the v0.14
first-run guard).
"""
import os

SINGLE_INSTANCE_NAME = "BrokeNBoredDUO"
SINGLE_INSTANCE_ENV = "DUO_SINGLE_INSTANCE_NAME"
SHOW_MESSAGE = b"show"
# How long to wait for the running instance to answer, and for
# the show message to leave. The socket is local; a live DUO
# answers at once, and a dead one refuses at once.
CONNECT_TIMEOUT_MS = 1000
WRITE_TIMEOUT_MS = 1000


def single_instance_name():
    """The local-server name for this run: the env override when
    set (tests use a unique name per run), else the default."""
    return os.environ.get(SINGLE_INSTANCE_ENV) or SINGLE_INSTANCE_NAME


def notify_existing(name):
    """Ask the DUO already running under `name` to come to the
    front. Returns True when one answered, False when none did
    (nothing listening under that name -- including a stale socket
    left behind by a crashed DUO, which refuses the connection)."""
    from PySide6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(CONNECT_TIMEOUT_MS):
        return False
    sock.write(SHOW_MESSAGE)
    sock.waitForBytesWritten(WRITE_TIMEOUT_MS)
    sock.disconnectFromServer()
    return True


class SingleInstance:
    """The first instance's end of the single-instance socket.

    acquire() tries to become the one running DUO: when another
    DUO already answers under this name it reports already_running
    (the caller notifies it and exits); otherwise it listens, and
    every later launch's connection calls the show callback.
    """

    def __init__(self, name=None, on_show=None):
        self.name = name or single_instance_name()
        self.server = None
        self.already_running = False
        self._on_show = on_show

    def acquire(self):
        """Become the running DUO under this name. Returns True
        when this instance owns the name now; False when another
        DUO already does (already_running is True then, and the
        other instance has been asked to come to the front)."""
        if self.server is not None:
            return True
        if notify_existing(self.name):
            self.already_running = True
            return False
        from PySide6.QtNetwork import QLocalServer
        # A crashed DUO leaves its socket file behind; without the
        # remove, listen fails "Address in use" forever (probed:
        # a SIGKILLed listener's socket refuses connections and
        # blocks a fresh listen until removeServer clears it).
        QLocalServer.removeServer(self.name)
        self.server = QLocalServer()
        self.server.newConnection.connect(self._handle_connection)
        if not self.server.listen(self.name):
            # Lost a race with another launch starting at the same
            # moment: ask it to come forward instead.
            self.server.close()
            self.server = None
            if notify_existing(self.name):
                self.already_running = True
                return False
            # Nobody answers and this process cannot listen either;
            # run anyway -- a second window in this rare case beats
            # refusing to start at all.
            return True
        return True

    def set_show_callback(self, callback):
        """The function to call when a later launch asks this
        instance to come to the front (the window's show + raise +
        activate, wired up once the window exists)."""
        self._on_show = callback

    def release(self):
        """Stop listening. Safe to call when nothing was acquired
        (a second instance that never listened)."""
        if self.server is not None:
            self.server.close()
            self.server = None

    def _handle_connection(self):
        if self.server is None:
            return
        conn = self.server.nextPendingConnection()
        while conn is not None:
            conn.deleteLater()
            conn = self.server.nextPendingConnection()
        if self._on_show is not None:
            self._on_show()
