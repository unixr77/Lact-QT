"""lactd systemd servisini denetleme (Qt gerektirmez; komut çalıştırıcı enjekte edilebilir)."""
import os
import subprocess
import time

UNIT = "lactd.service"
SOCKET_PATH = "/run/lactd.sock"


class ServiceController:
    def __init__(self, unit=UNIT, runner=None):
        self.unit = unit
        self._run = runner or self._default_runner

    @staticmethod
    def _default_runner(args, timeout=10):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            return subprocess.CompletedProcess(args, 127, "", f"{args[0]}: komut bulunamadı")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(args, 124, "", "zaman aşımı")

    def state(self):
        """{'installed': bool, 'active': bool, 'enabled': bool}"""
        listed = self._run(["systemctl", "list-unit-files", self.unit, "--no-legend"])
        installed = listed.returncode == 0 and self.unit in (listed.stdout or "")
        if not installed:
            return {"installed": False, "active": False, "enabled": False}
        active = (self._run(["systemctl", "is-active", self.unit]).stdout or "").strip() == "active"
        enabled = (self._run(["systemctl", "is-enabled", self.unit]).stdout or "").strip() in (
            "enabled", "enabled-runtime", "static")
        return {"installed": True, "active": active, "enabled": enabled}

    def start(self, enable=False):
        """Servisi başlatır (isteğe bağlı olarak açılışta da etkinleştirir). (başarılı, mesaj)."""
        verb = ["enable", "--now"] if enable else ["start"]
        result = self._run(["pkexec", "systemctl", *verb, self.unit], timeout=90)
        if result.returncode == 0:
            return True, ""
        if result.returncode in (126, 127) and not (result.stderr or "").strip():
            return False, "Yetkilendirme reddedildi ya da iptal edildi."
        return False, ((result.stderr or result.stdout or "").strip()
                       or f"systemctl {result.returncode} koduyla çıktı")


def wait_for_socket(path=SOCKET_PATH, seconds=10.0, step=0.25):
    """Soket görünene kadar bekler; göründüyse True."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(step)
    return os.path.exists(path)
