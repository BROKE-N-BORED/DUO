"""Live tok/s sparkline (v0.47, redesign R9; rule U37).

U37 wants real measurements shown like science software -- a live
graph, not just a number. Each pane gets one sparkline on its
activity row, in the side's color: the generation rate, sampled
every 500 ms while that side generates, as a rolling window of the
last 60 samples (the last 30 seconds of generation). When the side
is idle the graph freezes, showing the recent history. No axes, no
labels -- the stats line next to it carries the exact number.

What the samples are, honestly. Ollama's stream carries no token
counts until the done chunk, so mid-turn there is no true token
count to sample: the live samples are characters streamed so far,
divided by CHARS_PER_TOKEN, divided by the seconds since the first
token. That is an approximation, and it feeds only the graph's
shape -- no number is ever drawn from it, and the stats line's
counts stay exact (they come from the done chunk, never estimated).
The final sample of a turn IS exact: the turn's tok/s from the
done chunk, the same number the stats line shows.

The window owns the sampling (a 500 ms timer per side, running
only while that side generates); this module owns the samples and
the widget that draws them. Reduced motion does not stop the
sparkline: it is data, not decoration -- a state change, not
motion. Summaries never feed it, the same as the stats line: a
summarize turn counts toward the session total but is not a turn
the user watches.

PySide6 is imported lazily inside __init__ (the duo_motion.py /
duo_workers.py precedent), so this module -- and dual_chat.py,
which imports it -- still import cleanly on a machine without
PySide6 (the v0.14 first-run guard).
"""

# How often the window samples the live rate while a side
# generates, and how many samples the graph keeps.
SPARKLINE_SAMPLE_MS = 500
SPARKLINE_MAX_SAMPLES = 60
# The graph's scale never drops below this: a flat line at zero
# must not divide by zero, and a slow start still draws.
SPARKLINE_MIN_SCALE = 1.0
# The widget's size: small enough to sit on the activity row
# beside the session total, big enough to read at a glance.
SPARKLINE_WIDTH = 120
SPARKLINE_HEIGHT = 24
# Characters per token, for the live samples only (see the module
# docstring): the standard rough conversion, used for the graph's
# shape and nothing else.
CHARS_PER_TOKEN = 4


def live_rate(chars, elapsed_s):
    """Approximate tok/s from characters streamed so far.

    chars / CHARS_PER_TOKEN / elapsed_s. None when there is no
    elapsed time yet (or the inputs are unusable) -- no sample is
    taken before the first token has had time to arrive."""
    if (not isinstance(chars, (int, float))
            or not isinstance(elapsed_s, (int, float))):
        return None
    if chars < 0 or elapsed_s <= 0:
        return None
    return (chars / CHARS_PER_TOKEN) / elapsed_s


def trim_samples(samples):
    """Keep the newest SPARKLINE_MAX_SAMPLES, oldest first out.
    In place; the window's graph is a rolling window, not a log."""
    extra = len(samples) - SPARKLINE_MAX_SAMPLES
    if extra > 0:
        del samples[:extra]


def sparkline_points(samples, width=SPARKLINE_WIDTH,
                     height=SPARKLINE_HEIGHT):
    """(x, y) points for the polyline, oldest first.

    Right-aligned: the newest sample always sits at the right edge
    and older samples extend left at a fixed step, so the line
    grows from the right and never rescales horizontally as samples
    arrive. Vertically auto-scaled to the largest sample in the
    window (never below SPARKLINE_MIN_SCALE): the max sample maps
    to the top (y = 0), zero maps to the bottom (y = height - 1).
    Empty samples draw nothing.
    """
    if not samples:
        return []
    top = max(max(samples), SPARKLINE_MIN_SCALE)
    count = len(samples)
    step = (width - 1) / (SPARKLINE_MAX_SAMPLES - 1)
    points = []
    for index, value in enumerate(samples):
        x = width - 1 - (count - 1 - index) * step
        y = (height - 1) * (1.0 - value / top)
        points.append((x, y))
    return points


class Sparkline:
    """One pane's sparkline: the samples and the widget that draws
    them.

    `color` is the side's color (SIDE_COLORS); `parent` owns the
    widget (the window). add_sample appends a rate and repaints;
    clear drops the samples and repaints blank. The widget is
    `.widget` -- put it in the layout. samples() returns a copy of
    the current samples, oldest first.
    """

    def __init__(self, color, parent=None):
        from PySide6 import QtCore, QtGui, QtWidgets
        outer = self
        self.color = color
        self._samples = []
        self._QtCore = QtCore
        self._QtGui = QtGui

        class _Widget(QtWidgets.QWidget):
            def paintEvent(self, event):
                outer._paint()

        self.widget = _Widget(parent)
        self.widget.setFixedSize(SPARKLINE_WIDTH, SPARKLINE_HEIGHT)

    def samples(self):
        return list(self._samples)

    def add_sample(self, value):
        """Append a rate sample (None is ignored; a negative value
        clamps to zero) and repaint."""
        if value is None:
            return
        self._samples.append(max(0.0, float(value)))
        trim_samples(self._samples)
        self.widget.update()

    def clear(self):
        """Drop every sample and repaint blank."""
        if self._samples:
            self._samples = []
            self.widget.update()

    def _paint(self):
        QtCore = self._QtCore
        QtGui = self._QtGui
        painter = QtGui.QPainter(self.widget)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        points = sparkline_points(
            self._samples, self.widget.width(), self.widget.height())
        if points:
            pen = QtGui.QPen(QtGui.QColor(self.color))
            pen.setWidth(1)
            painter.setPen(pen)
            if len(points) == 1:
                painter.drawPoint(QtCore.QPointF(*points[0]))
            else:
                painter.drawPolyline(
                    [QtCore.QPointF(x, y) for x, y in points])
        painter.end()
