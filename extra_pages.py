"""Ek sekmeler: geçmiş, güç durumları, profiller, süreçler, sistem ve Vulkan penceresi."""
import copy
import json
import time

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog,
    QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QMessageBox, QPlainTextEdit, QPushButton,
    QSizePolicy, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from lact_client import LactError
from profile_io import (
    build_export, config_to_changes, dumps, extract_profile, parse_export, pick_config, unique_name,
)
from logic import (
    POWER_KINDS, RULE_TEMPLATES, HistoryStore, fmt_age, fmt_mem,
    heuristics_from_cells, history_to_csv, nice_range, parse_modes, parse_profiles,
    parse_rule_text, parse_states, process_rows, rule_summary,
    sample_from_stats, states_to_config, temp_label, vbios_to_bytes,
)
from ui_common import DASH, confirm, waiting

PALETTE = ["#3daee9", "#f67400", "#27ae60", "#da4453", "#9b59b6", "#f1c40f"]


def button_row(*buttons):
    row = QHBoxLayout()
    row.addStretch(1)
    for button in buttons:
        row.addWidget(button)
    return row


# --------------------------------------------------------------------------- #
class LinePlot(QWidget):
    """Basit çok serili çizgi grafiği (yaş-değer noktaları)."""

    PAD_L, PAD_T, PAD_R, PAD_B = 54, 22, 12, 22

    def __init__(self, title, unit="", y_min=None, y_max=None):
        super().__init__()
        self.title, self.unit = title, unit
        self.y_min, self.y_max = y_min, y_max
        self.series = []  # [(ad, [(yaş_sn, değer)])]
        self.span = 60.0
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, series, window):
        self.series, self.span = series, float(window)
        self.update()

    def _bounds(self):
        vals = [v for _name, pts in self.series for _age, v in pts]
        lo = self.y_min if self.y_min is not None else (min(vals) - 2 if vals else 0.0)
        hi = self.y_max if self.y_max is not None else (max(vals) + 2 if vals else 1.0)
        if vals:
            hi = max(hi, max(vals))
        return lo, hi

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        text = pal.color(QPalette.ColorRole.Text)
        base = pal.color(QPalette.ColorRole.Base)
        r = QRectF(self.PAD_L, self.PAD_T,
                   max(self.width() - self.PAD_L - self.PAD_R, 10),
                   max(self.height() - self.PAD_T - self.PAD_B, 10))
        p.fillRect(r, base)

        lo, hi, step = nice_range(*self._bounds())
        grid = QColor(text)
        grid.setAlpha(45)
        for i in range(int(round((hi - lo) / step)) + 1):
            value = lo + i * step
            y = r.bottom() - (value - lo) / (hi - lo) * r.height()
            p.setPen(QPen(grid, 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QPen(text, 1))
            p.drawText(QPointF(6, y + 4), f"{value:g}")
        p.setPen(QPen(text, 1))
        p.drawText(QPointF(r.left(), r.bottom() + 16), f"-{fmt_age(self.span)}")
        p.drawText(QPointF(r.center().x() - 14, r.bottom() + 16), f"-{fmt_age(self.span / 2)}")
        p.drawText(QPointF(r.right() - 34, r.bottom() + 16), "şimdi")

        title = f"{self.title} ({self.unit})" if self.unit else self.title
        # Başlık ve gösterge, grafiğin sol kenarıyla hizalı başlar (y ekseni etiketleri solda kalır)
        p.drawText(QPointF(r.left(), 14), title)
        x_legend = r.left() + p.fontMetrics().horizontalAdvance(title) + 24
        if not any(pts for _n, pts in self.series):
            p.drawText(QPointF(r.center().x() - 40, r.center().y()), "veri bekleniyor")
            return
        for i, (name, pts) in enumerate(self.series):
            color = QColor(PALETTE[i % len(PALETTE)])
            pixels = [QPointF(r.left() + (age + self.span) / self.span * r.width(),
                              r.bottom() - (value - lo) / (hi - lo) * r.height())
                      for age, value in pts]
            p.setPen(QPen(color, 2))
            for a, b in zip(pixels, pixels[1:]):
                p.drawLine(a, b)
            if pts:
                label = f"{name} {pts[-1][1]:.0f}"
                p.setBrush(color)
                p.drawRect(QRectF(x_legend, 6, 8, 8))
                p.setPen(QPen(text, 1))
                p.drawText(QPointF(x_legend + 12, 14), label)
                x_legend += 12 + p.fontMetrics().horizontalAdvance(label) + 18


class HistoryPage(QWidget):
    WINDOWS = [("1 dakika", 60), ("5 dakika", 300), ("15 dakika", 900),
               ("30 dakika", 1800), ("60 dakika", 3600)]
    # (anahtar, başlık, birim, y_min, y_max)
    PLOTS = [("temp", "Sıcaklık", "°C", None, None), ("clock", "Saat", "MHz", 0, None),
             ("power", "Güç", "W", 0, None), ("usage", "Kullanım", "%", 0, 100),
             ("fan", "Fan", "RPM", 0, None)]
    DEFAULT_VISIBLE = ["temp", "clock", "power", "usage"]

    def __init__(self, prefs=None):
        super().__init__()
        self.prefs = prefs
        self.store = HistoryStore(3600)
        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        self.window_box = QComboBox()
        self.window_box.addItems([name for name, _s in self.WINDOWS])
        saved = prefs.get("history_window", 0) if prefs else 0
        self.window_box.setCurrentIndex(saved if isinstance(saved, int)
                                        and 0 <= saved < len(self.WINDOWS) else 0)
        self.window_box.currentIndexChanged.connect(self._window_changed)
        top.addWidget(self.window_box)
        visible = prefs.get("history_plots", self.DEFAULT_VISIBLE) if prefs else self.DEFAULT_VISIBLE
        self.checks, self.plots = {}, {}
        for key, title, _unit, _lo, _hi in self.PLOTS:
            check = QCheckBox(title)
            check.setChecked(key in visible)
            check.toggled.connect(self._plots_changed)
            self.checks[key] = check
            top.addWidget(check)
        top.addStretch(1)
        self.export_btn = QPushButton("CSV…")
        self.export_btn.clicked.connect(self.export_csv)
        top.addWidget(self.export_btn)
        outer.addLayout(top)
        for key, title, unit, lo, hi in self.PLOTS:
            self.plots[key] = LinePlot(title, unit, y_min=lo, y_max=hi)
            outer.addWidget(self.plots[key], 1)
        self._apply_visibility()

    def current_window(self):
        return self.WINDOWS[max(0, self.window_box.currentIndex())][1]

    def _window_changed(self, index):
        if self.prefs:
            self.prefs.set("history_window", index)
        self.refresh()

    def _plots_changed(self, _checked=False):
        if self.prefs:
            self.prefs.set("history_plots", [k for k, c in self.checks.items() if c.isChecked()])
        self._apply_visibility()

    def _apply_visibility(self):
        for key, plot in self.plots.items():
            plot.setVisible(self.checks[key].isChecked())

    def clear(self):
        self.store.clear()
        self.refresh()

    def add_stats(self, stats):
        self.store.add(sample_from_stats(stats))
        if self.isVisible():
            self.refresh()

    def refresh(self):
        window = self.current_window()
        now = time.time()
        get = lambda key: self.store.series(key, window, now)
        temps = [(temp_label(k.split(":", 1)[1]), get(k))
                 for k in self.store.keys() if k.startswith("temp:")]
        self.plots["temp"].set_data(temps, window)
        self.plots["clock"].set_data([("GPU", get("clock:gpu")), ("VRAM", get("clock:vram"))], window)
        self.plots["power"].set_data([("Güç", get("power"))], window)
        self.plots["usage"].set_data([("GPU", get("busy")), ("VRAM", get("vram_pct"))], window)
        self.plots["fan"].set_data([("Fan", get("fan_rpm"))], window)

    def export_csv(self):
        if not self.store.samples:
            QMessageBox.information(self, "CSV", "Henüz kaydedilmiş veri yok.")
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Geçmişi CSV olarak kaydet", "lact-gecmis.csv", "CSV (*.csv)")
        if not path:
            return
        text = history_to_csv(self.store)  # tüm geçmiş, yalnızca görünen pencere değil
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        QMessageBox.information(self, "CSV", f"{len(self.store.samples)} satır kaydedildi:\n{path}")


# --------------------------------------------------------------------------- #
class StatesPage(QWidget):
    """AMD güç durumları (açık/kapalı) ve güç profili modu."""

    def __init__(self):
        super().__init__()
        self._config = {}
        self._states = {"core": [], "vram": []}
        self._checks = {"core": [], "vram": []}
        self._idx = {"core": [], "vram": []}
        self._mode_ids = []
        self._mode_names = []
        self._cells = []
        self._cell_widgets = []
        outer = QVBoxLayout(self)
        self.note = QLabel()
        outer.addWidget(self.note)
        self.boxes, self.box_layouts = {}, {}
        for key, _cfg, title in POWER_KINDS:
            box = QGroupBox(title)
            self.box_layouts[key] = QVBoxLayout(box)
            self.boxes[key] = box
            outer.addWidget(box)

        mode_box = QGroupBox("Güç profili modu")
        mode_lay = QVBoxLayout(mode_box)
        self.mode_check = QCheckBox("Güç profili modunu elle seç")
        self.mode_combo = QComboBox()
        self.mode_info = QLabel(DASH)
        mode_lay.addWidget(self.mode_check)
        mode_lay.addWidget(self.mode_combo)
        mode_lay.addWidget(self.mode_info)
        self.custom_box = QGroupBox("Özel mod değerleri")
        self.custom_grid = QGridLayout(self.custom_box)
        mode_lay.addWidget(self.custom_box)
        outer.addWidget(mode_box)
        self.mode_check.toggled.connect(self._sync_mode)
        self.mode_combo.currentIndexChanged.connect(self._sync_mode)


    def _sync_mode(self, *_args):
        on = self.mode_check.isChecked()
        self.mode_combo.setEnabled(on)
        idx = self.mode_combo.currentIndex()
        custom = 0 <= idx < len(self._mode_names) and self._mode_names[idx] == "CUSTOM"
        self.custom_box.setVisible(bool(on and custom))

    def load(self, states_data, modes_data, config):
        self._config = copy.deepcopy(config)
        self._states = parse_states(states_data)
        for key, _cfg, _title in POWER_KINDS:
            lay = self.box_layouts[key]
            for widget in self._checks[key]:
                lay.removeWidget(widget)
                widget.deleteLater()
            self._checks[key], self._idx[key] = [], []
            for idx, mhz, enabled in self._states[key]:
                check = QCheckBox(f"#{idx} — {mhz} MHz")
                check.setChecked(enabled)
                lay.addWidget(check)
                self._checks[key].append(check)
                self._idx[key].append(idx)
            self.boxes[key].setVisible(bool(self._states[key]))

        modes, value_names, active = parse_modes(modes_data)
        self._mode_ids = [i for i, _n, _c in modes]
        self._mode_names = [n for _i, n, _c in modes]
        self.mode_combo.clear()
        for idx, name, _comps in modes:
            self.mode_combo.addItem(f"{idx} — {name}" + ("  (etkin)" if idx == active else ""))
        cfg_idx = config.get("power_profile_mode_index")
        target = cfg_idx if cfg_idx is not None else active
        if target in self._mode_ids:
            self.mode_combo.setCurrentIndex(self._mode_ids.index(target))
        self.mode_check.setChecked(cfg_idx is not None)
        self.mode_info.setText(
            f"Sürücüdeki etkin mod: {DASH if active is None else active}" if modes
            else "Bu kartta güç profili modları yok")
        self._build_custom(modes, value_names, config)
        level = config.get("performance_level") or "auto"
        self.note.setText(
            f"Güç profili modu 'manual' performans seviyesi gerektirir (şu an: {level}).")
        self._sync_mode()

    def _build_custom(self, modes, value_names, config):
        for widget in self._cell_widgets:
            self.custom_grid.removeWidget(widget)
            widget.deleteLater()
        self._cell_widgets, self._cells = [], []
        custom = next(((n, c) for _i, n, c in modes if n == "CUSTOM"), None)
        if custom is None:
            return
        comps = custom[1]
        saved = config.get("custom_power_profile_mode_hueristics")  # (sic) LACT'taki yazım
        columns = []
        for ci, (clock, values) in enumerate(comps):
            if isinstance(saved, list) and ci < len(saved) and isinstance(saved[ci], list):
                values = saved[ci]
            columns.append((clock, values))
        for ci, (clock, _values) in enumerate(columns):
            head = QLabel(str(clock))
            self.custom_grid.addWidget(head, 0, ci + 1)
            self._cell_widgets.append(head)
        rows = max((len(v) for _c, v in columns), default=0)
        for ri in range(rows):
            name = QLabel(value_names[ri] if ri < len(value_names) else f"#{ri}")
            self.custom_grid.addWidget(name, ri + 1, 0)
            self._cell_widgets.append(name)
            row_cells = []
            for ci, (_clock, values) in enumerate(columns):
                cell = QLineEdit("" if ri >= len(values) or values[ri] is None else str(values[ri]))
                self.custom_grid.addWidget(cell, ri + 1, ci + 1)
                self._cell_widgets.append(cell)
                row_cells.append(cell)
            self._cells.append(row_cells)

    def collect(self):
        enabled = {key: {i for i, c in zip(self._idx[key], self._checks[key]) if c.isChecked()}
                   for key, _cfg, _title in POWER_KINDS}
        changes = {"power_states": states_to_config(
            self._config.get("power_states"), self._states, enabled)}
        if self.mode_check.isChecked() and self._mode_ids:
            index = self.mode_combo.currentIndex()
            changes["power_profile_mode_index"] = self._mode_ids[index]
            if self._mode_names[index] == "CUSTOM":
                cells = [[cell.text() for cell in row] for row in self._cells]
                changes["custom_power_profile_mode_hueristics"] = heuristics_from_cells(cells)
        else:
            changes["power_profile_mode_index"] = None
        return changes


# --------------------------------------------------------------------------- #
class ProfilesPage(QWidget):
    """Ayar profilleri: oluştur, sil, sırala, etkinleştir ve kurallarla otomatik geçiş."""

    DEFAULT_LABEL = "Varsayılan (profil yok)"

    def __init__(self, client, on_changed, apply_config=None, get_context=None):
        super().__init__()
        self.client = client
        self._apply_config = apply_config  # apply_config(değişiklikler) -> korundu mu
        self._context = get_context  # get_context() -> (gid, [gpu idleri], lact sürümü)
        self._on_changed = on_changed  # on_changed(etkin_profil, yapılandırma_değişti_mi)
        self.profiles, self.current, self.auto = [], None, False
        self._names = [None]
        outer = QVBoxLayout(self)
        self.status = QLabel(DASH)
        outer.addWidget(self.status)

        mid = QHBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._row_changed)
        mid.addWidget(self.list, 1)
        side = QVBoxLayout()
        self.activate_btn = QPushButton("Etkinleştir")
        self.new_btn = QPushButton("Yeni profil…")
        self.delete_btn = QPushButton("Sil")
        self.up_btn = QPushButton("Yukarı")
        self.down_btn = QPushButton("Aşağı")
        self.hold_btn = QPushButton("Sabitle")
        self.release_btn = QPushButton("Sabitlemeyi kaldır")
        self.export_btn = QPushButton("Dışa aktar…")
        self.import_btn = QPushButton("İçe aktar…")
        for btn, slot in ((self.activate_btn, self.activate), (self.new_btn, self.new_profile),
                          (self.delete_btn, self.delete), (self.up_btn, lambda: self.move(-1)),
                          (self.down_btn, lambda: self.move(1)), (self.hold_btn, self.hold),
                          (self.release_btn, self.release), (self.export_btn, self.export_profile),
                          (self.import_btn, self.import_profile)):
            btn.clicked.connect(slot)
            side.addWidget(btn)
        side.addStretch(1)
        mid.addLayout(side)
        outer.addLayout(mid, 1)

        self.auto_check = QCheckBox("Kurallara göre otomatik profil değiştir")
        self.auto_check.toggled.connect(self.toggle_auto)
        outer.addWidget(self.auto_check)

        rule_box = QGroupBox("Etkinleştirme kuralı (JSON)")
        rule_lay = QVBoxLayout(rule_box)
        self.rule_edit = QPlainTextEdit()
        self.rule_edit.setMaximumHeight(120)
        rule_lay.addWidget(self.rule_edit)
        tmpl = QHBoxLayout()
        for name in RULE_TEMPLATES:
            btn = QPushButton(f"Şablon: {name}")
            btn.clicked.connect(lambda _checked=False, n=name: self.fill_template(n))
            tmpl.addWidget(btn)
        tmpl.addStretch(1)
        self.test_btn = QPushButton("Kuralı dene")
        self.save_btn = QPushButton("Kuralı kaydet")
        self.test_btn.clicked.connect(self.test_rule)
        self.save_btn.clicked.connect(self.save_rule)
        tmpl.addWidget(self.test_btn)
        tmpl.addWidget(self.save_btn)
        rule_lay.addLayout(tmpl)
        self.rule_result = QLabel("")
        rule_lay.addWidget(self.rule_result)
        outer.addWidget(rule_box)

    # -- yardımcılar --
    def selected(self):
        row = self.list.currentRow()
        return row if 0 <= row < len(self._names) else None

    def _run(self, fn, *args):
        try:
            with waiting():
                return True, fn(*args)
        except LactError as exc:
            QMessageBox.warning(self, "Profil işlemi başarısız", str(exc))
            return False, None

    def reload(self, select=None):
        try:
            data = self.client.list_profiles()
        except LactError as exc:
            self.status.setText(f"Profiller okunamadı: {exc}")
            return
        self.profiles, self.current, self.auto = parse_profiles(data)
        rules = dict(self.profiles)
        self._names = [None] + [name for name, _rule in self.profiles]
        self.list.clear()
        for name in self._names:
            label = self.DEFAULT_LABEL if name is None else name
            mark = "  ●" if name == self.current else ""
            extra = "" if name is None else f"   — {rule_summary(rules.get(name))}"
            self.list.addItem(f"{label}{mark}{extra}")
        self.auto_check.blockSignals(True)
        self.auto_check.setChecked(self.auto)
        self.auto_check.blockSignals(False)
        want = select if select is not None else self.current
        self.list.setCurrentRow(self._names.index(want) if want in self._names else 0)
        self.status.setText("Etkin profil: {}{}".format(
            self.current or "Varsayılan", "  (otomatik geçiş açık)" if self.auto else ""))
        self._on_changed(self.current, False)

    def _after(self, select=None):
        self.reload(select)
        self._on_changed(self.current, True)

    def _row_changed(self, row):
        name = self._names[row] if 0 <= row < len(self._names) else None
        rule = dict(self.profiles).get(name) if name is not None else None
        self.rule_edit.setPlainText(json.dumps(rule, indent=2, ensure_ascii=False) if rule else "")
        for widget in (self.rule_edit, self.save_btn, self.test_btn, self.delete_btn,
                       self.up_btn, self.down_btn, self.hold_btn):
            widget.setEnabled(name is not None)
        self.rule_result.setText("")

    # -- eylemler --
    def activate(self):
        row = self.selected()
        if row is None:
            return
        ok, _ = self._run(self.client.set_profile, self._names[row], False)
        if ok:
            self._after()

    def toggle_auto(self, on):
        ok, _ = self._run(self.client.set_profile, self.current, bool(on))
        if ok:
            self._after()

    def new_profile(self):
        name, accepted = QInputDialog.getText(self, "Yeni profil", "Profil adı:")
        name = (name or "").strip()
        if not accepted or not name:
            return
        row = self.selected()
        base = self._names[row] if row is not None else None  # None -> varsayılanın kopyası
        ok, _ = self._run(self.client.create_profile, name, base)
        if ok:
            self.reload(select=name)

    def delete(self):
        row = self.selected()
        if row is None or self._names[row] is None:
            return
        name = self._names[row]
        if not confirm(self, "Profili sil", f"'{name}' profili silinsin mi?"):
            return
        ok, _ = self._run(self.client.delete_profile, name)
        if ok:
            self._after()

    def move(self, delta):
        row = self.selected()
        if row is None or self._names[row] is None:
            return
        new_pos = row - 1 + delta  # varsayılan girdiyi say(ma)
        if not 0 <= new_pos < len(self.profiles):
            return
        ok, _ = self._run(self.client.move_profile, self._names[row], new_pos)
        if ok:
            self.reload(select=self._names[row])

    def hold(self):
        row = self.selected()
        if row is not None and self._names[row] is not None:
            self._run(self.client.hold_profile, self._names[row])
            self.reload()

    def release(self):
        self._run(self.client.release_profile)
        self.reload()

    # -- dışa / içe aktarma --
    def export_profile(self):
        row = self.selected()
        if row is None or self._context is None:
            return
        name = self._names[row]
        gid, device_ids, version = self._context()
        rule = dict(self.profiles).get(name) if name is not None else None
        gpus = {}
        try:
            if name == self.current:  # etkin profil: doğrulanmış okuma yolu
                with waiting():
                    gpus = {dev: self.client.get_config(dev) for dev in device_ids}
            else:
                with waiting():
                    rule2, gpus = extract_profile(self.client.get_profile(name))
                rule = rule2 or rule
        except LactError as exc:
            if name == self.current:
                QMessageBox.warning(self, "Dışa aktar", str(exc))
                return
            gpus = {}
        if not gpus:
            QMessageBox.warning(
                self, "Dışa aktar",
                "Bu profilin içeriği daemon'dan okunamadı.\nProfili önce etkinleştirip tekrar dene.")
            return
        default_name = f"{name or 'varsayilan'}.json"
        path, _filter = QFileDialog.getSaveFileName(
            self, "Profili dışa aktar", default_name, "LACT profili (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(dumps(build_export(name, rule, gpus, version)))
        QMessageBox.information(self, "Dışa aktar", f"Profil kaydedildi:\n{path}")

    def import_profile(self):
        if self._context is None or self._apply_config is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, "Profili içe aktar", "", "LACT profili (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as handle:
                export = parse_export(handle.read())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "İçe aktar", str(exc))
            return
        gid, device_ids, _version = self._context()
        config, note = pick_config(export, gid, device_ids)
        if config is None:
            QMessageBox.warning(self, "İçe aktar", "Dosyada seçili ekran kartı için ayar yok.")
            return
        if note and not confirm(self, "Farklı ekran kartı", note + "\n\nYine de devam edilsin mi?"):
            return
        existing = [n for n, _r in self.profiles]
        name, accepted = QInputDialog.getText(
            self, "Profili içe aktar", "Profil adı:", text=unique_name(export["name"], existing))
        name = (name or "").strip()
        if not accepted or not name:
            return
        if name in existing:
            QMessageBox.warning(self, "İçe aktar", f"'{name}' adında bir profil zaten var.")
            return
        # Daemon'da profil içeriğini doğrudan yazan komut yok: profili oluşturup etkinleştirir,
        # ayarı olağan onay penceresiyle uygular, kuralı kaydeder ve istenirse önceki profile döneriz.
        previous, previous_auto = self.current, self.auto
        ok, _ = self._run(self.client.create_profile, name, None)
        if not ok:
            return
        ok, _ = self._run(self.client.set_profile, name, False)
        if not ok:
            self.reload(select=name)
            return
        self._after(select=name)  # ana pencere yeni profilin yapılandırmasını okur
        kept = self._apply_config(config_to_changes(self._current_config(), config))
        if kept is not True:
            QMessageBox.information(self, "İçe aktar",
                                    f"'{name}' profili oluşturuldu ama ayarlar uygulanmadı.")
            return
        if export["rule"]:
            self._run(self.client.set_profile_rule, name, export["rule"])
        self.reload(select=name)
        if previous != name and confirm(
                self, "İçe aktar",
                f"'{name}' profili içe aktarıldı ve etkinleştirildi.\n"
                f"Önceki profile ('{previous or 'Varsayılan'}') dönülsün mü?"):
            ok, _ = self._run(self.client.set_profile, previous, previous_auto)
            if ok:
                self._after()

    def _current_config(self):
        gid, _ids, _v = self._context()
        try:
            return self.client.get_config(gid)
        except LactError:
            return {}

    def fill_template(self, name):
        self.rule_edit.setPlainText(json.dumps(RULE_TEMPLATES[name], indent=2, ensure_ascii=False))

    def _rule(self):
        try:
            return True, parse_rule_text(self.rule_edit.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "Geçersiz kural", str(exc))
            return False, None

    def save_rule(self):
        row = self.selected()
        valid, rule = self._rule()
        if row is None or self._names[row] is None or not valid:
            return
        ok, _ = self._run(self.client.set_profile_rule, self._names[row], rule)
        if ok:
            self.reload(select=self._names[row])

    def test_rule(self):
        valid, rule = self._rule()
        if not valid or rule is None:
            return
        ok, result = self._run(self.client.evaluate_profile_rule, rule)
        if ok:
            self.rule_result.setText(f"Kural şu an {'eşleşiyor' if result else 'eşleşmiyor'}")


