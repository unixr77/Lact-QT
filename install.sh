#!/usr/bin/env bash
# lact-qt paketini bu klasörden derleyip kurar (Arch / CachyOS).
# Resmi 'lact' paketi kuruluysa pacman kaldırmayı sorar; /etc/lact/config.yaml korunur.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

if [ "$(id -u)" -eq 0 ]; then
  echo "Bu betiği root olarak çalıştırma; gerektiğinde sudo parolası ister." >&2
  exit 1
fi
command -v makepkg >/dev/null || { echo "makepkg bulunamadı: Arch tabanlı bir sistem gerekli." >&2; exit 1; }

if command -v updpkgsums >/dev/null; then
  updpkgsums
else
  echo "Uyarı: pacman-contrib kurulu değil; sağlama toplamları SKIP olarak kalacak."
fi

makepkg -si

if systemctl is-enabled --quiet lactd.service 2>/dev/null; then
  echo "lactd.service zaten açılışta etkin."
else
  read -rp "lactd servisi şimdi başlatılıp açılışta etkinleştirilsin mi? [E/h] " answer
  case "${answer:-E}" in
    [Ee]*) sudo systemctl enable --now lactd.service ;;
    *) echo "Atlandı. lact-qt'yi açınca servisi başlatma diyaloğu çıkar." ;;
  esac
fi
echo "Tamam. Uygulama menüsünde 'LACT QT' girişini ya da 'lact-qt' komutunu kullan."
