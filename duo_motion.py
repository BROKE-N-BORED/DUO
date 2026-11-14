"""Micro-interactions, finish moments, and the focus ring (v0.45,
redesign R7; library entries 001, 008, 023).

Three features plus one setting, all in this module so dual_chat.py
stays under its 4,000-line bound:

- Button dip (entry 001): a pressed button shrinks to 0.97 of its
  size over 75 ms and restores over 75 ms -- the entry's 150 ms is
  the whole press feedback, not one direction. A QPropertyAnimation
  on the button's geometry, driven by one event filter installed
  on every QPushButton in the window. Probed before building: the
  layout does NOT fight the animation -- the geometry shrinks
  monotonically, restores exactly, and stays put (probe_r7.py).
  Visual only: events are never swallowed, so clicks fire exactly
  as before. No dip for a disabled button, and none at all while
  reduced motion is on.
- Completion cue (entry 008): on a successful turn completion the
  side's activity dot flashes bright lime for 200 ms, then goes
  back to idle grey if the side is still idle. A timed color swap
  through a single-shot QTimer, NOT an animation -- it is a state
  change, so it stays under reduced motion. No flash on a failed
  turn (the red error state is already the cue). The flash must be
  lime, not the thinking green: the dot is already green while the
  turn runs, so green-to-green would be invisible.
- Focus ring (entry 023): an app-wide stylesheet with :focus rules
  for buttons, text edits, line edits, combos, spinboxes, and
  lists -- a 2 px solid border in a clear blue plus a light
  background tint, unmistakable next to the native 1 px dotted
  line. Probed before building: unfocused widgets keep their
  native Fusion look exactly (zero differing pixels), and a
  focused widget shows the blue ring (probe_r7.py, probe_focus2.py).
  A state change, not motion -- it stays under reduced motion.
- Reduced motion: a checkable Help-menu item (duo_menu.py), kept
  in QSettings under motion/reduced, default off. On: the dip
  stops; the flash and the focus ring keep working, because they
  are state changes, not motion.

Director spring (v0.46, redesign R8; library entry 002): the
Director toggle springs instead of snapping. The director
container's maximumHeight animates -- expand runs 350 ms with
OutBack (the property overshoots past full height, then settles
back: the spring feel), collapse runs 250 ms with OutCubic (a
smooth ease-out; overshooting below zero is meaningless). Both
under the 400 ms cap, and both interruptible: clicking
mid-flight reverses from the current height, never waiting for
the running animation. The chevron flips the moment the toggle
is clicked (dual_chat.py owns it); the spring only moves the
height. The layout reflows as the director grows and shrinks,
so the panes follow the spring -- probed before building
(probe_spring.py): the widget height tracks maximumHeight
exactly, sizeHint stays valid while hidden, and OutBack
overshoots the property to 330 against a full height of 301.
Reduced motion makes the toggle instant, like the dip. The
Relay/Pairs/Tasks section toggles stay instant by design: R8 is
the Director toggle only, the one that moves the whole window.

PySide6 is imported lazily inside the functions (the duo_menu.py /
duo_workers.py precedent), so this module -- and dual_chat.py,
which imports it -- still import cleanly on a machine without
PySide6 (the v0.14 first-run guard). The QObject subclasses live
inside __init__ methods, the duo_workers.py pattern.
"""

# Button dip (entry 001): how far a pressed button shrinks, and
# how long each half of the press takes.
DIP_SCALE = 0.97
DIP_PRESS_MS = 75
DIP_RELEASE_MS = 75
# Completion cue (entry 008): the lime flash, and how long it
# holds before the dot goes back to idle grey.
COMPLETION_FLASH_COLOR = "#a3e635"
COMPLETION_FLASH_MS = 200
# Focus ring (entry 023): the ring color (side A's blue, already in
# the palette) and the light tint a focused field gets.
FOCUS_RING_COLOR = "#2563eb"
FOCUS_RING_TINT = "#e8f0fe"
# Reduced motion: the QSettings key, default off.
SETTING_REDUCED_MOTION = "motion/reduced"
# Director spring (entry 002): the toggle's expand/collapse
# durations. Expand overshoots with OutBack (the spring feel);
# collapse eases out with OutCubic (overshooting below zero is
# meaningless). Both under the 400 ms cap.
DIRECTOR_EXPAND_MS = 350
DIRECTOR_COLLAPSE_MS = 250
DIRECTOR_SPRING_MAX_MS = 400
# Qt's QWIDGETSIZE_MAX, the default maximum size for a widget:
# the expanded director goes back to this cap so it can still
# grow when a section opens or the review frame appears. This
# PySide6 build does not bind the name, so the value is spelled
# out (confirmed by probe: a fresh widget reads 16777215).
QWIDGETSIZE_MAX = 16777215

