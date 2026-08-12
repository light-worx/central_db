"""
:mod:`toast` -- a minimal, non-modal, auto-dismissing notification bubble.

OpenLP itself has no built-in "toast" widget -- only a plain
``QStatusBar`` (``MainWindow.show_status_message()``) and the fully
modal, blocking ``QMessageBox``. For the automatic startup/shutdown
sync, a modal dialog on every single failure would be far too intrusive
for something that's meant to run silently in the background -- but a
failed sync is still worth surfacing more prominently than a status bar
line that's easy to miss. This fills that gap.
"""

from PyQt5 import QtCore, QtWidgets


class Toast(QtWidgets.QFrame):
    """
    A small, frameless, auto-dismissing message bubble anchored to the
    bottom-right of its parent window.
    """

    def __init__(self, parent, message, is_error=True, duration_ms=6000):
        super(Toast, self).__init__(parent)
        self.setWindowFlags(QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)

        background = '#b23b3b' if is_error else '#3b8132'
        self.setStyleSheet(
            f'Toast {{ background-color: {background}; border-radius: 6px; }} '
            f'QLabel {{ color: white; }}'
        )

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        label = QtWidgets.QLabel(message, self)
        label.setWordWrap(True)
        layout.addWidget(label)

        self.setMaximumWidth(360)
        self.adjustSize()
        self._position()

        QtCore.QTimer.singleShot(duration_ms, self.close)

    def _position(self):
        parent = self.parent()
        if parent is not None:
            top_left = parent.mapToGlobal(QtCore.QPoint(0, 0))
            geometry = QtCore.QRect(top_left, parent.size())
        else:
            geometry = QtWidgets.QApplication.primaryScreen().availableGeometry()
        x = geometry.x() + geometry.width() - self.width() - 24
        y = geometry.y() + geometry.height() - self.height() - 48
        self.move(x, y)


def show_toast(parent, message, is_error=True, duration_ms=6000):
    """
    Show a :class:`Toast` and return it. Callers should keep the
    returned reference until it closes (e.g. as an instance attribute)
    -- ``Qt.ToolTip`` top-level widgets aren't reliably kept alive by
    Qt's normal parent/child ownership alone, so an unreferenced Python
    wrapper can be garbage-collected before the widget is ever shown.
    """
    toast = Toast(parent, message, is_error=is_error, duration_ms=duration_ms)
    toast.show()
    return toast