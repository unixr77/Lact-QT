# LACT QT

LACT QT: LACT'ın GTK4/libadwaita arayüzü yerine Qt6 (PySide6) arayüzü. Donanıma dokunan `lactd`
daemon'u, LACT'ın kaynak kodundan GUI'siz derlenip pakete eklenir; resmi `lact` paketi gerekmez.

## Kurulum (Arch / CachyOS)
    bash install.sh

Resmi `lact` kuruluysa pacman kaldırmayı sorar. `/etc/lact/config.yaml` korunur, eski
ayarların aynen devam eder.

## Ayarlar nasıl hatırlanır?
"Değişiklikleri koru" dediğin ayarlar `/etc/lact/config.yaml` dosyasına yazılır. `lactd.service`
açılışta bu dosyayı okuyup ayarları uygular. Servisi açılışta etkinleştirmek için:

    sudo systemctl enable --now lactd.service

(lact-qt'yi açtığında servis çalışmıyorsa aynısını yapmayı öneren bir diyalog çıkar.)

## Profiller
Profiller sekmesinden profil dışa/içe aktarılır (JSON). İçe aktarma, daemon'da içerik yazan bir komut
olmadığı için profili oluşturup etkinleştirir, ayarı olağan onay penceresiyle uygular ve önceki profile
dönmeyi sorar.

## Kaldırma
    sudo pacman -Rns lact-qt

## Uzak daemon
    lact-qt --tcp-address 192.168.1.10:12853

(daemon tarafında `/etc/lact/config.yaml` içinde `tcp_listen_address` ayarlanmalı.)
