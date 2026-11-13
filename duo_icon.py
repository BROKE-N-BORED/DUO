"""The DUO window icon, drawn in code -- no image file to ship
(the push path cannot carry binaries).

Split out of dual_chat.py in v0.25 (audit T14). PySide6 is
imported lazily inside the function, as before, so importing
this module never needs Qt.
"""


def make_app_icon():
    """The DUO window icon: two side-by-side panes in a dark rounded
    square, drawn in code -- no image file to ship (the push path
    cannot carry binaries)."""
    from PySide6 import QtCore, QtGui
    size = 64
    image = QtGui.QImage(size, size, QtGui.QImage.Format_ARGB32)
    image.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor("#1f2a33"))
    painter.drawRoundedRect(2, 2, size - 4, size - 4, 12, 12)
    painter.setBrush(QtGui.QColor("#5b9bd5"))
    painter.drawRoundedRect(10, 14, 20, 36, 4, 4)
    painter.setBrush(QtGui.QColor("#70ad47"))
    painter.drawRoundedRect(34, 14, 20, 36, 4, 4)
    painter.end()
    return QtGui.QIcon(QtGui.QPixmap.fromImage(image))
