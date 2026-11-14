"""DUO's accessibility presets (v0.48, redesign R10, entry 015).

One preset so far: Big text. When it is on, the app's text gets
bigger everywhere, immediately -- no restart. Two mechanisms,
because one is not enough:

  * The application font grows by BIG_TEXT_FONT_DELTA_PT. That
    reaches every top-level window (the About, How to Use, and
    confirmation dialogs), but NOT the main window's widgets: the
    main window carries the v0.45 focus-ring stylesheet, and a
    widget under a styled ancestor no longer follows the app font
    (probed: a QPlainTextEdit under a styled parent stayed 9 pt
    while the app font went to 11 pt).
  * The main window's stylesheet gains a `*` font-size rule at
    the grown size, composed with the focus-ring stylesheet. That
    reaches every widget in the main window -- except the labels
    that set their own size in a widget stylesheet (widget-level
    beats ancestor-level), which are re-styled explicitly: the
    11 px stats, activity, session, attach, relay-chip, and meter
    labels go to 14 px, the grey field explanations go to 14 px,
    and the 14 px pane titles go to 16 px so headers stay headers.

Off restores exactly: the base app font, the focus-ring-only
stylesheet, and the original label sizes.

The setting lives in QSettings under accessibility/big_text,
default off. A checkable "Big text" item in the Help menu toggles
it (the v0.45 "Reduce motion" pattern). On a first run the app
offers it once, with a plain Yes / No dialog that defaults to No
-- the offer itself is recorded in QSettings
(accessibility/big_text_offered), so it never appears again.

The app font is process-global, so the base font is captured once
(class state on BigText) and the grow/restore is idempotent: a
second window built while big text is on styles its own labels
and sets its own window stylesheet, but never grows the app font
twice.

PySide6 is imported lazily inside the functions (the duo_menu.py
precedent), so importing this module never needs Qt -- dual_chat's
first-run guard must still be able to import dual_chat and print
its plain "run install.sh first" message on a machine with no
PySide6.
"""

# The QSettings keys, default off / unoffered.
SETTING_BIG_TEXT = "accessibility/big_text"
SETTING_BIG_TEXT_OFFERED = "accessibility/big_text_offered"
# How much bigger the text gets, in points (the app font) -- the
# small labels grow by the same step in pixels (11 -> 14, 14 -> 16).
BIG_TEXT_FONT_DELTA_PT = 2
# The small labels' sizes, in pixels. 11 px is the size the labels
# are created with all over build_window, and the size EXPLAIN_STYLE
# (duo_constants.py) sets for the field explanations; 14 px is the
# pane titles' size. The big sizes must stay in step with both.
SMALL_LABEL_NORMAL_PX = 11
SMALL_LABEL_BIG_PX = 14
TITLE_NORMAL_PX = 14
TITLE_BIG_PX = 16
# The first-boot offer: plain words, no jargon, no em-dash.
BIG_TEXT_OFFER_TITLE = "Big text"
BIG_TEXT_OFFER_TEXT = "DUO can make its text bigger. Turn on Big text?"


def small_label_style(big):
    """The stylesheet for a plain small label (stats, activity,
    session total, attach note, relay chip, meter): 11 px normally,
    14 px with Big text on."""
    return "font-size: %dpx;" % (
        SMALL_LABEL_BIG_PX if big else SMALL_LABEL_NORMAL_PX)


def explanation_style(big):
    """The stylesheet for a grey field-explanation label: 11 px
    normally, 14 px with Big text on. At normal size this is
    EXPLAIN_STYLE exactly."""
    return "font-size: %dpx; color: #6b6b6b;" % (
        SMALL_LABEL_BIG_PX if big else SMALL_LABEL_NORMAL_PX)


def title_style(big, color):
    """The stylesheet for a pane title in the side's `color`: bold
    14 px normally, bold 16 px with Big text on. At normal size
    this is the title's stylesheet exactly."""
    return "font-weight: bold; font-size: %dpx; color: %s;" % (
        TITLE_BIG_PX if big else TITLE_NORMAL_PX, color)


