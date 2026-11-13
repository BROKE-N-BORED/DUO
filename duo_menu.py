"""DUO's menu bar (v0.30, audit T18).

Three menus -- File, Relay, Help -- plus keyboard shortcuts for the
main actions. The menu adds access, not capability: every item calls
the same handler as the button it mirrors. It lives in its own
module so dual_chat.py stays under the 4,000-line bound the
ModuleSplitTests pin.

build_menu_bar(window, handlers, version) builds the bar and returns
(menu_bar, actions), where actions maps each action name to its
QAction so build_window can mirror the buttons' enabled states onto
them in refresh_buttons().

PySide6 is imported lazily inside the functions (the duo_icon.py
precedent), so importing this module never needs Qt -- dual_chat's
first-run guard must still be able to import dual_chat and print
its plain "run install.sh first" message on a machine with no
PySide6.
"""

def about_text(version):
    """The About dialog's text: name + version, what DUO is, and the
    license line. No links, no web."""
    return ("DUO %s: a dual-window chat interface for two local "
            "Ollama models.\n\nMIT License (see LICENSE)."
            % version)


def make_about_dialog(parent, version):
    """Build the About dialog without showing it. Showing is
    show_about_dialog's job, so tests can read the dialog's text
    without blocking on exec."""
    from PySide6 import QtWidgets
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("About DUO")
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel(about_text(version)))
    close_btn = QtWidgets.QPushButton("Close")
    close_btn.clicked.connect(dialog.close)
    layout.addWidget(close_btn)
    return dialog


def show_about_dialog(parent, version):
    """Show the About dialog and wait for Close."""
    make_about_dialog(parent, version).exec()


# The built-in How to Use index (U22): short plain-language
# sections, one per thing DUO does. No web, no links, no em-dashes.
HOW_TO_USE_SECTIONS = (
    ("The two panes",
     "Each pane is one model. Pick a model in a pane's dropdown "
     "before you can send to it. Nothing is picked for you. The "
     "Temperature control under each picker sets how focused or "
     "random that side's replies are. 0.7 is Ollama's default."),
    ("Sending a message",
     "Type in the message box at the bottom, then Send to A, Send "
     "to B, or Send to Both. Send to Both sends the same message "
     "to each side, one after the other, so the PC never runs two "
     "models at the same time. Ctrl+Enter sends to both from "
     "anywhere in the window. Tick Enter to send and plain Enter "
     "sends to both too, with Shift+Enter still adding a new "
     "line."),
    ("The relay",
     "Start Relay lets the two models talk to each other. You "
     "write the first message, pick which side starts, how many "
     "turns at most, and whether each side knows it is talking to "
     "another AI (Aware) or thinks it is talking to a person "
     "(Blind). Stop on agreement ends the relay when a side "
     "agrees. Review each hop pauses before every reply so you "
     "can read or edit it first."),
    ("Tasks",
     "Describe a task for one model, pick who does it, and pick "
     "what the other side does next: Review it, Continue it, or "
     "Nothing. Rounds sets how many times the work passes back "
     "and forth. With Review it or Continue it the task always "
     "runs at least 2 rounds."),
    ("Attaching files",
     "Attach adds text files to your next message, and the model "
     "reads their contents. Text files only, up to 100 KB each, "
     "at most 5 files. The files ride with the message you send, "
     "or with the relay's first message, and then they clear."),
    ("Transcripts and conversations",
     "Save keeps one side's transcript as a text file. Save both "
     "keeps both sides in one file, and Save full transcript "
     "keeps both sides mixed in the order the conversation "
     "actually happened. Save conversation keeps the whole "
     "conversation in a file you can open again later, with both "
     "models and both instruction fields. Import loads a saved "
     "transcript back into one pane. Search finds words in a "
     "pane."),
    ("Saved pairs and relay presets",
     "A saved pair remembers which model goes in each pane, so "
     "you can pick both at once. A relay preset remembers the "
     "whole relay row: start side, mode, turn cap, stop words, "
     "and the two checkboxes. Name a pair or preset, save it, "
     "and pick it from the dropdown later. Nothing loads by "
     "itself."),
    ("The phone deck",
     "Phone deck shows a QR code for your phone. Scan it on your "
     "home WiFi and your phone gets a small page where you can "
     "watch the chat, send messages to either side or both, and "
     "export the full transcript. Anyone on your WiFi can open "
     "the page. It only works on your home network."),
    ("The update check",
     "DUO checks GitHub once when it starts and says in the "
     "status bar when a newer version is out. Check for updates "
     "runs the same check whenever you like. DUO never downloads "
     "or installs anything. You download a new version from "
     "GitHub yourself."),
)