# --------------------------------------------------------------------------- #
class ProcessesPage(QWidget):
    HEADERS = ["PID", "Süreç", "VRAM", "Tür", "Kullanım", "Argümanlar"]

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        self.status = QLabel(DASH)
        outer.addWidget(self.status)
        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.table, 1)

    def load(self, data):
        rows = process_rows(data)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            util = ", ".join(f"{k}: {v}" for k, v in row["util"].items()) or DASH
            cells = [row["pid"], row["name"], fmt_mem(row["memory"]),
                     ", ".join(row["types"]) or DASH, util, row["args"]]
            for c, text in enumerate(cells):
                self.table.setItem(r, c, QTableWidgetItem(str(text)))
        self.table.resizeColumnsToContents()
        self.status.setText(f"GPU kullanan süreç sayısı: {len(rows)}")


# --------------------------------------------------------------------------- #
class SystemActions:
    """Menüden çağrılan sistem işlemleri: Overdrive, tanılama, GPU bağlantısı, sıfırlama."""

    def __init__(self, parent, client, get_gid, on_changed):
        self.parent, self.client, self.get_gid, self._on_changed = parent, client, get_gid, on_changed

    def _call(self, title, fn, *args):
        try:
            with waiting():
                return True, fn(*args)
        except LactError as exc:
            QMessageBox.warning(self.parent, title, str(exc))
            return False, None

    def overdrive(self, enable):
        text = ("Overclocking açılırken LACT çekirdek parametresini yazar ve initramfs'i yeniler "
                "(birkaç dakika sürebilir).\nEtkisi için yeniden başlatma gerekir. Devam edilsin mi?"
                if enable else
                "Overclocking devre dışı bırakılacak. Etkisi için yeniden başlatma gerekir. "
                "Devam edilsin mi?")
        if not confirm(self.parent, "Overclocking", text):
            return
        ok, _ = self._call("Overclocking", self.client.enable_overdrive if enable
                           else self.client.disable_overdrive)
        if ok:
            QMessageBox.information(self.parent, "Overclocking", "Tamamlandı. Yeniden başlatma gerekli.")
            self._on_changed()

    def snapshot(self):
        ok, result = self._call("Snapshot", self.client.generate_snapshot)
        if ok:
            QMessageBox.information(self.parent, "Snapshot oluşturuldu", str(result or "Tamamlandı."))

    def vbios(self):
        gid = self.get_gid()
        if not gid:
            return
        ok, data = self._call("VBIOS", self.client.vbios_dump, gid)
        if not ok:
            return
        try:
            blob = vbios_to_bytes(data)
        except ValueError as exc:
            QMessageBox.warning(self.parent, "VBIOS", str(exc))
            return
        path, _filter = QFileDialog.getSaveFileName(self.parent, "VBIOS'u kaydet", "vbios.rom")
        if path:
            with open(path, "wb") as handle:
                handle.write(blob)
            QMessageBox.information(self.parent, "VBIOS", f"{len(blob)} bayt kaydedildi:\n{path}")

    def attach(self, attach):
        gid = self.get_gid()
        if not gid:
            return
        if not attach and not confirm(
                self.parent, "GPU'yu ayır",
                "GPU sürücüden ayrılacak (sanal makineye aktarım için); ekran gidebilir. "
                "Devam edilsin mi?"):
            return
        ok, _ = self._call("GPU bağlantısı", self.client.reattach_gpu if attach
                           else self.client.detach_gpu, gid)
        if ok:
            self._on_changed()

    def reset(self):
        if not confirm(self.parent, "Ayarları sıfırla",
                       "Tüm LACT ayarları ve profiller silinebilir. Devam edilsin mi?"):
            return
        ok, _ = self._call("Sıfırlama", self.client.reset_config)
        if ok:
            self._on_changed()


