#!/usr/bin/env python3
"""LACT için Qt6 (PySide6) arayüzü — aşama 2.

Sekmeler: Bilgi, İzleme, Güç, Hız aşırtma, Fan.
Donanıma dokunan her şeyi lactd yapar; bu program yalnızca daemon'un JSON
soketine bağlanan bir istemcidir. Yapılandırma okunur, değiştirilir ve
bütünüyle geri yazılır, bu yüzden bilmediğimiz anahtarlar korunur.
"""
import argparse
import copy
import math
import sys

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from curve import DEFAULT_CURVE, CurveModel
from lact_client import DEFAULT_SOCKET, LactClient, LactError, with_changes

LEVELS = ["auto", "low", "high", "manual"]
POLL_MS = 1000
DASH = "—"

FAN_MODES = ["Otomatik (sürücü)", "Statik hız", "Özel eğri"]
DEFAULT_FAN = {
    "mode": "curve", "static_speed": 1.0, "temperature_key": "edge",
    "interval_ms": 500, "curve": dict(DEFAULT_CURVE),
    "spindown_delay_ms": 5000, "change_threshold": 2,
}
# (config anahtarı, device_stats'taki pmfw_info anahtarı, başlık, birim)
PMFW_ROWS = [
    ("acoustic_limit", "acoustic_limit", "Akustik limit", " RPM"),
    ("acoustic_target", "acoustic_target", "Akustik hedef", " RPM"),
    ("target_temperature", "target_temp", "Hedef sıcaklık", " °C"),
    ("minimum_pwm", "minimum_pwm", "Asgari fan hızı", " %"),
    ("zero_rpm_threshold", "zero_rpm_temperature", "Zero RPM sıcaklığı", " °C"),
]


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


def fmt_state(value):
    """Güç durumu: {'Index': 1} -> '#1', None -> '—', metin -> olduğu gibi."""
    if isinstance(value, dict) and "Index" in value:
        return f"#{value['Index']}"
    return DASH if value is None else str(value)


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


def rdna_table(clocks):
    """device_clocks_info yanıtından RDNA saat tablosunu çıkarır (yoksa None)."""
    table = dig(clocks, "table", default={})
    if table.get("type") == "amd" and dig(table, "value", "kind") == "rdna":
        return dig(table, "value", "data", default=None)
    return None


