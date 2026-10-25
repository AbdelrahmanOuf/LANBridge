"""Reproducible vector assets and multi-resolution Windows icon (no network)."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'src/lanbridge/resources'
SHAPES = {
    'dashboard': '<rect x="3" y="3" width="7" height="7" rx="1.4"/><rect x="14" y="3" width="7" height="11" rx="1.4"/><rect x="3" y="14" width="7" height="7" rx="1.4"/><rect x="14" y="18" width="7" height="3" rx="1"/>',
    'network': '<rect x="8" y="2" width="8" height="6" rx="1.5"/><path d="M12 8v6M4 14h16M4 14v3m8-3v3m8-3v3"/><rect x="1" y="17" width="6" height="5" rx="1"/><rect x="9" y="17" width="6" height="5" rx="1"/><rect x="17" y="17" width="6" height="5" rx="1"/>',
    'devices': '<rect x="2" y="3" width="15" height="11" rx="2"/><path d="M9 14v5m-4 0h8"/><rect x="17" y="9" width="5" height="12" rx="1.5"/>',
    'share': '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 14h6m-3-3 3 3-3 3"/>',
    'transfer': '<path d="M3 7h17m-4-4 4 4-4 4M21 17H4m4-4-4 4 4 4"/>',
    'history': '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7m2-4v5l3 2"/>',
    'diagnostics': '<path d="M2 12h4l3-8 6 16 3-8h4"/>',
    'settings': '<path d="m10 2-.7 3-2.6 1.5L4 5.5l-2 3 2.4 2v3L2 15.5l2 3 2.7-1 2.6 1.5.7 3h4l.7-3 2.6-1.5 2.7 1 2-3-2.4-2v-3l2.4-2-2-3-2.7 1L14.7 5 14 2z"/><circle cx="12" cy="12" r="3"/>',
    'refresh': '<path d="M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
}


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for name, shape in SHAPES.items():
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke="#79CDE8" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{shape}</g></svg>'
        (DEST / f'{name}.svg').write_text(svg, encoding='utf-8')
    app_svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
    <defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#153465"/><stop offset="1" stop-color="#081222"/></linearGradient>
    <linearGradient id="blue"><stop stop-color="#2563EB"/><stop offset="1" stop-color="#06B6D4"/></linearGradient></defs>
    <rect x="8" y="8" width="240" height="240" rx="59" fill="url(#bg)" stroke="#2A4D79" stroke-width="3"/>
    <path d="M62 152V98a14 14 0 0 1 14-14h40v28H90v40z" fill="#E6F2FF"/>
    <path d="M194 104v54a14 14 0 0 1-14 14h-40v-28h26v-40z" fill="#E6F2FF"/>
    <path d="M82 126h92" stroke="url(#blue)" stroke-width="22" stroke-linecap="round"/>
    <path d="m137 103 24 23-24 23" fill="none" stroke="#08C5E2" stroke-width="13" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="66" cy="184" r="5" fill="#06B6D4"/><path d="M82 184h55" stroke="#355E89" stroke-width="6" stroke-linecap="round"/>
    </svg>'''
    (DEST / 'app.svg').write_text(app_svg, encoding='utf-8')
    app = QApplication.instance() or QApplication([])
    renderer = QSvgRenderer(app_svg.encode())
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    png = DEST / 'app.png'
    image.save(str(png))
    with Image.open(png) as bitmap:
        bitmap.save(DEST / 'app.ico', sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    app.processEvents()


if __name__ == '__main__':
    main()