# The focus ring, one rule per focusable widget class. Only :focus
# selectors: nothing here touches an unfocused widget, so the
# native look survives untouched (probed: zero differing pixels).
FOCUS_RING_STYLESHEET = (
    "QPushButton:focus { border: 2px solid %s; "
    "background-color: %s; }\n"
    "QTextEdit:focus { border: 2px solid %s; "
    "background-color: %s; }\n"
    "QPlainTextEdit:focus { border: 2px solid %s; "
    "background-color: %s; }\n"
    "QLineEdit:focus { border: 2px solid %s; "
    "background-color: %s; }\n"
    "QComboBox:focus { border: 2px solid %s; "
    "background-color: %s; }\n"
    "QSpinBox:focus, QDoubleSpinBox:focus { border: 2px solid %s; "
    "background-color: %s; }\n"
    "QListWidget:focus { border: 2px solid %s; "
    "background-color: %s; }\n"
    % ((FOCUS_RING_COLOR, FOCUS_RING_TINT) * 7)
)


def dot_stylesheet(color):
    """The activity dot's stylesheet for `color`: a filled round
    dot, 12 px across. The same format set_side_state uses -- one
    source of truth for the shape, so the flash restores exactly
    what the state setter paints."""
    return "background-color: %s; border-radius: 6px;" % color


class ReducedMotion:
    """The reduced-motion setting: read once from QSettings, written
    back on change. Default off. The dip consults it live, so
    toggling the menu item takes effect on the next press."""

    def __init__(self, prefs):
        self._prefs = prefs
        self._value = bool(prefs.value(
            SETTING_REDUCED_MOTION, False, type=bool))

    @property
    def value(self):
        return self._value

    def set(self, value):
        self._value = bool(value)
        self._prefs.setValue(SETTING_REDUCED_MOTION, self._value)


class PressDipFilter:
    """Dips every button it watches (see the module docstring).

    `reduced_motion` is a callable returning True while reduced
    motion is on; `parent` owns the filter object (the window).
    Install the filter object (`.obj`) on each button with
    installEventFilter, or use install_press_dips() to cover a
    whole window at once. The filter never swallows an event.
    """

    def __init__(self, reduced_motion, parent=None):
        from PySide6 import QtCore
        outer = self

        class _Filter(QtCore.QObject):
            def eventFilter(self, watched, event):
                return outer._filter_event(watched, event)

        self._QtCore = QtCore
        self._reduced_motion = reduced_motion
        self._animations = {}  # button -> running QPropertyAnimation
        # button -> geometry before its current dip cycle. Recorded
        # on the first press of a cycle and cleared only when a
        # restore animation finishes, so a quick press-release-press
        # never mistakes a mid-animation geometry for the rest rect.
        self._rest = {}
        self.obj = _Filter(parent)

    def _shrunk(self, rect):
        """`rect` shrunk to DIP_SCALE around its own centre."""
        shrunk = self._QtCore.QRect(rect)
        shrunk.setWidth(max(1, int(rect.width() * DIP_SCALE)))
        shrunk.setHeight(max(1, int(rect.height() * DIP_SCALE)))
        shrunk.moveCenter(rect.center())
        return shrunk

    def _animate(self, button, target, duration, on_finished=None):
        current = self._animations.get(button)
        if current is not None:
            current.stop()
        anim = self._QtCore.QPropertyAnimation(button, b"geometry")
        anim.setDuration(duration)
        anim.setStartValue(button.geometry())
        anim.setEndValue(target)
        if on_finished is not None:
            anim.finished.connect(on_finished)
        self._animations[button] = anim
        anim.start()

    def _filter_event(self, watched, event):
        QtCore = self._QtCore
        if event.type() not in (QtCore.QEvent.MouseButtonPress,
                                QtCore.QEvent.MouseButtonRelease):
            return False
        # Left button only: a right-click never activates a button,
        # so it must not dip one either. (The type check above runs
        # first: only mouse events carry button().)
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return False
        if event.type() == QtCore.QEvent.MouseButtonPress:
            if watched.isEnabled() and not self._reduced_motion():
                if watched not in self._rest:
                    self._rest[watched] = watched.geometry()
                self._animate(watched, self._shrunk(self._rest[watched]),
                              DIP_PRESS_MS)
        elif event.type() == QtCore.QEvent.MouseButtonRelease:
            rest = self._rest.get(watched)
            if rest is not None:
                # A press dipped this button; restore it even if
                # reduced motion was switched on mid-press --
                # leaving it shrunk would be worse than 75 ms of
                # motion. No rest recorded means no dip happened
                # (disabled, reduced motion, or a release without
                # a press): nothing to restore.
                self._animate(watched, rest, DIP_RELEASE_MS,
                              on_finished=lambda b=watched:
                              self._rest.pop(b, None))
        return False


