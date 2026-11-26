"""Arayüzden bağımsız yardımcı mantık (Qt gerektirmez, doğrudan test edilebilir)."""
import base64
import json
import math
import time
from collections import deque

# ---------------------------------------------------------------- geçmiş --- #


class HistoryStore:
    """Son N örneği tutar (saniyede bir örnekle 3600 örnek = 60 dk)."""

    def __init__(self, max_samples=3600):
        self.samples = deque(maxlen=max_samples)

    def add(self, values, ts=None):
        self.samples.append((time.time() if ts is None else ts, dict(values)))

    def clear(self):
        self.samples.clear()

    def keys(self):
        seen = []
        for _ts, values in self.samples:
            for key in values:
                if key not in seen:
                    seen.append(key)
        return seen

    def series(self, key, window_s, now=None):
        """[(yaş_sn (<=0), değer)] — son window_s saniyedeki geçerli noktalar."""
        now = time.time() if now is None else now
        out = []
        for ts, values in self.samples:
            value = values.get(key)
            age = ts - now
            if value is not None and age >= -window_s:
                out.append((age, float(value)))
        return out


def sample_from_stats(stats):
    """device_stats yanıtından grafik için sayısal örnek çıkarır."""
    s = stats or {}
    out = {}
    for name, temp in (s.get("temps") or {}).items():
        if not temp.get("display_only") and isinstance(temp.get("current"), (int, float)):
            out[f"temp:{name}"] = temp["current"]
    clk = s.get("clockspeed") or {}
    out["clock:gpu"] = clk.get("gpu_clockspeed")
    out["clock:vram"] = clk.get("vram_clockspeed")
    power = s.get("power") or {}
    out["power"] = power.get("average") if power.get("average") is not None else power.get("current")
    out["fan_rpm"] = (s.get("fan") or {}).get("speed_current")
    out["busy"] = s.get("busy_percent")
    vram = s.get("vram") or {}
    if vram.get("total"):
        out["vram_pct"] = vram.get("used", 0) * 100 / vram["total"]
    return out


def nice_range(lo, hi, ticks=4):
    """Eksen için 'güzel' aralık ve adım: (alt, üst, adım)."""
    if hi - lo < 1e-9:
        hi = lo + 1.0
    raw = (hi - lo) / ticks
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    return math.floor(lo / step) * step, math.ceil(hi / step) * step, step


def fmt_age(seconds):
    seconds = int(round(seconds))
    return f"{seconds} sn" if seconds < 90 else f"{round(seconds / 60)} dk"


# ----------------------------------------------------------- güç durumları --- #
# (get_power_states anahtarı, yapılandırma anahtarı, başlık)
POWER_KINDS = (("core", "core_clock", "GPU saat durumları"),
               ("vram", "memory_clock", "VRAM saat durumları"))


def parse_states(data):
    """get_power_states -> {"core": [(indeks, MHz, etkin)], "vram": [...]}."""
    out = {}
    for key, _cfg, _title in POWER_KINDS:
        items = []
        for st in (data or {}).get(key) or []:
            sid = st.get("id")
            idx = sid.get("Index") if isinstance(sid, dict) else sid
            items.append((idx, st.get("value"), bool(st.get("enabled", True))))
        out[key] = items
    return out


def states_to_config(existing, states, enabled_by_kind):
    """Yapılandırmadaki power_states sözlüğünün yeni halini döndürür (boşsa None).

    Her tür için tüm durumlar açıksa anahtar silinir; hiçbiri açık değilse ValueError.
    """
    result = dict(existing or {})
    for key, cfg_key, title in POWER_KINDS:
        all_idx = [i for i, _mhz, _on in states.get(key, [])]
        if not all_idx:
            continue
        chosen = sorted(i for i in all_idx if i in enabled_by_kind.get(key, set()))
        if not chosen:
            raise ValueError(f"{title}: en az bir durum açık kalmalı")
        if len(chosen) == len(all_idx):
            result.pop(cfg_key, None)
        else:
            result[cfg_key] = chosen
    return result or None


# --------------------------------------------------------- güç profili modu --- #
def parse_modes(data):
    """device_power_profile_modes -> (modlar, değer_adları, etkin_indeks).

    modlar: [(indeks, ad, [(saat_türü, [değerler])])]
    """
    data = data or {}
    modes = []
    for key, mode in (data.get("modes") or {}).items():
        comps = [(c.get("clock_type", "?"), list(c.get("values") or []))
                 for c in mode.get("components") or []]
        modes.append((int(key), mode.get("name", key), comps))
    modes.sort(key=lambda m: m[0])
    return modes, list(data.get("value_names") or []), data.get("active")


