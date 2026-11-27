# Maintainer: lact-qt kullanıcısı
# LACT'ın GTK4/libadwaita arayüzü yerine Qt6 (PySide6) arayüzü kurar. Donanıma dokunan
# 'lact' daemon'u (lactd) kaynak koddan, GUI'siz (headless) olarak derlenir; böylece
# resmi 'lact' paketi gerekmez.
pkgname=lact-qt
pkgver=1.0.0
pkgrel=1
_lactver=0.10.1
pkgdesc="LACT için Qt6/KDE arayüzü ve GUI'siz LACT daemon'u (GTK/libadwaita gerekmez)"
arch=('x86_64')
url="https://github.com/ilya-zlobintsev/LACT"
license=('MIT')
depends=('python' 'pyside6' 'polkit' 'systemd' 'libdrm' 'hwdata')
makedepends=('cargo' 'clang' 'make')
optdepends=('vulkan-icd-loader: Vulkan bilgisi'
            'ocl-icd: OpenCL bilgisi')
provides=('lact' 'lactd')
conflicts=('lact' 'lact-git' 'lact-libadwaita')
install=lact-qt.install
source=("LACT-${_lactver}.tar.gz::https://github.com/ilya-zlobintsev/LACT/archive/refs/tags/v${_lactver}.tar.gz"
        'lact_qt.py'
        'extra_pages.py'
        'logic.py'
        'curve.py'
        'lact_client.py'
        'ui_common.py'
        'prefs.py'
        'service.py'
        'profile_io.py'
        'lactd.service'
        'lact-qt.desktop'
        'lact-qt.svg'
        'lact-qt')
# 'updpkgsums' (pacman-contrib) bu değerleri gerçek sağlama toplamlarıyla doldurur;
# install.sh bunu otomatik yapar.
sha256sums=('SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP' 'SKIP')

build() {
  cd "LACT-${_lactver}"
  export CARGO_TARGET_DIR=target
  make build-release-headless
}

package() {
  # --- Qt arayüzü ---
  install -d "$pkgdir/usr/lib/$pkgname"
  install -m644 lact_qt.py extra_pages.py logic.py curve.py lact_client.py ui_common.py prefs.py service.py profile_io.py \
    -t "$pkgdir/usr/lib/$pkgname"
  install -Dm755 lact-qt "$pkgdir/usr/bin/lact-qt"
  install -Dm644 lact-qt.desktop "$pkgdir/usr/share/applications/lact-qt.desktop"
  install -Dm644 lact-qt.svg "$pkgdir/usr/share/icons/hicolor/scalable/apps/lact-qt.svg"
  install -Dm644 lactd.service "$pkgdir/usr/lib/systemd/system/lactd.service"

  # --- LACT daemon'u (GUI'siz derleme) ---
  cd "LACT-${_lactver}"
  install -Dm755 target/release/lact "$pkgdir/usr/bin/lact"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE.LACT"
}
