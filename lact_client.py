"""lactd (LACT daemon) için hafif JSON istemcisi. Qt'a bağımlı değildir."""
import copy
import json
import socket

DEFAULT_SOCKET = "/run/lactd.sock"


class LactError(Exception):
    """Daemon'a ulaşılamadı ya da daemon hata döndürdü."""


def _error_text(data):
    # Daemon hataları {"description": ..., "source": {"description": ...}} zinciri olarak gelir.
    parts = []
    node = data
    while isinstance(node, dict):
        if node.get("description"):
            parts.append(str(node["description"]))
        node = node.get("source")
    return ": ".join(parts) if parts else str(data)


def with_changes(config, **changes):
    """Yapılandırmanın kopyasını döndürür; yalnızca verilen anahtarlar değişir.

    Bilmediğimiz anahtarlar olduğu gibi korunur. Değeri None olan anahtar
    silinir: LACT'ta alanın hiç olmaması "varsayılanı kullan" demektir.
    """
    new = copy.deepcopy(config)
    for key, value in changes.items():
        if value is None:
            new.pop(key, None)
        else:
            new[key] = copy.deepcopy(value)
    return new


class LactClient:
    def __init__(self, path=DEFAULT_SOCKET, timeout=3.0):
        self.path = path
        self.timeout = timeout

    def call(self, command, args=None, timeout=None):
        request = {"command": command}
        if args is not None:
            request["args"] = args
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout or self.timeout)
        buf = bytearray()
        try:
            sock.connect(self.path)
            sock.sendall((json.dumps(request) + "\n").encode())
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        except OSError as exc:
            raise LactError(f"{self.path}: {exc}") from exc
        finally:
            sock.close()
        try:
            response = json.loads(buf)
        except ValueError as exc:
            raise LactError("Daemon'dan geçersiz yanıt geldi") from exc
        if response.get("status") != "ok":
            raise LactError(_error_text(response.get("data")))
        return response.get("data")

    # --- okuma ---
    def devices(self):
        return self.call("list_devices") or []

    def system_info(self):
        return self.call("system_info") or {}

    def device_info(self, gid):
        return self.call("device_info", {"id": gid}) or {}

    def device_stats(self, gid):
        return self.call("device_stats", {"id": gid}) or {}

    def clocks_info(self, gid):
        return self.call("device_clocks_info", {"id": gid}) or {}

    def get_config(self, gid):
        return self.call("get_gpu_config", {"id": gid}) or {}

    # --- yazma ---
    def set_config(self, gid, config):
        """Yapılandırmayı uygular; daemon'un geri alma süresini (sn) döndürür."""
        seconds = self.call("set_gpu_config", {"id": gid, "config": config})
        return int(seconds) if isinstance(seconds, (int, float)) else 5

    def confirm(self, keep=True):
        """Bekleyen ayarı onaylar. Onaylanmazsa daemon kısa süre sonra geri alır."""
        self.call("confirm_pending_config",
                  {"command": "confirm" if keep else "revert"})

    # --- ek okuma ---
    def api_info(self, gid):
        return self.call("device_api_info", {"id": gid}, timeout=15) or {}

    def displays_info(self, gid):
        return self.call("displays_info", {"id": gid}) or {}

    def power_states(self, gid):
        return self.call("get_power_states", {"id": gid}) or {}

    def power_profile_modes(self, gid):
        return self.call("device_power_profile_modes", {"id": gid}) or {}

    def process_list(self, gid):
        return self.call("process_list", {"id": gid}) or {}

    # --- profiller ---
    # list_profiles, set_profile, create_profile ve delete_profile argümanları LACT 0.10.1
    # kaynağına göre doğrulanmıştır. move_profile, hold_profile, release_profile,
    # set_profile_rule ve evaluate_profile_rule argümanları DOĞRULANMADI; yanlışsa
    # daemon'un hata mesajı arayüzde gösterilir.
    def list_profiles(self):
        return self.call("list_profiles", {"include_state": False}) or {}

    def set_profile(self, name, auto_switch=False):
        """name=None varsayılan profili (ayarsız) etkinleştirir."""
        self.call("set_profile", {"name": name, "auto_switch": bool(auto_switch)})

    def create_profile(self, name, base=None):
        """base=None: varsayılan profilin kopyası; metin: o profilin kopyası."""
        self.call("create_profile",
                  {"name": name, "base": "default" if base is None else {"profile": base}})

    def delete_profile(self, name):
        self.call("delete_profile", {"name": name})

    def move_profile(self, name, position):
        self.call("move_profile", {"name": name, "new_position": int(position)})

    def hold_profile(self, name):
        self.call("hold_profile", {"name": name})

    def release_profile(self):
        self.call("release_profile")

    def set_profile_rule(self, name, rule):
        self.call("set_profile_rule", {"name": name, "rule": rule})

    def evaluate_profile_rule(self, rule):
        return self.call("evaluate_profile_rule", {"rule": rule})

    # --- sistem ---
    def enable_overdrive(self):
        """Çekirdek parametresini yazar ve initramfs'i yeniler (yavaş olabilir); yeniden başlatma gerekir."""
        return self.call("enable_overdrive", timeout=180)

    def disable_overdrive(self):
        return self.call("disable_overdrive", timeout=180)

    def generate_snapshot(self):
        return self.call("generate_snapshot", timeout=60)

    def vbios_dump(self, gid):
        return self.call("vbios_dump", {"id": gid}, timeout=30)

    def detach_gpu(self, gid):
        return self.call("detach_gpu", {"id": gid})

    def reattach_gpu(self, gid):
        return self.call("reattach_gpu", {"id": gid})

    def reset_config(self):
        # Daemon'daki komut adı gerçekten "rest_config" (sürüm 0.10.1).
        return self.call("rest_config")