def how_to_use_text():
    """The whole index as plain text: each heading on its own line,
    then its section, with a blank line between sections."""
    return "\n\n".join("%s\n%s" % (heading, body)
                       for heading, body in HOW_TO_USE_SECTIONS)


def make_how_to_use_dialog(parent):
    """Build the How to Use dialog without showing it. Showing is
    show_how_to_use_dialog's job, so tests can read the index
    without blocking on exec."""
    from PySide6 import QtWidgets
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("How to use DUO")
    layout = QtWidgets.QVBoxLayout(dialog)
    text = QtWidgets.QTextEdit()
    text.setPlainText(how_to_use_text())
    text.setReadOnly(True)
    text.setMinimumSize(560, 420)
    layout.addWidget(text)
    close_btn = QtWidgets.QPushButton("Close")
    close_btn.clicked.connect(dialog.close)
    layout.addWidget(close_btn)
    return dialog


def show_how_to_use_dialog(parent):
    """Show the How to Use dialog and wait for Close."""
    make_how_to_use_dialog(parent).exec()


def build_menu_bar(window, handlers, version):
    """Build DUO's menu bar on `window` (a QMainWindow).

    `handlers` maps each action name to the callable the matching
    button already uses: save_conversation, open_conversation,
    save_full, save_both, debug_report, quit, start_relay,
    stop_relay, check_updates. About and How to use are the two
    items with no button: they open dialogs.

    Returns (menu_bar, actions): the QMenuBar and a dict mapping
    every action name (plus "about" and "how_to_use") to its
    QAction.
    """
    from PySide6 import QtGui
    menu_bar = window.menuBar()
    actions = {}

    def add(menu, name, text, handler, shortcut=None):
        action = QtGui.QAction(text, menu)
        if shortcut is not None:
            action.setShortcut(QtGui.QKeySequence(shortcut))
        action.triggered.connect(handler)
        menu.addAction(action)
        actions[name] = action
        return action

    file_menu = menu_bar.addMenu("File")
    add(file_menu, "save_conversation", "Save conversation",
        handlers["save_conversation"], "Ctrl+S")
    add(file_menu, "open_conversation", "Open conversation",
        handlers["open_conversation"], "Ctrl+O")
    add(file_menu, "save_full", "Save full transcript",
        handlers["save_full"], "Ctrl+Shift+S")
    add(file_menu, "save_both", "Save both", handlers["save_both"])
    file_menu.addSeparator()
    add(file_menu, "debug_report", "Debug report",
        handlers["debug_report"])
    file_menu.addSeparator()
    add(file_menu, "quit", "Quit", handlers["quit"], "Ctrl+Q")

    relay_menu = menu_bar.addMenu("Relay")
    add(relay_menu, "start_relay", "Start relay",
        handlers["start_relay"])
    add(relay_menu, "stop_relay", "Stop relay",
        handlers["stop_relay"])

    help_menu = menu_bar.addMenu("Help")
    add(help_menu, "check_updates", "Check for updates",
        handlers["check_updates"])
    add(help_menu, "how_to_use", "How to use",
        lambda: show_how_to_use_dialog(window))
    add(help_menu, "about", "About",
        lambda: show_about_dialog(window, version))

    return menu_bar, actions