# --------------------------------------------------------------------------- #
class OptionalValue(QWidget):
    """Onay kutusu + kaydırıcı + sayı kutusu. İşaretsizse ayar yapılandırmadan silinir."""

    def __init__(self, suffix=""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.check = QCheckBox("Özel değer")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.spin = QSpinBox()
        self.spin.setSuffix(suffix)
        lay.addWidget(self.check)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.slider.valueChanged.connect(self.spin.setValue)
        self.spin.valueChanged.connect(self.slider.setValue)
        self.check.toggled.connect(self._sync)
        self._sync(False)

    def _sync(self, on):
        self.slider.setEnabled(bool(on))
        self.spin.setEnabled(bool(on))

    def configure(self, lo, hi, value, default):
        """value: yapılandırmadaki değer (None = ayarlı değil); default: işaretsizken gösterilen."""
        lo, hi = int(lo), int(hi)
        self.slider.setRange(lo, hi)
        self.spin.setRange(lo, hi)
        shown = default if value is None else value
        self.spin.setValue(int(min(max(shown, lo), hi)))
        self.check.setChecked(value is not None)
        self._sync(value is not None)

    def value(self):
        return self.spin.value() if self.check.isChecked() else None


# --------------------------------------------------------------------------- #
class InfoPage(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

    def show_info(self, info, system):
        drm = dig(info, "drm_info", default={})
        pci = dig(info, "pci_info", default={})
        link = dig(info, "link_info", default={})
        vk = (info.get("vulkan_instances") or [{}])[0]
        cl = (info.get("opencl_instances") or [{}])[0]
        rebar = dig(drm, "memory_info", "resizeable_bar")
        vram_vendor = str(drm.get("vram_vendor", DASH)).capitalize()

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.addWidget(group("Ekran kartı", [
            ("Model", drm.get("device_name", DASH)),
            ("Kart üreticisi", dig(pci, "subsystem_pci_info", "vendor", default=DASH)),
            ("PCI kimliği", "{}:{}".format(
                dig(pci, "device_pci_info", "vendor_id", default="?"),
                dig(pci, "device_pci_info", "model_id", default="?"))),
            ("Sürücü", info.get("driver", DASH)),
            ("VBIOS", info.get("vbios_version", DASH)),
            ("Mimari", "{} ({})".format(drm.get("asic_name", DASH),
                                        drm.get("chip_class", DASH))),
            ("Hesaplama birimi", drm.get("compute_units", DASH)),
        ]))
        lay.addWidget(group("Bellek ve veri yolu", [
            ("VRAM", "{} ({})".format(drm.get("vram_type", DASH), vram_vendor)),
            ("Bellek yolu", "{} bit".format(drm.get("vram_bit_width", DASH))),
            ("Azami bant genişliği", "{} GB/s".format(drm.get("vram_max_bw", DASH))),
            ("Resizable BAR", DASH if rebar is None else ("Açık" if rebar else "Kapalı")),
            ("PCIe bağlantısı", "{} x{}  (azami {} x{})".format(
                link.get("current_speed", DASH), link.get("current_width", DASH),
                link.get("max_speed", DASH), link.get("max_width", DASH))),
        ]))
        if vk:
            drv = vk.get("driver") or {}
            lay.addWidget(group("Vulkan", [
                ("Aygıt", vk.get("device_name", DASH)),
                ("API sürümü", vk.get("api_version", DASH)),
                ("Sürücü", "{} — {}".format(drv.get("name", DASH), drv.get("info", DASH))),
            ]))
        if cl:
            lay.addWidget(group("OpenCL", [
                ("Platform", cl.get("platform_name", DASH)),
                ("Sürüm", str(cl.get("version", DASH)).strip()),
                ("Genel bellek", gib(cl.get("global_memory"))),
            ]))
        od = system.get("amdgpu_overdrive_enabled")
        lay.addWidget(group("Yazılım", [
            ("LACT", "{} ({})".format(system.get("version", DASH), system.get("commit", DASH))),
            ("Dağıtım", system.get("distro", DASH)),
            ("Çekirdek", system.get("kernel_version", DASH)),
            ("Overdrive", DASH if od is None else ("Etkin" if od else "Kapalı")),
        ]))
        lay.addStretch(1)
        self.setWidget(body)  # eski gövdeyi Qt siler


# --------------------------------------------------------------------------- #
class StatsPage(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)

        load_box = QGroupBox("Yük")
        load = QFormLayout(load_box)
        self.busy = QProgressBar()
        self.busy.setRange(0, 100)
        self.vram = QProgressBar()
        self.vram.setRange(0, 100)
        self.power = QProgressBar()
        load.addRow("GPU kullanımı:", self.busy)
        load.addRow("VRAM:", self.vram)
        load.addRow("Güç:", self.power)
        outer.addWidget(load_box)

        misc_box = QGroupBox("Saatler ve durum")
        misc = QFormLayout(misc_box)
        self.gpu_clock = QLabel(DASH)
        self.vram_clock = QLabel(DASH)
        self.voltage = QLabel(DASH)
        self.fan = QLabel(DASH)
        self.level = QLabel(DASH)
        self.states = QLabel(DASH)
        self.throttle = QLabel(DASH)
        for name, widget in (("GPU saati", self.gpu_clock), ("VRAM saati", self.vram_clock),
                             ("GPU voltajı", self.voltage), ("Fan", self.fan),
                             ("Performans seviyesi", self.level),
                             ("Güç durumu", self.states), ("Kısılma", self.throttle)):
            misc.addRow(f"{name}:", widget)
        outer.addWidget(misc_box)

        temp_box = QGroupBox("Sıcaklıklar")
        self.temp_form = QFormLayout(temp_box)
        self.temp_rows = {}
        outer.addWidget(temp_box)
        outer.addStretch(1)

    def update_stats(self, s):
        self.busy.setValue(int(dig(s, "busy_percent", default=0)))

        used, total = dig(s, "vram", "used"), dig(s, "vram", "total")
        if total:
            self.vram.setValue(int(used * 100 / total))
            self.vram.setFormat(f"{gib(used)} / {gib(total)}")

        avg = float(dig(s, "power", "average", default=0.0))
        cap = float(dig(s, "power", "cap_current", default=0.0)
                    or dig(s, "power", "cap_max", default=1.0))
        self.power.setRange(0, max(int(cap), 1))
        self.power.setValue(min(int(avg), max(int(cap), 1)))
        self.power.setFormat(f"{avg:.0f} W / {cap:.0f} W")

        clk = dig(s, "clockspeed", default={})
        self.gpu_clock.setText("{} MHz (hedef {} MHz)".format(
            clk.get("gpu_clockspeed", DASH), clk.get("target_gpu_clockspeed", DASH)))
        self.vram_clock.setText("{} MHz".format(clk.get("vram_clockspeed", DASH)))
        self.voltage.setText("{} mV".format(dig(s, "voltage", "gpu", default=DASH)))

        fan = dig(s, "fan", default={})
        mode = "özel kontrol" if fan.get("control_enabled") else "sürücü kontrolünde"
        rpm, pwm = fan.get("speed_current"), fan.get("pwm_current")
        self.fan.setText("{} ({}){}".format(
            f"{rpm} RPM" if rpm is not None else DASH, mode,
            f", PWM {pwm}" if pwm is not None else ""))

        self.level.setText(str(s.get("performance_level", DASH)))
        ps = s.get("active_power_states") or {}
        self.states.setText("GPU: {}, VRAM: {}".format(
            fmt_state(ps.get("core")), fmt_state(ps.get("memory"))) if ps else DASH)
        thr = s.get("throttle_info") or {}
        self.throttle.setText(", ".join(f"{k}: {v}" for k, v in thr.items()) if thr else "Yok")

        for name, t in (s.get("temps") or {}).items():
            label = self.temp_rows.get(name)
            if label is None:
                label = QLabel()
                self.temp_form.addRow(f"{name}:", label)
                self.temp_rows[name] = label
            cur, crit = t.get("current"), t.get("crit")
            text = f"{cur:.0f} °C" if isinstance(cur, (int, float)) else DASH
            if isinstance(crit, (int, float)):
                text += f"   (kritik {crit:.0f} °C)"
            label.setText(text)


# --------------------------------------------------------------------------- #
class PerfPage(QWidget):
    """Güç sınırı ve performans seviyesi."""

    def __init__(self, on_apply, on_reload):
        super().__init__()
        self._on_apply = on_apply
        outer = QVBoxLayout(self)

        cap_box = QGroupBox("Güç sınırı")
        cap_form = QFormLayout(cap_box)
        self.cap_slider = QSlider(Qt.Orientation.Horizontal)
        self.cap_spin = QSpinBox()
        self.cap_spin.setSuffix(" W")
        row = QHBoxLayout()
        row.addWidget(self.cap_slider, 1)
        row.addWidget(self.cap_spin)
        cap_form.addRow("Sınır:", row)
        self.cap_note = QLabel()
        cap_form.addRow("", self.cap_note)
        self.cap_slider.valueChanged.connect(self.cap_spin.setValue)
        self.cap_spin.valueChanged.connect(self.cap_slider.setValue)
        outer.addWidget(cap_box)

        lvl_box = QGroupBox("Performans seviyesi")
        lvl_form = QFormLayout(lvl_box)
        self.level = QComboBox()
        self.level.addItems(LEVELS)
        lvl_form.addRow("Seviye:", self.level)
        outer.addWidget(lvl_box)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.reload_btn = QPushButton("Geri yükle")
        self.apply_btn = QPushButton("Uygula")
        self.reload_btn.clicked.connect(on_reload)
        self.apply_btn.clicked.connect(self._apply)
        buttons.addWidget(self.reload_btn)
        buttons.addWidget(self.apply_btn)
        outer.addLayout(buttons)
        outer.addStretch(1)

    def load(self, stats, config):
        power = dig(stats, "power", default={})
        lo = int(power.get("cap_min", 0))
        hi = int(power.get("cap_max", 0))
        if hi > lo:
            self.cap_slider.setRange(lo, hi)
            self.cap_spin.setRange(lo, hi)
        current = config.get("power_cap") or power.get("cap_current") or power.get("cap_default") or lo
        self.cap_spin.setValue(int(round(current)))  # kaydırıcıyı da günceller
        default = power.get("cap_default")
        self.cap_note.setText(
            f"Aralık: {lo}–{hi} W" + (f", varsayılan: {default:.0f} W" if default else ""))

        level = config.get("performance_level") or stats.get("performance_level") or "auto"
        if self.level.findText(level) < 0:
            self.level.addItem(level)
        self.level.setCurrentText(level)

    def collect(self):
        return {"power_cap": float(self.cap_spin.value()),
                "performance_level": self.level.currentText()}

    def _apply(self):
        self._on_apply(self.collect())


# --------------------------------------------------------------------------- #
class OcPage(QScrollArea):
    """GPU saat ofseti, voltaj ofseti (düşük voltaj / undervolt) ve azami VRAM saati."""

    def __init__(self, on_apply, on_reload):
        super().__init__()
        self._on_apply = on_apply
        self._config = {}
        self._supported = {}
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        body = QWidget()
        outer = QVBoxLayout(body)
        self.status = QLabel(DASH)
        outer.addWidget(self.status)

        box = QGroupBox("Saat ve voltaj")
        form = QFormLayout(box)
        self.sclk = OptionalValue(" MHz")
        self.volt = OptionalValue(" mV")
        self.mem = OptionalValue(" MHz")
        self.sclk_hint = QLabel()
        self.volt_hint = QLabel()
        self.mem_hint = QLabel()
        form.addRow("GPU saat ofseti:", self.sclk)
        form.addRow("", self.sclk_hint)
        form.addRow("Voltaj ofseti:", self.volt)
        form.addRow("", self.volt_hint)
        form.addRow("Azami VRAM saati:", self.mem)
        form.addRow("", self.mem_hint)
        outer.addWidget(box)

        outer.addWidget(QLabel(
            "Kararsızlık görürsen 'Özel değer' kutularının işaretini kaldırıp Uygula'ya bas;\n"
            "ayarlar varsayılana döner."))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.reload_btn = QPushButton("Geri yükle")
        self.apply_btn = QPushButton("Uygula")
        self.reload_btn.clicked.connect(on_reload)
        self.apply_btn.clicked.connect(self._apply)
        buttons.addWidget(self.reload_btn)
        buttons.addWidget(self.apply_btn)
        outer.addLayout(buttons)
        outer.addStretch(1)
        self.setWidget(body)

    def load(self, config, clocks, ratio, overdrive):
        self._config = copy.deepcopy(config)
        data = rdna_table(clocks)
        rng = dig(data, "od_range", default={}) if data else {}
        offsets = config.get("gpu_clock_offsets") or {}

        specs = (
            ("sclk", self.sclk, self.sclk_hint, rng.get("sclk_offset"),
             offsets.get("0"), 0, "MHz"),
            ("volt", self.volt, self.volt_hint, rng.get("voltage_offset"),
             config.get("voltage_offset"), dig(data, "voltage_offset", default=0), "mV"),
            ("mem", self.mem, self.mem_hint, rng.get("mclk"),
             config.get("max_memory_clock"),
             dig(data, "current_mclk_range", "max", default=0), "MHz"),
        )
        for name, widget, hint, limits, value, default, unit in specs:
            ok = bool(data) and isinstance(limits, dict) and "min" in limits and "max" in limits
            self._supported[name] = ok
            widget.setEnabled(ok)
            if ok:
                widget.configure(limits["min"], limits["max"], value, default)
                hint.setText(f"Aralık: {limits['min']} … {limits['max']} {unit}")
            else:
                widget.configure(0, 0, None, 0)
                hint.setText("Bu kartta desteklenmiyor")
        if ratio and ratio != 1:
            self.mem_hint.setText(self.mem_hint.text() + f"   (efektif hız ≈ ×{ratio:g})")

        if not data:
            self.status.setText("Saat tablosu okunamadı.\nOverdrive kapalı olabilir.")
        else:
            self.status.setText(
                "Şu an etkin:  GPU ofseti {} MHz  •  voltaj ofseti {} mV  •  azami VRAM {} MHz".format(
                    data.get("sclk_offset", DASH), data.get("voltage_offset", DASH),
                    dig(data, "current_mclk_range", "max", default=DASH)))
        if overdrive is False:
            self.status.setText(self.status.text() + "\nUyarı: amdgpu Overdrive kapalı görünüyor.")

    def collect(self):
        changes = {}
        if self._supported.get("sclk"):
            offsets = copy.deepcopy(self._config.get("gpu_clock_offsets") or {})
            value = self.sclk.value()
            if value is None:
                offsets.pop("0", None)
            else:
                offsets["0"] = value
            changes["gpu_clock_offsets"] = offsets or None
        if self._supported.get("volt"):
            changes["voltage_offset"] = self.volt.value()
        if self._supported.get("mem"):
            changes["max_memory_clock"] = self.mem.value()
        return changes

    def _apply(self):
        self._on_apply(self.collect())


# --------------------------------------------------------------------------- #
class CurveEditor(QWidget):
    """Sürükle-bırak fan eğrisi editörü. Veri mantığı curve.CurveModel'dedir."""

    changed = Signal()
    PAD_L, PAD_T, PAD_R, PAD_B = 46, 14, 16, 30

    def __init__(self):
        super().__init__()
        self.model = CurveModel()
        self.selected = None
        self._drag = None
        self.current_temp = None
        self.setMinimumHeight(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_current_temp(self, temp):
        self.current_temp = temp
        self.update()

    def reload(self):
        self.selected = None
        self.update()

    # -- geometri --
    def _plot(self):
        return QRectF(self.PAD_L, self.PAD_T,
                      max(self.width() - self.PAD_L - self.PAD_R, 10),
                      max(self.height() - self.PAD_T - self.PAD_B, 10))

    def _to_px(self, temp, speed):
        r, m = self._plot(), self.model
        x = r.left() + (temp - m.t_min) / max(m.t_max - m.t_min, 1) * r.width()
        return QPointF(x, r.bottom() - speed * r.height())

    def _from_px(self, pos):
        r, m = self._plot(), self.model
        temp = m.t_min + (pos.x() - r.left()) / r.width() * (m.t_max - m.t_min)
        return temp, (r.bottom() - pos.y()) / r.height()

    def _hit(self, pos):
        best, best_d = None, 12.0
        for i, (t, s) in enumerate(self.model.points):
            pt = self._to_px(t, s)
            d = math.hypot(pt.x() - pos.x(), pt.y() - pos.y())
            if d < best_d:
                best, best_d = i, d
        return best

    # -- çizim --
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        text = pal.color(QPalette.ColorRole.Text)
        accent = pal.color(QPalette.ColorRole.Highlight)
        base = pal.color(QPalette.ColorRole.Base)
        r, m = self._plot(), self.model

        p.fillRect(r, base)
        floor_y = self._to_px(m.t_min, m.s_min).y()
        band = QColor(text)
        band.setAlpha(25)
        p.fillRect(QRectF(r.left(), floor_y, r.width(), r.bottom() - floor_y), band)

        grid = QColor(text)
        grid.setAlpha(50)
        p.setPen(QPen(grid, 1))
        temps = list(range((m.t_min + 9) // 10 * 10, m.t_max + 1, 10))
        for t in temps:
            x = self._to_px(t, 0).x()
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        for pct in range(0, 101, 10):
            y = self._to_px(m.t_min, pct / 100).y()
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.setPen(QPen(text, 1))
        for t in temps:
            p.drawText(QPointF(self._to_px(t, 0).x() - 10, r.bottom() + 16), f"{t}°")
        for pct in range(0, 101, 20):
            p.drawText(QPointF(6, self._to_px(m.t_min, pct / 100).y() + 4), f"{pct}%")

        pts = [self._to_px(t, s) for t, s in m.points]
        p.setPen(QPen(accent, 2))
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)
        p.setPen(QPen(accent, 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(r.left(), pts[0].y()), pts[0])
        p.drawLine(pts[-1], QPointF(r.right(), pts[-1].y()))

        if self.current_temp is not None and m.t_min <= self.current_temp <= m.t_max:
            x = self._to_px(self.current_temp, 0).x()
            p.setPen(QPen(text, 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawText(QPointF(x + 4, r.top() + 12),
                       f"şu an {self.current_temp:.0f}° → %{m.speed_at(self.current_temp) * 100:.0f}")

        for i, pt in enumerate(pts):
            p.setPen(QPen(accent, 2))
            p.setBrush(accent if i == self.selected else base)
            p.drawEllipse(pt, 6, 6)
        if self.selected is not None and self.selected < len(pts):
            t, s = m.points[self.selected]
            p.setPen(QPen(text, 1))
            p.drawText(QPointF(pts[self.selected].x() + 10, pts[self.selected].y() - 10),
                       f"{t}° → %{s * 100:.0f}")

    # -- fare ve klavye --
    def mousePressEvent(self, event):
        idx = self._hit(event.position())
        if event.button() == Qt.MouseButton.RightButton:
            if idx is not None and self.model.remove(idx):
                self.selected = None
                self.update()
                self.changed.emit()
        elif event.button() == Qt.MouseButton.LeftButton and idx is not None:
            self.selected = self._drag = idx
            self.update()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        temp, speed = self._from_px(event.position())
        self.model.move(self._drag, temp, speed)
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, _event):
        self._drag = None

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._hit(event.position()) is not None:
            return
        temp, speed = self._from_px(event.position())
        idx = self.model.add(temp, speed)
        if idx is not None:
            self.selected = idx
            self.update()
            self.changed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete and self.selected is not None:
            if self.model.remove(self.selected):
                self.selected = None
                self.update()
                self.changed.emit()
        else:
            super().keyPressEvent(event)


class FanPage(QScrollArea):
    """Fan kontrolü: sürücü (PMFW), statik hız veya özel eğri."""

    def __init__(self, on_apply, on_reload):
        super().__init__()
        self._on_apply = on_apply
        self._settings = dict(DEFAULT_FAN)
        self._pmfw = {}
        self._had_settings = False
        self._zero_initial = False
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        body = QWidget()
        outer = QVBoxLayout(body)

        mode_box = QGroupBox("Fan kontrolü")
        mode_form = QFormLayout(mode_box)
        self.mode = QComboBox()
        self.mode.addItems(FAN_MODES)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.live = QLabel(DASH)
        mode_form.addRow("Mod:", self.mode)
        mode_form.addRow("Şu an:", self.live)
        outer.addWidget(mode_box)

        self.curve_box = QGroupBox("Özel eğri")
        curve_lay = QVBoxLayout(self.curve_box)
        self.curve = CurveEditor()
        curve_lay.addWidget(self.curve)
        curve_lay.addWidget(QLabel(
            "Sürükle: taşı  •  boş yere çift tıkla: nokta ekle  •  sağ tık ya da Delete: sil"))
        form = QFormLayout()
        self.temp_key = QComboBox()
        self.interval = QSpinBox()
        self.interval.setRange(100, 10000)
        self.interval.setSingleStep(50)
        self.interval.setSuffix(" ms")
        self.spindown = QSpinBox()
        self.spindown.setRange(0, 60000)
        self.spindown.setSingleStep(500)
        self.spindown.setSuffix(" ms")
        self.threshold = QSpinBox()
        self.threshold.setRange(0, 20)
        self.threshold.setSuffix(" °C")
        form.addRow("Sıcaklık sensörü:", self.temp_key)
        form.addRow("Güncelleme aralığı:", self.interval)
        form.addRow("Yavaşlama gecikmesi:", self.spindown)
        form.addRow("Değişim eşiği:", self.threshold)
        curve_lay.addLayout(form)
        outer.addWidget(self.curve_box)

        self.static_box = QGroupBox("Statik hız")
        static_form = QFormLayout(self.static_box)
        self.static_slider = QSlider(Qt.Orientation.Horizontal)
        self.static_spin = QSpinBox()
        self.static_spin.setSuffix(" %")
        row = QHBoxLayout()
        row.addWidget(self.static_slider, 1)
        row.addWidget(self.static_spin)
        static_form.addRow("Hız:", row)
        self.static_slider.valueChanged.connect(self.static_spin.setValue)
        self.static_spin.valueChanged.connect(self.static_slider.setValue)
        outer.addWidget(self.static_box)

        self.pmfw_box = QGroupBox("Sürücü (PMFW) ayarları")
        pmfw_form = QFormLayout(self.pmfw_box)
        self.zero_rpm = QCheckBox("Zero RPM (düşük sıcaklıkta fan durur)")
        pmfw_form.addRow(self.zero_rpm)
        self.pmfw_widgets = {}
        for key, _stat, title, unit in PMFW_ROWS:
            widget = OptionalValue(unit)
            self.pmfw_widgets[key] = widget
            pmfw_form.addRow(f"{title}:", widget)
        pmfw_form.addRow(QLabel(
            "Özel eğri ya da statik hız seçiliyse bu ayarlar uygulanmaz."))
        outer.addWidget(self.pmfw_box)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.reload_btn = QPushButton("Geri yükle")
        self.apply_btn = QPushButton("Uygula")
        self.reload_btn.clicked.connect(on_reload)
        self.apply_btn.clicked.connect(self._apply)
        buttons.addWidget(self.reload_btn)
        buttons.addWidget(self.apply_btn)
        outer.addLayout(buttons)
        outer.addStretch(1)
        self.setWidget(body)

    def _mode_changed(self, index):
        self.pmfw_box.setEnabled(index == 0)
        self.static_box.setEnabled(index == 1)
        self.curve_box.setEnabled(index == 2)

    def load(self, stats, config):
        fan = dig(stats, "fan", default={})
        enabled = bool(config.get("fan_control_enabled"))
        saved = copy.deepcopy(config.get("fan_control_settings") or {})
        self._had_settings = bool(saved)
        self._settings = {**copy.deepcopy(DEFAULT_FAN), **saved}

        # Sürücünün izin verdiği hız aralığı (pwm_min/255 ... pwm_max/255)
        pwm_min, pwm_max = fan.get("pwm_min"), fan.get("pwm_max")
        s_min = math.ceil(pwm_min / 255 * 100) / 100 if pwm_min else 0.0
        s_max = min(1.0, round(pwm_max / 255, 2)) if pwm_max else 1.0
        t_lo, t_hi = fan.get("temperature_range") or (25, 100)

        self.curve.model.set_limits(t_lo, t_hi, s_min, s_max)
        self.curve.model.load(self._settings.get("curve"))
        self.curve.reload()

        lo_pct, hi_pct = int(round(s_min * 100)), int(round(s_max * 100))
        self.static_slider.setRange(lo_pct, hi_pct)
        self.static_spin.setRange(lo_pct, hi_pct)
        self.static_spin.setValue(
            int(min(max(round(float(self._settings["static_speed"]) * 100), lo_pct), hi_pct)))

        keys = [k for k, t in (stats.get("temps") or {}).items() if not t.get("display_only")]
        current_key = self._settings.get("temperature_key") or "edge"
        if current_key not in keys:
            keys.append(current_key)
        self.temp_key.clear()
        self.temp_key.addItems(keys)
        self.temp_key.setCurrentText(current_key)
        self.interval.setValue(int(self._settings["interval_ms"]))
        self.spindown.setValue(int(self._settings.get("spindown_delay_ms") or 0))
        self.threshold.setValue(int(self._settings.get("change_threshold") or 0))

        info = fan.get("pmfw_info") or {}
        self.pmfw_box.setVisible(bool(info))
        self._pmfw = copy.deepcopy(config.get("pmfw_options") or {})
        for key, stat_key, _title, _unit in PMFW_ROWS:
            item = info.get(stat_key) or {}
            lo, hi = (item.get("allowed_range") or [0, 100])[:2]
            current = item.get("current")
            self.pmfw_widgets[key].configure(
                lo, hi, self._pmfw.get(key), lo if current is None else current)
        self._zero_initial = bool(self._pmfw.get("zero_rpm", info.get("zero_rpm_enable", False)))
        self.zero_rpm.setChecked(self._zero_initial)

        index = 0 if not enabled else (1 if self._settings.get("mode") == "static" else 2)
        self.mode.setCurrentIndex(index)
        self._mode_changed(index)

    def update_live(self, stats):
        fan = dig(stats, "fan", default={})
        rpm, pwm = fan.get("speed_current"), fan.get("pwm_current")
        self.live.setText("{} RPM  (PWM {})".format(
            DASH if rpm is None else rpm, DASH if pwm is None else pwm))
        key = self.temp_key.currentText() or "edge"
        self.curve.set_current_temp(dig(stats, "temps", key, "current"))

    def collect(self):
        mode = self.mode.currentIndex()
        changes = {"fan_control_enabled": mode != 0}
        if mode != 0 or self._had_settings:
            settings = copy.deepcopy(self._settings)
            if mode != 0:
                settings.update({
                    "mode": "static" if mode == 1 else "curve",
                    "static_speed": round(self.static_spin.value() / 100, 2),
                    "temperature_key": self.temp_key.currentText(),
                    "interval_ms": self.interval.value(),
                    "curve": self.curve.model.to_config(),
                    "spindown_delay_ms": self.spindown.value(),
                    "change_threshold": self.threshold.value(),
                })
            changes["fan_control_settings"] = settings

        pmfw = copy.deepcopy(self._pmfw)
        for key, widget in self.pmfw_widgets.items():
            value = widget.value()
            if value is None:
                pmfw.pop(key, None)
            else:
                pmfw[key] = value
        if self.zero_rpm.isChecked() != self._zero_initial or "zero_rpm" in pmfw:
            pmfw["zero_rpm"] = self.zero_rpm.isChecked()
        changes["pmfw_options"] = pmfw
        return changes

    def _apply(self):
        self._on_apply(self.collect())


# --------------------------------------------------------------------------- #
class ConfirmDialog(QDialog):
    """Daemon, onaylanmayan ayarı kısa süre sonra geri alır; geri sayım bunu gösterir."""

    def __init__(self, parent, seconds):
        super().__init__(parent)
        self.setWindowTitle("Ayarları onayla")
        self.setModal(True)
        self.left = max(int(seconds), 1)
        lay = QVBoxLayout(self)
        self.label = QLabel()
        lay.addWidget(self.label)
        buttons = QDialogButtonBox()
        buttons.addButton("Değişiklikleri koru", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Geri al", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

    def _refresh(self):
        self.label.setText(
            f"Ayarlar uygulandı. {self.left} sn içinde onaylamazsan geri alınacak.")

    def _tick(self):
        self.left -= 1
        if self.left <= 0:
            self.reject()
        else:
            self._refresh()


# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.devices = []
        self.system = {}
        self.gid = None
        self.info = {}
        self.stats = {}
        self.clocks = {}
        self.config = {}

        self.setWindowTitle("LACT")
        self.setWindowIcon(QIcon.fromTheme("preferences-desktop-display"))
        self.resize(760, 760)

        central = QWidget()
        lay = QVBoxLayout(central)
        top = QHBoxLayout()
        top.addWidget(QLabel("Ekran kartı:"))
        self.gpu_box = QComboBox()
        self.gpu_box.currentIndexChanged.connect(self._gpu_selected)
        top.addWidget(self.gpu_box, 1)
        lay.addLayout(top)

        self.info_page = InfoPage()
        self.stats_page = StatsPage()
        self.perf_page = PerfPage(self.apply_config, self.reload_config)
        self.oc_page = OcPage(self.apply_config, self.reload_config)
        self.fan_page = FanPage(self.apply_config, self.reload_config)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.info_page, "Bilgi")
        self.tabs.addTab(self.stats_page, "İzleme")
        self.tabs.addTab(self.perf_page, "Güç")
        self.tabs.addTab(self.oc_page, "Hız aşırtma")
        self.tabs.addTab(self.fan_page, "Fan")
        lay.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.poll = QTimer(self)
        self.poll.timeout.connect(self.refresh_stats)

    # -- bağlantı ve yükleme --
    def connect_daemon(self):
        try:
            self.devices = self.client.devices()
            self.system = self.client.system_info()
        except LactError as exc:
            self.statusBar().showMessage(
                f"lactd'ye bağlanılamadı ({exc}). 3 sn sonra yeniden denenecek…")
            QTimer.singleShot(3000, self.connect_daemon)
            return
        self.gpu_box.blockSignals(True)
        self.gpu_box.clear()
        for dev in self.devices:
            self.gpu_box.addItem(dev.get("name", dev.get("id", "?")))
        self.gpu_box.blockSignals(False)
        if self.devices:
            self.load_gpu(0)
        else:
            self.statusBar().showMessage("lactd hiç GPU bulamadı.")

    def _gpu_selected(self, index):
        if 0 <= index < len(self.devices):
            self.load_gpu(index)

    def _fetch(self):
        """Config, istatistik ve saat tablosunu okur; saat tablosu isteğe bağlıdır."""
        self.config = self.client.get_config(self.gid)
        self.stats = self.client.device_stats(self.gid)
        try:
            self.clocks = self.client.clocks_info(self.gid)
        except LactError:
            self.clocks = {}

    def _fill_pages(self):
        ratio = dig(self.info, "drm_info", "vram_clock_ratio", default=1.0)
        self.perf_page.load(self.stats, self.config)
        self.oc_page.load(self.config, self.clocks, ratio,
                          self.system.get("amdgpu_overdrive_enabled"))
        self.fan_page.load(self.stats, self.config)

    def load_gpu(self, index):
        self.gid = self.devices[index]["id"]
        try:
            self.info = self.client.device_info(self.gid)
            self._fetch()
        except LactError as exc:
            self.statusBar().showMessage(f"GPU verisi alınamadı: {exc}")
            return
        self.info_page.show_info(self.info, self.system)
        self.stats_page.update_stats(self.stats)
        self._fill_pages()
        self.statusBar().showMessage("Bağlandı.")
        self.poll.start(POLL_MS)

    def refresh_stats(self):
        if not self.gid:
            return
        try:
            stats = self.client.device_stats(self.gid)
        except LactError as exc:
            self.statusBar().showMessage(f"İzleme güncellenemedi: {exc}")
            return
        self.stats_page.update_stats(stats)
        self.fan_page.update_live(stats)

    def reload_config(self):
        if not self.gid:
            return
        try:
            self._fetch()
        except LactError as exc:
            self.statusBar().showMessage(f"Yapılandırma okunamadı: {exc}")
            return
        self._fill_pages()

    # -- ayar uygulama --
    def apply_config(self, changes):
        new_config = with_changes(self.config, **changes)
        try:
            seconds = self.client.set_config(self.gid, new_config)
        except LactError as exc:
            QMessageBox.critical(self, "Ayar uygulanamadı", str(exc))
            return
        keep = ConfirmDialog(self, seconds).exec() == QDialog.DialogCode.Accepted
        try:
            self.client.confirm(keep)
        except LactError:
            pass  # süre dolduysa daemon ayarı zaten geri aldı
        self.reload_config()
        self.statusBar().showMessage("Ayarlar korundu." if keep else "Ayarlar geri alındı.")


def main():
    parser = argparse.ArgumentParser(description="LACT için Qt arayüzü")
    parser.add_argument("--socket", default=DEFAULT_SOCKET, help="lactd soket yolu")
    args, qt_args = parser.parse_known_args()

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("lact-qt")
    app.setDesktopFileName("lact-qt")
    window = MainWindow(LactClient(args.socket))
    window.show()
    QTimer.singleShot(0, window.connect_daemon)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