def heuristics_from_cells(cells):
    """cells[satır][sütun] metin -> [[int|None]] (bileşen başına bir iç liste)."""
    if not cells:
        return []
    out = []
    for col in range(len(cells[0])):
        column = []
        for row in range(len(cells)):
            text = str(cells[row][col]).strip()
            if text == "":
                column.append(None)
                continue
            try:
                column.append(int(text))
            except ValueError as exc:
                raise ValueError(f"Geçersiz sayı: '{text}'") from exc
        out.append(column)
    return out


# ------------------------------------------------------------------ profiller --- #
RULE_TEMPLATES = {
    "Süreç": {"type": "process", "filter": {"name": "vkcube"}},
    "Gamemode": {"type": "gamemode"},
}


def parse_profiles(data):
    """list_profiles yanıtını dayanıklı biçimde ayrıştırır -> (profiller, etkin, otomatik).

    Yanıtın tam biçimi kesin olarak doğrulanmadığı için hem sözlük hem liste kabul edilir.
    """
    data = data if isinstance(data, dict) else {"profiles": data}
    raw = data.get("profiles")
    profiles = []
    if isinstance(raw, dict):
        for name, value in raw.items():
            rule = value.get("rule") if isinstance(value, dict) and "rule" in value else value
            profiles.append((name, rule if rule else None))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                profiles.append((item, None))
            elif isinstance(item, dict) and item.get("name") is not None:
                profiles.append((item["name"], item.get("rule") or None))
    current = data.get("current_profile")
    auto = bool(data.get("auto_switch", data.get("auto_switch_profiles", False)))
    return profiles, current, auto


def parse_rule_text(text):
    """Kural metni (JSON) -> nesne; boşsa None; geçersizse ValueError."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError(f"Kural geçerli JSON değil: {exc}") from exc


def rule_summary(rule):
    if not rule:
        return "Kural yok"
    if not isinstance(rule, dict):
        return str(rule)
    kind = rule.get("type", "?")
    flt = rule.get("filter")
    if kind == "process" and isinstance(flt, dict):
        extra = f" {flt['args']}" if flt.get("args") else ""
        return f"Süreç: {flt.get('name', '?')}{extra}"
    if kind == "gamemode":
        return "Gamemode"
    return f"{kind} ({len(flt) if isinstance(flt, list) else '?'} koşul)"


# ------------------------------------------------------------ süreçler, vbios --- #
def process_rows(data):
    """process_list -> bellek kullanımına göre sıralı satırlar."""
    rows = []
    for pid, proc in ((data or {}).get("processes") or {}).items():
        rows.append({
            "pid": int(pid), "name": proc.get("name", "?"),
            "memory": proc.get("memory_used") or 0,
            "types": list(proc.get("types") or []),
            "util": proc.get("util") or {}, "args": proc.get("args", ""),
        })
    rows.sort(key=lambda r: r["memory"], reverse=True)
    return rows


def fmt_mem(n):
    if not isinstance(n, (int, float)):
        return "—"
    return f"{n / 2**30:.2f} GiB" if n >= 2**30 else f"{n / 2**20:.0f} MiB"


def vbios_to_bytes(data):
    """vbios_dump yanıtını bayta çevirir (tamsayı listesi ya da base64 metni)."""
    if isinstance(data, (list, tuple)):
        return bytes(data)
    if isinstance(data, str):
        try:
            return base64.b64decode(data, validate=True)
        except ValueError as exc:
            raise ValueError("VBIOS verisi çözülemedi") from exc
    raise ValueError("VBIOS yanıtı beklenmeyen biçimde")


def display_rows(data):
    """displays_info -> [(bağlayıcı, açıklama)]."""
    rows = []
    for connector, info in ((data or {}).get("displays") or {}).items():
        ct = info.get("connector_type")
        if isinstance(ct, dict) and ct:
            kind, detail = next(iter(ct.items()))
            lanes = detail.get("lanes") if isinstance(detail, dict) else None
            ct = f"{kind}" + (f", {lanes} şerit" if lanes else "")
        parts = [info.get("model"), info.get("manufacturer"), ct]
        size = info.get("size")
        if isinstance(size, (list, tuple)) and len(size) == 2:
            parts.append(f"{size[0]}×{size[1]} cm")
        rows.append((connector, "  •  ".join(str(p) for p in parts if p)))
    return rows
