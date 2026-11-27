"""Profil dışa/içe aktarma biçimi (Qt gerektirmez).

Daemon'da içe/dışa aktarma komutu yoktur; dosya biçimi bu uygulamaya aittir:
    {"format": "lact-qt-profile", "version": 1, "name": ..., "rule": ..., "gpus": {<gpu id>: <ayar>}}
"""
import json

FORMAT = "lact-qt-profile"
VERSION = 1


def build_export(name, rule, gpus, lact_version=None):
    return {"format": FORMAT, "version": VERSION, "lact_version": lact_version,
            "name": name or "Varsayılan", "rule": rule or None, "gpus": gpus}


def dumps(export):
    return json.dumps(export, indent=2, ensure_ascii=False)


def parse_export(text):
    """Dosya metnini doğrular; geçerliyse {'name', 'rule', 'gpus', 'lact_version'} döndürür."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"Dosya geçerli JSON değil: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Bu dosya bir lact-qt profili değil")
    if data.get("version") != VERSION:
        raise ValueError(f"Desteklenmeyen profil sürümü: {data.get('version')}")
    gpus = data.get("gpus")
    if not isinstance(gpus, dict) or not gpus or not all(isinstance(v, dict) for v in gpus.values()):
        raise ValueError("Dosyada GPU ayarı yok")
    rule = data.get("rule")
    if rule is not None and not isinstance(rule, dict):
        raise ValueError("Profil kuralı geçersiz")
    return {"name": str(data.get("name") or "İçe aktarılan profil"), "rule": rule or None,
            "gpus": gpus, "lact_version": data.get("lact_version")}


def pick_config(export, gid, device_ids):
    """Hedef GPU için ayarı seçer -> (ayar, uyarı). Uygun ayar yoksa (None, None)."""
    gpus = export["gpus"]
    if gid in gpus:
        return gpus[gid], None
    if len(gpus) == 1 and len(device_ids) == 1:
        (other_id, config), = gpus.items()
        return config, (f"Dosyadaki ayarlar farklı bir karta ({other_id}) ait.\n"
                        "Saat, voltaj ve fan değerleri bu kart için uygun olmayabilir.")
    return None, None


def unique_name(name, existing):
    candidate, n = name, 2
    while candidate in existing:
        candidate = f"{name} ({n})"
        n += 1
    return candidate


def config_to_changes(current, new):
    """Mevcut yapılandırmayı tamamen yenisiyle değiştirecek değişiklikler (silinecekler None)."""
    return {key: new.get(key) for key in set(current) | set(new)}


def extract_profile(data):
    """get_profile yanıtından (kural, {gpu_id: ayar}); biçim beklenenden farklıysa (None, {})."""
    if not isinstance(data, dict) or not isinstance(data.get("gpus"), dict):
        return None, {}
    return data.get("rule") or None, {k: v for k, v in data["gpus"].items() if isinstance(v, dict)}