def ask_service_action(parent, state):
    """lactd servisi için diyalog: 'enable' | 'start' | None."""
    active, enabled = state.get("active"), state.get("enabled")
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    if active and not enabled:
        box.setWindowTitle("LACT servisi açılışta etkin değil")
        box.setText("'lactd' servisi çalışıyor ama bilgisayar açılınca otomatik başlamıyor.")
        box.setInformativeText("Etkinleştirirsen kaydettiğin ayarlar her açılışta uygulanır.\n"
                               "Yönetici parolası istenecek.")
    else:
        box.setWindowTitle("LACT servisi çalışmıyor")
        box.setText("GPU ayarlarını uygulayan 'lactd' servisi çalışmıyor.")
        box.setInformativeText("Servisi başlatayım mı? Açılışta da etkinleştirirsen kaydettiğin "
                               "ayarlar her açılışta otomatik uygulanır.\nYönetici parolası istenecek.")
    enable_btn = start_btn = None
    if not enabled:
        enable_btn = box.addButton(
            "Açılışta etkinleştir" if active else "Başlat ve açılışta etkinleştir",
            QMessageBox.ButtonRole.AcceptRole)
    if not active:
        start_btn = box.addButton("Servisi başlat" if enabled else "Yalnızca şimdi başlat",
                                  QMessageBox.ButtonRole.AcceptRole if enabled
                                  else QMessageBox.ButtonRole.ActionRole)
    box.addButton("Vazgeç", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(enable_btn or start_btn)
    box.exec()
    clicked = box.clickedButton()
    if enable_btn is not None and clicked is enable_btn:
        return "enable"
    if start_btn is not None and clicked is start_btn:
        return "start"
    return None


# --------------------------------------------------------------------------- #
class VulkanDialog(QDialog):
    """device_api_info içindeki Vulkan özellik ve eklenti listeleri."""

    def __init__(self, parent, api):
        super().__init__(parent)
        self.setWindowTitle("Vulkan ayrıntıları")
        self.resize(560, 640)
        inst = (api.get("vulkan_instances") or [{}])[0]
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{inst.get('device_name', DASH)} — Vulkan {inst.get('api_version', DASH)}"))
        tabs = QTabWidget()
        for title, mapping in (("Özellikler", inst.get("features") or {}),
                               ("Eklentiler", inst.get("extensions") or {})):
            enabled = sum(1 for on in mapping.values() if on)
            tabs.addTab(self._list_tab(mapping), f"{title} ({enabled}/{len(mapping)})")
        lay.addWidget(tabs)

    @staticmethod
    def _list_tab(mapping):
        page = QWidget()
        lay = QVBoxLayout(page)
        flt = QLineEdit()
        flt.setPlaceholderText("Filtrele…")
        lst = QListWidget()
        for name, on in sorted(mapping.items()):
            lst.addItem(f"{'✓' if on else '✗'}  {name}")

        def apply(text):
            needle = text.lower()
            for i in range(lst.count()):
                item = lst.item(i)
                item.setHidden(needle not in item.text().lower())

        flt.textChanged.connect(apply)
        lay.addWidget(flt)
        lay.addWidget(lst)
        return page