class BigText:
    """The Big text preset: the QSettings wrapper and the applier.

    `window` is the main window (its stylesheet carries the font
    rule); `labels` is the window's self-sized labels as (label,
    style_builder) pairs, where style_builder takes True/False for
    big/normal and returns the stylesheet string. The constructor
    reads the persisted setting and applies it, so a window built
    with the setting on starts big.
    """

    # The app font before Big text ever grew it, captured on the
    # first grow, and whether the font is grown right now. Class
    # state, because the font belongs to the process, not to one
    # window: two windows must never grow it twice or restore it
    # to a grown size. (The window stylesheet is per window and is
    # set on every apply, so a second window still gets its rule.)
    _base_font = None
    _applied = False

    def __init__(self, window, prefs, labels=()):
        self._window = window
        self._prefs = prefs
        self._labels = list(labels)
        self._value = bool(prefs.value(
            SETTING_BIG_TEXT, False, type=bool))
        self._apply_font()
        self._apply_labels()

    @property
    def value(self):
        return self._value

    def set(self, value):
        """Turn Big text on or off: persist the setting, grow or
        restore the fonts, and re-style this window's self-sized
        labels. Immediate, both directions."""
        self._value = bool(value)
        self._prefs.setValue(SETTING_BIG_TEXT, self._value)
        self._apply_font()
        self._apply_labels()

    def offer(self, parent):
        """The one-time first-boot offer. Returns True when the
        dialog was shown, False when it was already offered once.
        Yes turns Big text on; No (and Escape, which picks the
        default No) leaves it off. Either answer records the offer
        so it never appears again."""
        if bool(self._prefs.value(
                SETTING_BIG_TEXT_OFFERED, False, type=bool)):
            return False
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            parent, BIG_TEXT_OFFER_TITLE, BIG_TEXT_OFFER_TEXT,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        self._prefs.setValue(SETTING_BIG_TEXT_OFFERED, True)
        if answer == QMessageBox.StandardButton.Yes:
            self.set(True)
        return True

    def _apply_labels(self):
        for label, style_builder in self._labels:
            label.setStyleSheet(style_builder(self._value))

    def _font_rule(self):
        """The `*` font-size rule for the grown size, or "" when
        the base font has no usable size (then there is no rule to
        grow by, and the window keeps the focus ring only)."""
        if self._base_font is None:
            return ""
        if self._base_font.pointSize() > 0:
            return "* { font-size: %dpt; }" % (
                self._base_font.pointSize() + BIG_TEXT_FONT_DELTA_PT)
        if self._base_font.pixelSize() > 0:
            return "* { font-size: %dpx; }" % (
                self._base_font.pixelSize() + BIG_TEXT_FONT_DELTA_PT)
        return ""

    def _apply_font(self):
        from PySide6 import QtGui, QtWidgets
        from duo_motion import FOCUS_RING_STYLESHEET
        app = QtWidgets.QApplication.instance()
        if self._value:
            if app is not None and not BigText._applied:
                BigText._base_font = app.font()
                font = QtGui.QFont(BigText._base_font)
                if font.pointSize() > 0:
                    font.setPointSize(
                        font.pointSize() + BIG_TEXT_FONT_DELTA_PT)
                    app.setFont(font)
                    BigText._applied = True
                elif font.pixelSize() > 0:
                    font.setPixelSize(
                        font.pixelSize() + BIG_TEXT_FONT_DELTA_PT)
                    app.setFont(font)
                    BigText._applied = True
                # A font with neither a point nor a pixel size
                # cannot be grown by a fixed step; leave it alone.
            rule = self._font_rule()
            sheet = FOCUS_RING_STYLESHEET
            if rule:
                sheet = sheet + "\n" + rule + "\n"
            self._window.setStyleSheet(sheet)
        else:
            if app is not None and BigText._applied:
                app.setFont(BigText._base_font)
                BigText._applied = False
            self._window.setStyleSheet(FOCUS_RING_STYLESHEET)


def install_accessibility(window, small_labels, prefs):
    """Install R10 on a fully built window: read the persisted
    Big text setting and apply it to the app font, the window
    stylesheet, and this window's self-sized labels. Returns a
    BigText handle.

    Call after install_motion: the focus-ring stylesheet must
    already be in place, because the font rule is composed with
    it. `small_labels` is the window's self-sized labels as
    (label, style_builder) pairs, collected in build_window once
    every widget exists. `prefs` is the window's QSettings.
    """
    return BigText(window, prefs, small_labels)