def install_press_dips(window, reduced_motion):
    """Install one PressDipFilter on every QPushButton in `window`.
    Call once, after the window is fully built, so no button is
    missed. Returns the filter (keep it alive: it owns the running
    animations)."""
    from PySide6 import QtWidgets
    dip = PressDipFilter(reduced_motion, parent=window)
    for button in window.findChildren(QtWidgets.QPushButton):
        button.installEventFilter(dip.obj)
    return dip


class CompletionFlash:
    """One side's completion cue (see the module docstring).

    `dot` is the side's activity-dot label, `idle_color` the grey
    it shows at rest, `is_idle` a callable returning True while the
    side is still idle. flash() paints the lime and starts the
    200 ms timer; when it fires, the grey comes back only if the
    side is still idle -- a new job starting first owns the light,
    and its own state setter has already repainted the dot.
    """

    def __init__(self, dot, idle_color, is_idle, parent):
        from PySide6 import QtCore
        self._dot = dot
        self._idle_color = idle_color
        self._is_idle = is_idle
        self._timer = QtCore.QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COMPLETION_FLASH_MS)
        self._timer.timeout.connect(self._clear)

    def flash(self):
        self._dot.setStyleSheet(
            dot_stylesheet(COMPLETION_FLASH_COLOR))
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _clear(self):
        if self._is_idle():
            self._dot.setStyleSheet(dot_stylesheet(self._idle_color))


