"""Arayüz modülleri arasında paylaşılan küçük yardımcılar."""
from contextlib import contextmanager

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFormLayout, QGroupBox, QLabel, QMessageBox

DASH = "—"


def dig(data, *keys, default=None):
    """İç içe sözlükte güvenli erişim."""
    for key in keys:
        if isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return default
    return default if data is None else data


def gib(n):
    return f"{n / 2**30:.1f} GiB" if isinstance(n, (int, float)) else DASH


def selectable(text):
    # Not: kaydırma alanındaki satır kaydıran QLabel'lar yüksekliği yanlış hesaplayıp
    # metni kesiyor; bu yüzden satır kaydırma kapalı tutulur.
    label = QLabel(str(text))
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def group(title, rows):
    box = QGroupBox(title)
    form = QFormLayout(box)
    for name, value in rows:
        form.addRow(f"{name}:", selectable(value))
    return box


@contextmanager
def waiting():
    """Uzun süren daemon çağrıları sırasında bekleme imleci gösterir."""
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


def confirm(parent, title, text):
    answer = QMessageBox.question(parent, title, text)
    return answer == QMessageBox.StandardButton.Yes
