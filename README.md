# LACT QT

LACT QT: LACT'ın GTK4/libadwaita arayüzü yerine Qt6 (PySide6) arayüzü. Donanıma dokunan `lactd`
daemon'u, LACT'ın kaynak kodundan GUI'siz derlenip pakete eklenir; resmi `lact` paketi gerekmez.

## Ekran Görüntüleri

| | |
| :---: | :---: |
| ![Ekran Görüntüsü 1](preview/Ekran%20G%C3%B6r%C3%BCnt%C3%BCs%C3%BC_20261128_004154.png) | ![Ekran Görüntüsü 2](preview/Ekran%20G%C3%B6r%C3%BCnt%C3%BCs%C3%BC_20261128_004218.png) |
| ![Ekran Görüntüsü 3](preview/Ekran%20G%C3%B6r%C3%BCnt%C3%BCs%C3%BC_20261128_004230.png) | ![Ekran Görüntüsü 4](preview/Ekran%20G%C3%B6r%C3%BCnt%C3%BCs%C3%BC_20261128_004252.png) |
| ![Ekran Görüntüsü 5](preview/Ekran%20G%C3%B6r%C3%BCnt%C3%BCs%C3%BC_20261128_004319.png) | ![Ekran Görüntüsü 6](preview/Ekran%20G%C3%B6r%C3%BCnt%C3%BCs%C3%BC_20261128_004329.png) |

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

## Otomatik profil kuralları
Profiller sekmesinde bir profil seçip kural belirleyebilirsin: belirli bir süreç çalışırken ya da
GameMode etkinken o profil kendiliğinden etkinleşir ("Kurallara göre otomatik profil değiştir"
açık olmalı). Ayarlar, o sırada etkin olan profile kaydedilir.

## Kaldırma
    sudo pacman -Rns lact-qt

## Uzak daemon
    lact-qt --tcp-address 192.168.1.10:12853

(daemon tarafında `/etc/lact/config.yaml` içinde `tcp_listen_address` ayarlanmalı.)
```[cite: 1]

*(Not: Dosya adlarındaki boşluk ve Türkçe karakterlerin GitHub Markdown'da sorunsuz çalışması için URL kodlaması (`%20`, `%C3%B6` vb.) uygulanmıştır. Dilerseniz tablo hücrelerine görsellerin alt başlıklarını da ekleyebiliriz.)*
