"""Arayüz tercihleri (pencere boyutu, son sekme, grafik seçimi, uzak adres) — JSON dosyası."""
import json
import os


def default_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "lact-qt", "prefs.json")


class Prefs:
    def __init__(self, path=None):
        self.path = path or default_path()
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            self.data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        """Atomik yazar; yazılamazsa sessizce vazgeçer (tercihler kritik değildir)."""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass
