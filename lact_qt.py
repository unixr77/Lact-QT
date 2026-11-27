#!/usr/bin/env python3
"""LACT için Qt6 (PySide6) arayüzü.

Sekmeler: Bilgi, İzleme (canlı + geçmiş), Güç, Fan, Profiller, Süreçler; sistem işlemleri ☰ menüsünde.
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
    QInputDialog, QMenu, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from curve import DEFAULT_CURVE, CurveModel
from extra_pages import (
    HistoryPage, ProcessesPage, ProfilesPage, StatesPage, SystemActions, VulkanDialog,
    ask_service_action,
)
from lact_client import DEFAULT_SOCKET, LactClient, LactError, is_tcp_address, with_changes
from logic import VERSION, display_rows
from prefs import Prefs
from service import ServiceController, wait_for_socket
from ui_common import DASH, confirm, dig, gib, group, waiting

LEVELS = ["auto", "low", "high", "manual"]
POLL_MS = 1000
PROCESS_POLL_MS = 3000

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
    def __init__(self, on_vulkan=None):
        super().__init__()
        self._on_vulkan = on_vulkan
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

    def show_info(self, info, system, displays=None):
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
            if self._on_vulkan:
                btn = QPushButton("Vulkan ayrıntıları…")
                btn.clicked.connect(self._on_vulkan)
                row = QHBoxLayout()
                row.addStretch(1)
                row.addWidget(btn)
                lay.addLayout(row)
        if cl:
            lay.addWidget(group("OpenCL", [
                ("Platform", cl.get("platform_name", DASH)),
                ("Sürüm", str(cl.get("version", DASH)).strip()),
                ("Genel bellek", gib(cl.get("global_memory"))),
            ]))
        od = system.get("amdgpu_overdrive_enabled")
        shown = display_rows(displays)
        if shown:
            lay.addWidget(group("Ekranlar", shown))
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
        self.throttle = QLabel(DASH)
        for name, widget in (("GPU saati", self.gpu_clock), ("VRAM saati", self.vram_clock),
                             ("GPU voltajı", self.voltage), ("Fan", self.fan),
                             ("Kısılma", self.throttle)):
            misc.addRow(f"{name}:", widget)
        outer.addWidget(misc_box)

        temp_box = QGroupBox("Sıcaklıklar")
        self.temp_form = QFormLayout(temp_box)
        self.temp_rows = {}
        outer.addWidget(temp_box)
        outer.addStretch(1)

    def reset_temps(self):
        """Başka karta geçince eski sensör satırlarını kaldırır."""
        for label in self.temp_rows.values():
            self.temp_form.removeRow(label)
        self.temp_rows = {}

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

    def __init__(self):
        super().__init__()
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


# --------------------------------------------------------------------------- #
class OcPage(QWidget):
    """GPU saat ofseti, voltaj ofseti (düşük voltaj / undervolt) ve azami VRAM saati."""

    def __init__(self):
        super().__init__()
        self._config = {}
        self._supported = {}
        outer = QVBoxLayout(self)
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

        outer.addWidget(QLabel("Özel değer kutusunun işaretini kaldırmak ayarı varsayılana döndürür."))

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

    def __init__(self, on_apply, on_reload, on_reset_pmfw=None):
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
        self.fan_warn = QLabel("")
        mode_form.addRow("", self.fan_warn)
        outer.addWidget(mode_box)

        self.curve_box = QGroupBox("Özel eğri")
        curve_lay = QVBoxLayout(self.curve_box)
        self.curve = CurveEditor()
        curve_lay.addWidget(self.curve)
        self.curve_hint = QLabel(
            "Sürükle: taşı  •  boş yere çift tıkla: nokta ekle  •  sağ tık ya da Delete: sil")
        curve_lay.addWidget(self.curve_hint)
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
        self.pmfw_reset = QPushButton("Sürücü ayarlarını sıfırla")
        if on_reset_pmfw:
            self.pmfw_reset.clicked.connect(on_reset_pmfw)
        pmfw_form.addRow(self.pmfw_reset)
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

    def load(self, stats, config, flags=None):
        flags = list(flags or [])
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

        # RDNA3+ kartlarda (HasPmfw) LACT eğrinin tam 5 noktalı olmasını ister.
        fixed = "HasPmfw" in flags
        self.curve.model.fixed_count = 5 if fixed else None
        self.curve_hint.setText(
            "Sürükle: noktaları taşı  •  bu kartta eğri tam 5 noktalı olmalı (nokta eklenemez/silinemez)"
            if fixed else
            "Sürükle: taşı  •  boş yere çift tıkla: nokta ekle  •  sağ tık ya da Delete: sil")
        self.fan_warn.setText(
            "" if (not flags or "ConfigurableFanControl" in flags) else
            "Uyarı: bu kartta özel fan kontrolü yapılandırılamıyor görünüyor (Overdrive kapalı olabilir).")
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
class TuningPage(QScrollArea):
    """Güç sınırı, saat/voltaj ve (gelişmiş) güç durumları tek sayfada, tek Uygula düğmesiyle."""

    def __init__(self, on_apply, on_reload, on_overdrive):
        super().__init__()
        self._on_apply = on_apply
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        outer = QVBoxLayout(body)

        self.od_box = QGroupBox("Overclocking kapalı")
        od = QHBoxLayout(self.od_box)
        od.addWidget(QLabel("Saat ve voltaj ayarları için sürücüde Overdrive açılmalı "
                            "(yeniden başlatma gerekir)."), 1)
        self.od_btn = QPushButton("Etkinleştir")
        self.od_btn.clicked.connect(on_overdrive)
        od.addWidget(self.od_btn)
        outer.addWidget(self.od_box)

        self.perf = PerfPage()
        self.oc = OcPage()
        outer.addWidget(self.perf)
        outer.addWidget(self.oc)

        self.adv = QGroupBox("Gelişmiş: güç durumları ve profil modu")
        self.adv.setCheckable(True)
        self.adv.setChecked(False)
        adv_lay = QVBoxLayout(self.adv)
        self.states = StatesPage()
        adv_lay.addWidget(self.states)
        self.states.setVisible(False)
        self.adv.toggled.connect(self.states.setVisible)
        outer.addWidget(self.adv)

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

    def load(self, stats, config, clocks, ratio, overdrive, states, modes):
        self.perf.load(stats, config)
        self.oc.load(config, clocks, ratio, overdrive)
        self.states.load(states, modes, config)
        self.od_box.setVisible(overdrive is False)

    def collect(self):
        changes = {}
        changes.update(self.perf.collect())
        changes.update(self.oc.collect())
        if self.adv.isChecked():  # kapalıyken güç durumlarına dokunulmaz
            changes.update(self.states.collect())
        return changes

    def _apply(self):
        try:
            changes = self.collect()
        except ValueError as exc:
            QMessageBox.warning(self, "Geçersiz ayar", str(exc))
            return
        self._on_apply(changes)


class MonitorPage(QScrollArea):
    """Canlı değerler ve geçmiş grafikleri tek kaydırılabilir sayfada."""

    def __init__(self, stats_page, history_page):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.addWidget(stats_page)
        lay.addWidget(history_page)
        self.setWidget(body)


# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self, client, prefs=None, service=None):
        super().__init__()
        self.client = client
        self.prefs = prefs if prefs is not None else Prefs()
        self.service = service if service is not None else ServiceController()
        self._service_prompted = False
        self._denied_shown = False
        self._tab_restored = False
        self.devices = []
        self.system = {}
        self.gid = None
        self.info = {}
        self.stats = {}
        self.clocks = {}
        self.displays = {}
        self.states = {}
        self.modes = {}
        self.config = {}

        self.setWindowTitle("LACT")
        self.setWindowIcon(QIcon.fromTheme("lact-qt", QIcon.fromTheme("preferences-desktop-display")))
        self.resize(int(self.prefs.get("width", 860)), int(self.prefs.get("height", 760)))

        central = QWidget()
        lay = QVBoxLayout(central)
        top = QHBoxLayout()
        self.gpu_label = QLabel("Ekran kartı:")
        self.gpu_box = QComboBox()
        self.gpu_box.currentIndexChanged.connect(self._gpu_selected)
        top.addWidget(self.gpu_label)
        top.addWidget(self.gpu_box, 1)
        top.addStretch(1)
        self.profile_label = QLabel("Profil: " + DASH)
        top.addWidget(self.profile_label)
        self.menu_btn = QToolButton()
        self.menu_btn.setText("☰")
        self.menu_btn.setToolTip("Menü")
        self.menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        top.addWidget(self.menu_btn)
        lay.addLayout(top)

        self.sysactions = SystemActions(self, client, lambda: self.gid, self.on_system_changed)
        self._build_menu()

        self.info_page = InfoPage(self.show_vulkan)
        self.stats_page = StatsPage()
        self.history_page = HistoryPage(self.prefs)
        self.monitor_page = MonitorPage(self.stats_page, self.history_page)
        self.tuning_page = TuningPage(self.apply_config, self.reload_config, self.toggle_overdrive)
        self.fan_page = FanPage(self.apply_config, self.reload_config, self.reset_pmfw)
        self.profiles_page = ProfilesPage(client, self.on_profile_changed, self.apply_config,
                                          self._profile_context)
        self.proc_page = ProcessesPage()
        self.tabs = QTabWidget()
        for page, title in ((self.info_page, "Bilgi"), (self.monitor_page, "İzleme"),
                            (self.tuning_page, "Güç"), (self.fan_page, "Fan"),
                            (self.profiles_page, "Profiller"), (self.proc_page, "Süreçler")):
            self.tabs.addTab(page, title)
        self.tabs.currentChanged.connect(self._tab_changed)
        lay.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.poll = QTimer(self)
        self.poll.timeout.connect(self.refresh_stats)
        self.proc_timer = QTimer(self)
        self.proc_timer.timeout.connect(self.refresh_processes)

    # -- menü --
    def _build_menu(self):
        menu = QMenu(self.menu_btn)
        self.menu_btn.setMenu(menu)

        def add(text, slot):
            action = menu.addAction(text)
            action.triggered.connect(lambda *_: slot())
            return action

        self.act_remote = add("Uzak daemon'a bağlan…", self.connect_remote)
        self.act_local = add("Yerel daemon'a dön", self.connect_local)
        menu.addSeparator()
        self.act_od = add("Overclocking'i etkinleştir", self.toggle_overdrive)
        self.act_service = add("LACT servisini yönet…", self.manage_service)
        menu.addSeparator()
        self.act_snapshot = add("Hata ayıklama snapshot'ı oluştur", self.sysactions.snapshot)
        self.act_vbios = add("VBIOS'u kaydet…", self.sysactions.vbios)
        self.act_detach = add("GPU'yu ayır", lambda: self.sysactions.attach(False))
        self.act_attach = add("GPU'yu yeniden bağla", lambda: self.sysactions.attach(True))
        self.act_reset = add("Tüm ayarları sıfırla", self.sysactions.reset)
        menu.addSeparator()
        self.act_about = add("Hakkında", self.show_about)

    def _update_menu(self):
        flags = self.info.get("flags") or []
        od = self.system.get("amdgpu_overdrive_enabled")
        self.act_od.setText("Overclocking'i devre dışı bırak" if od else "Overclocking'i etkinleştir")
        self.act_od.setEnabled(od is not None)
        self.act_vbios.setEnabled("DumpableVBios" in flags)
        self.act_local.setVisible(self.client.is_remote)
        self.setWindowTitle(f"LACT — {self.client.path}" if self.client.is_remote else "LACT")

    # -- bağlantı ve yükleme --
    def connect_daemon(self):
        try:
            self.devices = self.client.devices()
            self.system = self.client.system_info()
        except LactError as exc:
            reason = getattr(exc, "reason", None)
            if not self.client.is_remote and reason in ("missing", "refused") \
                    and not self._service_prompted:
                self._offer_service()
                QTimer.singleShot(300, self.connect_daemon)
                return
            if reason == "denied" and not self._denied_shown:
                self._denied_shown = True
                QMessageBox.warning(
                    self, "Soket erişimi reddedildi",
                    "lactd soketine erişim izni yok.\\nKullanıcın lactd'nin yetkili grubunda "
                    "(varsayılan: wheel) olmalı ya da /etc/lact/config.yaml içinde admin_user "
                    "ayarlanmalı; ardından servisi yeniden başlat.")
            self.statusBar().showMessage(
                f"lactd'ye bağlanılamadı ({exc}). 3 sn sonra yeniden denenecek…")
            QTimer.singleShot(3000, self.connect_daemon)
            return
        previous = self.gid
        self.gpu_box.blockSignals(True)
        self.gpu_box.clear()
        for dev in self.devices:
            self.gpu_box.addItem(dev.get("name", dev.get("id", "?")))
        # Yeniden bağlanınca (profil/sistem değişikliği) seçili kart korunur
        index = next((i for i, d in enumerate(self.devices) if d.get("id") == previous), 0)
        if self.devices:
            self.gpu_box.setCurrentIndex(index)
        self.gpu_box.blockSignals(False)
        if self.devices:
            self.load_gpu(index)
        else:
            self.gid = None
            self.statusBar().showMessage("lactd hiç GPU bulamadı.")
        self._update_menu()
        self.profiles_page.reload()
        if not self._tab_restored:
            self._tab_restored = True
            tab = self.prefs.get("tab", 0)
            if isinstance(tab, int) and 0 <= tab < self.tabs.count():
                self.tabs.setCurrentIndex(tab)

    def _gpu_selected(self, index):
        if 0 <= index < len(self.devices):
            self.load_gpu(index)

    def _optional(self, fn):
        """Kartın desteklemeyebileceği okumalar: hata olursa boş döner."""
        try:
            return fn(self.gid)
        except LactError:
            return {}

    def _fetch(self):
        """Config ve istatistik zorunlu; geri kalanlar isteğe bağlıdır."""
        self.config = self.client.get_config(self.gid)
        self.stats = self.client.device_stats(self.gid)
        self.clocks = self._optional(self.client.clocks_info)
        self.states = self._optional(self.client.power_states)
        self.modes = self._optional(self.client.power_profile_modes)

    def _fill_pages(self):
        flags = self.info.get("flags") or []
        ratio = dig(self.info, "drm_info", "vram_clock_ratio", default=1.0)
        self.tuning_page.load(self.stats, self.config, self.clocks, ratio,
                              self.system.get("amdgpu_overdrive_enabled"), self.states, self.modes)
        self.fan_page.load(self.stats, self.config, flags)
        self._update_menu()

    def load_gpu(self, index):
        previous, self.gid = self.gid, self.devices[index]["id"]
        if previous != self.gid:  # farklı karta geçildi: eski kartın verisi karışmasın
            self.history_page.clear()
            self.stats_page.reset_temps()
        try:
            self.info = self.client.device_info(self.gid)
            self._fetch()
        except LactError as exc:
            self.statusBar().showMessage(f"GPU verisi alınamadı: {exc}")
            return
        self.displays = self._optional(self.client.displays_info)
        self.info_page.show_info(self.info, self.system, self.displays)
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
        self.history_page.add_stats(stats)

    def reload_config(self):
        if not self.gid:
            return
        try:
            self._fetch()
        except LactError as exc:
            self.statusBar().showMessage(f"Yapılandırma okunamadı: {exc}")
            return
        self._fill_pages()

    # -- systemd servisi --
    def _run_service_dialog(self, state):
        action = ask_service_action(self, state)
        if action is None:
            return False
        with waiting():
            ok, message = self.service.start(enable=(action == "enable"))
            if ok:
                wait_for_socket()
        if not ok:
            QMessageBox.warning(self, "Servis başlatılamadı", message)
            return False
        self.statusBar().showMessage("LACT servisi başlatıldı.")
        return True

    def _offer_service(self):
        self._service_prompted = True
        state = self.service.state()
        if not state["installed"]:
            QMessageBox.warning(self, "LACT servisi bulunamadı",
                                "lactd.service sistemde kurulu değil.\\n"
                                "lact-qt paketini kurduğundan emin ol.")
            return False
        if state["active"]:
            return False  # çalışıyor; bağlantı yeniden denenecek
        return self._run_service_dialog(state)

    def manage_service(self):
        if self.client.is_remote:
            QMessageBox.information(self, "LACT servisi",
                                    "Uzak daemon'a bağlıyken yerel servis yönetilemez.")
            return
        state = self.service.state()
        if not state["installed"]:
            QMessageBox.warning(self, "LACT servisi bulunamadı",
                                "lactd.service sistemde kurulu değil.")
            return
        if state["active"] and state["enabled"]:
            QMessageBox.information(self, "LACT servisi",
                                    "Servis çalışıyor ve bilgisayar açılınca otomatik başlıyor.")
            return
        if self._run_service_dialog(state):
            self.connect_daemon()

    # -- uzak bağlantı --
    def connect_remote(self):
        text, accepted = QInputDialog.getText(
            self, "Uzak daemon", "Adres (host:port):", text=str(self.prefs.get("remote", "")))
        address = (text or "").strip()
        if not accepted or not address:
            return
        if not is_tcp_address(address):
            QMessageBox.warning(self, "Uzak daemon", "Adres 'host:port' biçiminde olmalı.")
            return
        self.prefs.set("remote", address)
        self.client.path = address
        self.connect_daemon()

    def connect_local(self):
        self.client.path = DEFAULT_SOCKET
        self._service_prompted = False
        self.connect_daemon()

    # -- sekmeler --
    def _tab_changed(self, index):
        self.prefs.set("tab", index)
        page = self.tabs.widget(index)
        if page is self.proc_page:
            self.refresh_processes()
            self.proc_timer.start(PROCESS_POLL_MS)
        else:
            self.proc_timer.stop()
        if page is self.profiles_page:
            self.profiles_page.reload()

    def refresh_processes(self):
        if not self.gid:
            return
        try:
            self.proc_page.load(self.client.process_list(self.gid))
        except LactError as exc:
            self.statusBar().showMessage(f"Süreç listesi alınamadı: {exc}")

    def show_vulkan(self):
        if not self.gid:
            return
        try:
            api = self.client.api_info(self.gid)
        except LactError as exc:
            QMessageBox.warning(self, "Vulkan ayrıntıları", str(exc))
            return
        VulkanDialog(self, api).exec()

    def show_about(self):
        QMessageBox.about(
            self, "LACT hakkında",
            f"LACT Qt arayüzü {VERSION}\\nDaemon: LACT {self.system.get('version', DASH)}\\n"
            f"Bağlantı: {self.client.path}")

    def _profile_context(self):
        return self.gid, [d["id"] for d in self.devices], self.system.get("version")

    def on_profile_changed(self, current, config_changed):
        self.profile_label.setText(f"Profil: {current or 'Varsayılan'}")
        if config_changed:
            self.reload_config()

    def on_system_changed(self):
        """Overdrive, GPU ayırma ya da sıfırlama sonrası her şeyi baştan okur."""
        self.connect_daemon()

    def toggle_overdrive(self):
        self.sysactions.overdrive(not self.system.get("amdgpu_overdrive_enabled"))

    def reset_pmfw(self):
        if not self.gid or not confirm(
                self, "Sürücü fan ayarları",
                "Sürücünün fan ayarları (akustik limit, hedef sıcaklık, zero RPM…) "
                "varsayılana döndürülsün mü?"):
            return
        try:
            with waiting():
                self.client.reset_pmfw(self.gid)
        except LactError as exc:
            QMessageBox.warning(self, "Sürücü fan ayarları", str(exc))
            return
        self.reload_config()

    # -- ayar uygulama --
    def apply_config(self, changes):
        """Ayarı uygular; korundu mu (True/False) döndürür, hata olursa None."""
        new_config = with_changes(self.config, **changes)
        try:
            seconds = self.client.set_config(self.gid, new_config)
        except LactError as exc:
            QMessageBox.critical(self, "Ayar uygulanamadı", str(exc))
            return None
        keep = ConfirmDialog(self, seconds).exec() == QDialog.DialogCode.Accepted
        try:
            self.client.confirm(keep)
        except LactError:
            pass  # süre dolduysa daemon ayarı zaten geri aldı
        self.reload_config()
        self.statusBar().showMessage(
            "Ayarlar korundu ve kaydedildi." if keep else "Ayarlar geri alındı.")
        return keep

    def closeEvent(self, event):
        self.prefs.set("width", self.width())
        self.prefs.set("height", self.height())
        self.prefs.set("tab", self.tabs.currentIndex())
        self.prefs.save()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description="LACT için Qt arayüzü")
    parser.add_argument("--socket", default=DEFAULT_SOCKET, help="lactd unix soket yolu")
    parser.add_argument("--tcp-address", metavar="HOST:PORT",
                        help="uzak lactd'ye TCP ile bağlan")
    args, qt_args = parser.parse_known_args()
    address = args.tcp_address or args.socket
    if args.tcp_address and not is_tcp_address(args.tcp_address):
        parser.error("--tcp-address 'host:port' biçiminde olmalı")

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("lact-qt")
    app.setDesktopFileName("lact-qt")
    window = MainWindow(LactClient(address))
    window.show()
    QTimer.singleShot(0, window.connect_daemon)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
