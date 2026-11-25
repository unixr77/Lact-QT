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

    def call(self, command, args=None):
        request = {"command": command}
        if args is not None:
            request["args"] = args
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
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