class DirectorSpring:
    """The Director toggle's spring (entry 002, see the module
    docstring).

    Animates the director container's maximumHeight: expand runs
    DIRECTOR_EXPAND_MS with OutBack, collapse runs
    DIRECTOR_COLLAPSE_MS with OutCubic. Interruptible: starting a
    new direction mid-flight stops the running animation and
    starts from the current height. `target_collapsed` is the last
    requested state -- the toggle flips it, not the director's
    visibility, because mid-animation the visibility still shows
    the old state. Settled states: expanded means visible with
    maximumHeight back at QWIDGETSIZE_MAX (so the director can
    still grow when a section opens or the review frame appears);
    collapsed means hidden with maximumHeight 0 (so the next
    expand starts from 0). The reduced-motion key is read fresh
    from QSettings on every toggle, so the spring and the menu
    item can never disagree.
    """

    def __init__(self, director, prefs):
        from PySide6 import QtCore
        self._director = director
        self._prefs = prefs
        self._QtCore = QtCore
        self._anim = None
        self._target_collapsed = False

    @property
    def target_collapsed(self):
        return self._target_collapsed

    def is_animating(self):
        """True while a spring is still running."""
        return self._anim is not None

    def set_collapsed(self, collapsed, instant=False):
        """Move the director toward `collapsed`. The target flips
        at once; the height follows by spring, or instantly when
        `instant` is set (startup) or reduced motion is on."""
        collapsed = bool(collapsed)
        self._target_collapsed = collapsed
        self._stop_anim()
        if instant or self._reduced_motion():
            self._jump(collapsed)
        elif collapsed:
            self._animate_collapse()
        else:
            self._animate_expand()

    def stop(self):
        """Stop any running animation (closing time). The director
        keeps whatever height it had; the window is going away,
        and the next open applies the saved state instantly."""
        self._stop_anim()

    def _reduced_motion(self):
        return bool(self._prefs.value(
            SETTING_REDUCED_MOTION, False, type=bool))

    def _stop_anim(self):
        # stop() freezes the property at its current value --
        # that is the next animation's start value, so an
        # interrupted spring reverses from where it actually is.
        # stop() never emits finished, so the old finished
        # handler cannot fire afterwards.
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

    def _jump(self, collapsed):
        if collapsed:
            self._director.setMaximumHeight(0)
            self._director.setVisible(False)
        else:
            self._director.setMaximumHeight(QWIDGETSIZE_MAX)
            self._director.setVisible(True)

    def _full_height(self):
        # sizeHint stays valid while hidden (probed): the layout
        # still knows the content's preferred height.
        return self._director.sizeHint().height()

    def _animate_expand(self):
        director = self._director
        full = self._full_height()
        if full <= 0:
            self._jump(False)
            return
        start = director.maximumHeight()
        if start >= QWIDGETSIZE_MAX:
            # Already fully expanded (settled): nothing to do.
            return
        director.setVisible(True)
        anim = self._QtCore.QPropertyAnimation(
            director, b"maximumHeight")
        anim.setDuration(DIRECTOR_EXPAND_MS)
        anim.setEasingCurve(self._QtCore.QEasingCurve.Type.OutBack)
        anim.setStartValue(max(0, start))
        anim.setEndValue(full)
        anim.finished.connect(self._on_expand_finished)
        self._anim = anim
        anim.start()

    def _on_expand_finished(self):
        self._anim = None
        # Back to no cap: the director must still grow when a
        # section opens or the review frame appears.
        self._director.setMaximumHeight(QWIDGETSIZE_MAX)

    def _animate_collapse(self):
        director = self._director
        full = self._full_height()
        # Clamp to the full height: mid-expand the property can
        # overshoot past it (OutBack), but the widget itself never
        # grows past its sizeHint, so the collapse starts there.
        start = min(director.maximumHeight(), full)
        if not director.isVisible() or start <= 0:
            # Already collapsed (or nothing to shrink): settle.
            self._jump(True)
            return
        anim = self._QtCore.QPropertyAnimation(
            director, b"maximumHeight")
        anim.setDuration(DIRECTOR_COLLAPSE_MS)
        anim.setEasingCurve(self._QtCore.QEasingCurve.Type.OutCubic)
        anim.setStartValue(start)
        anim.setEndValue(0)
        anim.finished.connect(self._on_collapse_finished)
        self._anim = anim
        anim.start()

    def _on_collapse_finished(self):
        self._anim = None
        self._director.setVisible(False)
        # maximumHeight stays 0: the next expand starts from 0.


class Motion:
    """The handle install_motion returns: the reduced-motion
    setting, the dip filter, and the per-side completion flashes."""

    def __init__(self, reduced_motion, dip_filter, flashes):
        self.reduced_motion = reduced_motion
        self.dip_filter = dip_filter
        self._flashes = flashes

    def flash(self, side):
        """Flash `side`'s completion cue (a turn just finished)."""
        self._flashes[side].flash()

    def stop(self):
        """Stop the flash timers (closing time: a timer firing into
        a closed window would repaint a hidden dot for nothing)."""
        for flash in self._flashes.values():
            flash.stop()


def install_motion(window, widgets, side_state, idle_color, prefs):
    """Install R7 on a fully built window: the focus-ring
    stylesheet, the button dip on every QPushButton, the per-side
    completion flash, and the reduced-motion setting. Returns a
    Motion handle.

    `widgets` is build_window's widgets dict -- the activity dots
    live at widgets[side]["activity_dot"]. `side_state` maps each
    side to its current state name (the same dict object
    set_side_state writes, so the flash always sees the truth).
    `idle_color` is the dot's grey at rest, from SIDE_STATES.
    `prefs` is the window's QSettings.
    """
    window.setStyleSheet(FOCUS_RING_STYLESHEET)
    reduced = ReducedMotion(prefs)
    dip = install_press_dips(window, lambda: reduced.value)
    flashes = {}
    for side in ("A", "B"):
        flashes[side] = CompletionFlash(
            widgets[side]["activity_dot"], idle_color,
            lambda s=side: side_state[s] == "idle", window)
    return Motion(reduced, dip, flashes)
