from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import socket
import uuid
from pathlib import Path

from PySide6.QtCore import QPropertyAnimation, QThreadPool, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QSystemTrayIcon,
    QTableWidget, QTableWidgetItem, QTreeView, QVBoxLayout, QWidget,
)

from lanbridge.core.errors import CredentialError, ValidationError
from lanbridge.core.models import Progress, TaskSpec, TaskState, TransferSettings
from lanbridge.core.paths import validate_unc
from lanbridge.network import discovery
from lanbridge.network.diagnostics import connectivity
from lanbridge.network.validation import host, ipv4
from lanbridge.security.credentials import Credentials
from lanbridge.services.application import ApplicationService

from .dialogs import CredentialDialog, NetworkDialog, PreviewDialog, ShareDialog
from .i18n import text
from .style import icon, ltr, stylesheet
from .widgets import ActivityChart, Metric, card, label, row, size_text, time_text
from .workers import Worker

PAGES = ['Dashboard', 'Network Setup', 'Device Discovery', 'File Sharing',
         'Transfer Center', 'Transfer History', 'Diagnostics', 'Settings']
PAGE_ICONS = ['dashboard', 'network', 'devices', 'share', 'transfer', 'history', 'diagnostics', 'settings']


class MainWindow(QMainWindow):
    def __init__(self, service: ApplicationService, *, refresh_on_start: bool = True):
        super().__init__()
        self.service = service
        self.language = service.store.get_setting('language', 'ar')
        self.theme = service.store.get_setting('theme', 'dark')
        self.task: TaskSpec | None = None
        self._workers: set[Worker] = set()
        self._critical_jobs = 0
        self._close_pending = False
        self._sensitive: list[QWidget] = []
        self.adapters_data: list[dict] = []
        self.share_data: list[dict] = []
        self.history_data: list[dict] = []
        self.last_report: dict | None = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.setWindowTitle('LANBridge Pro')
        self.setWindowIcon(icon('app'))
        self.setMinimumSize(760, 460)
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        self.resize(min(1280, max(760, area.width() - 40)) if area else 1280,
                    min(880, max(460, area.height() - 40)) if area else 880)
        QApplication.instance().setLayoutDirection(Qt.LayoutDirection.RightToLeft if self.language == 'ar'
                                                   else Qt.LayoutDirection.LeftToRight)
        QApplication.instance().setStyleSheet(stylesheet(self.theme))
        self._build()
        self.tray = QSystemTrayIcon(icon('app'), self)
        self.tray.setToolTip('LANBridge Pro')
        self.tray.activated.connect(lambda _: self.showNormal())
        self.tray.show()
        self.refresh_history()
        if os.name == 'nt' and refresh_on_start:
            QTimer.singleShot(200, self.refresh_adapters)
        elif os.name != 'nt':
            self.statusBar().showMessage('Linux test host • Windows networking and Robocopy execution are unavailable.')
        if service.recovered and service.store.get_setting('recovery_prompt', True):
            QTimer.singleShot(700, self._recovery_notice)

    def t(self, value: str) -> str:
        return text(value, self.language)

    def button(self, key: str, callback, *, primary=False, sensitive=False, image='') -> QPushButton:
        button = QPushButton(self.t(key))
        if image:
            button.setIcon(icon(image))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            button.setObjectName('primary')
        button.clicked.connect(lambda checked=False: callback())
        if sensitive:
            self._sensitive.append(button)
        return button

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName('root')
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        side = QFrame()
        side.setObjectName('sidebar')
        side.setFixedWidth(226)
        navigation = QVBoxLayout(side)
        navigation.setContentsMargins(16, 24, 16, 20)
        navigation.setSpacing(7)
        navigation.addWidget(label('LANBridge Pro', 'brand'))
        navigation.addWidget(label('LOCAL • SECURE • RESUMABLE', 'brandSub'))
        navigation.addSpacing(28)
        self.nav = []
        for index, key in enumerate(PAGES):
            button = self.button(key, lambda i=index: self.navigate(i), image=PAGE_ICONS[index])
            button.setObjectName('nav')
            button.setCheckable(True)
            navigation.addWidget(button)
            self.nav.append(button)
        navigation.addStretch()
        navigation.addWidget(label(self.t('Offline by design'), 'eyebrow'))
        navigation.addWidget(label(socket.gethostname(), 'muted'))
        navigation.addWidget(label('v1.0.0  •  Windows 10 / 11', 'muted'))
        outer.addWidget(side)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 24, 28, 16)
        content_layout.setSpacing(18)
        header = QHBoxLayout()
        self.page_title = label(self.t('Dashboard'), 'pageTitle')
        header.addWidget(self.page_title)
        header.addStretch()
        header.addWidget(label('●  ' + self.t('Local approval required'), 'eyebrow', wrap=False))
        content_layout.addLayout(header)
        self.stack = QStackedWidget()
        self.builders = [self.dashboard_page, self.network_page, self.discovery_page, self.shares_page,
                         self.transfer_page, self.history_page, self.diagnostics_page, self.settings_page]
        for build in self.builders:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 10, 10)
            layout.setSpacing(18)
            build(layout)
            layout.addStretch()
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        content_layout.addWidget(self.stack)
        self.footer = label(self.t('Files stay at the source. Matching completed files are skipped on resume.'), 'hint')
        content_layout.addWidget(self.footer)
        outer.addWidget(content, 1)
        self.navigate(0)

    def navigate(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.page_title.setText(self.t(PAGES[index]))
        for i, button in enumerate(self.nav):
            button.setChecked(i == index)
        page = self.stack.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        self.animation = QPropertyAnimation(effect, b'opacity', self)
        self.animation.setDuration(160)
        self.animation.setStartValue(.5)
        self.animation.setEndValue(1.0)
        self.animation.finished.connect(lambda: page.setGraphicsEffect(None))
        self.animation.start()
        if index == 5:
            self.refresh_history()

    def dashboard_page(self, layout: QVBoxLayout) -> None:
        hero = QFrame()
        hero.setObjectName('hero')
        box = QVBoxLayout(hero)
        box.setContentsMargins(30, 28, 30, 28)
        box.setSpacing(15)
        box.addWidget(label(self.t('One app. Either direction.'), 'eyebrow'))
        box.addWidget(label(self.t('Your files. A direct connection.'), 'heroTitle'))
        box.addWidget(label(self.t('Select → Check → Transfer'), 'heroText'))
        box.addWidget(row(self.button('New session', lambda: self.navigate(4), primary=True, image='transfer'),
                          self.button('Network Setup', lambda: self.navigate(1), image='network')))
        layout.addWidget(hero)
        self.dashboard_metrics = [Metric(self.t('Local computer'), socket.gethostname()),
                                  Metric(self.t('Stored tasks'), '0'), Metric(self.t('Recoverable tasks'), '0')]
        grid = QGridLayout()
        for i, metric in enumerate(self.dashboard_metrics):
            grid.addWidget(metric, 0, i)
        layout.addLayout(grid)
        for title, hint, target in [
            ('01  Connect the cable', 'Choose a specific Ethernet adapter, then DHCP or direct PC-to-PC setup.', 1),
            ('02  Authorize a share', 'The other PC creates its own share for an authorized Windows account.', 3),
            ('03  Copy and verify', 'Preflight checks space and paths. Sessions are saved for later recovery.', 4),
        ]:
            panel, inner = card(self.t(title))
            inner.addWidget(label(self.t(hint), 'muted'))
            inner.addWidget(self.button(PAGES[target], lambda i=target: self.navigate(i)))
            layout.addWidget(panel)
        panel, inner = card(self.t('Security by Windows'))
        inner.addWidget(label(self.t('SMB signing is preserved. No Guest bypass. No SMB1 activation.'), 'muted'))
        inner.addWidget(label(self.t('File copy does not migrate installed applications or Windows.'), 'hint'))
        layout.addWidget(panel)

    def table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels([self.t(x) for x in headers])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(230)
        # IP, filenames, UNC strings retain their character order in Arabic mode.
        table.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        return table

    @staticmethod
    def fill(table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(r, c, item)

    def network_page(self, layout: QVBoxLayout) -> None:
        layout.addWidget(label('Ethernet • IPv4 • DHCP • DNS', 'eyebrow'))
        layout.addWidget(label('اختر صف المحول المطلوب بدقة؛ المحولات الافتراضية وWi-Fi لا تتغير تلقائيًا. / Select one specific adapter.', 'hint'))
        self.adapter_table = self.table(['Name', 'Status', 'Link speed', 'IPv4 / Prefix', 'MAC', 'Profile'])
        self.adapter_table.itemSelectionChanged.connect(self.adapter_details)
        layout.addWidget(self.adapter_table)
        layout.addWidget(row(self.button('Refresh', self.refresh_adapters, image='refresh'),
                             self.button('Configure IP', self.configure_network, primary=True, sensitive=True),
                             self.button('Private profile', self.private_profile, sensitive=True)))
        layout.addWidget(row(self.button('Allow SMB peer', self.allow_smb, sensitive=True),
                             self.button('Restore saved change', self.restore_change, sensitive=True)))
        self.adapter_detail = QPlainTextEdit()
        self.adapter_detail.setReadOnly(True)
        self.adapter_detail.setMinimumHeight(200)
        self.adapter_detail.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        layout.addWidget(self.adapter_detail)
        layout.addWidget(label('Direct cable: PC A 192.168.50.1/24  ↔  PC B 192.168.50.2/24\n'
                               'Gateway/DNS may remain empty. A Private profile and peer-scoped TCP 445 rule need separate approval.', 'hint'))

    def selected_adapter(self) -> dict:
        index = self.adapter_table.currentRow()
        if not 0 <= index < len(self.adapters_data):
            raise ValidationError('اختر محول الشبكة أولًا. / Select a network adapter first.')
        return self.adapters_data[index]

    def refresh_adapters(self) -> None:
        def show(result):
            self.adapters_data = result
            self.fill(self.adapter_table, [[x['name'], x['status'], x['speed'],
                ', '.join(f"{p['ip']}/{p['prefix']}" for p in x['ipv4']), x['mac'], x['profile']] for x in result])
            if result:
                # Prefer a connected physical Ethernet adapter; selection still explicit for changes.
                chosen = next((i for i, a in enumerate(result) if a['hardware'] and a['status'] == 'Up'
                               and '802.3' in a.get('medium', '')), 0)
                self.adapter_table.selectRow(chosen)
            self.statusBar().showMessage(f'{len(result)} adapters')
        self.submit(lambda _: self.service.adapters(), show)

    def adapter_details(self) -> None:
        try:
            self.adapter_detail.setPlainText(json.dumps(self.selected_adapter(), ensure_ascii=False, indent=2))
        except ValidationError:
            self.adapter_detail.clear()

    def configure_network(self) -> None:
        try:
            adapter = self.selected_adapter()
            dialog = NetworkDialog(adapter, self.language, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if (not adapter['hardware'] or '802.3' not in adapter.get('medium', '')) and not dialog.explicit.isChecked():
                raise ValidationError('أكد اختيار Wi-Fi/Virtual صراحة، أو اختر Ethernet.')
            payload = dialog.payload()
            if payload['mode'] == 'static':
                for other in self.adapters_data:
                    if other['index'] != adapter['index'] and any(x['ip'] == payload['ip'] for x in other['ipv4']):
                        raise ValidationError('هذا العنوان مستخدم على محول محلي آخر.')
                note = ('سيجري Windows فحص Duplicate Address Detection. فشل Ping لا يثبت خلو العنوان.\n'
                        'Windows DAD checks for duplicates; an unused address cannot be guaranteed before assignment.')
                if not self.confirm(note):
                    return
            self.change('network_apply', payload)
        except Exception as exc:
            self.error(str(exc))

    def private_profile(self) -> None:
        try:
            self.change('profile_private', self.selected_adapter())
        except Exception as exc:
            self.error(str(exc))

    def allow_smb(self) -> None:
        try:
            adapter = self.selected_adapter()
            peer, ok = QInputDialog.getText(self, self.t('Allow SMB peer'), 'Authorized peer IPv4:')
            if not ok:
                return
            owner = str(uuid.uuid4())
            self.change('firewall_add', {'index': adapter['index'], 'guid': adapter['guid'],
                                        'peer': ipv4(peer.strip()), 'owner': owner, 'rule': 'LANBridgePro-' + owner})
        except Exception as exc:
            self.error(str(exc))

    def change(self, action: str, payload: dict) -> None:
        def done(result):
            self.info(json.dumps(result.get('data', result), ensure_ascii=False, indent=2))
            self.refresh_adapters()
        self.submit(lambda _: self.service.local_change(action, payload), done, critical=True)

    def restore_change(self) -> None:
        if self.service.busy:
            self.error('انتظر انتهاء المهمة النشطة. / Wait for the active operation.')
            return
        entries = [x for x in self.service.store.operations() if x['action'] in
                   ('network_apply', 'profile_private', 'share_create', 'firewall_add')]
        if not entries:
            self.info('لا توجد تغييرات محفوظة. / No saved local changes.')
            return
        names = [f"{x['action']} | {x['state']} | {dt.datetime.fromtimestamp(x['created']):%Y-%m-%d %H:%M} | {x['id'][:8]}"
                 for x in entries]
        name, ok = QInputDialog.getItem(self, self.t('Restore saved change'),
                                       'Select original local change (current state is previewed before restore):', names, 0, False)
        if ok:
            entry = entries[names.index(name)]
            self.submit(lambda _: self.service.rollback(entry), lambda result: self.info(json.dumps(result, indent=2)), critical=True)

    def discovery_page(self, layout: QVBoxLayout) -> None:
        panel, inner = card(self.t('Connect'))
        self.peer = ltr(QLineEdit())
        self.peer.setPlaceholderText('192.168.50.2  /  PC-B')
        self.unc = ltr(QLineEdit())
        self.unc.setPlaceholderText(r'\\PC-B\LANBridge_abc123')
        inner.addWidget(label('Remote computer / الجهاز الآخر', 'muted'))
        inner.addWidget(self.peer)
        inner.addWidget(label('SMB UNC path', 'muted'))
        inner.addWidget(self.unc)
        inner.addWidget(row(self.button('Connect', self.connect_share, primary=True),
                            self.button('Test connection', self.test_connection),
                            self.button('Forget saved login', self.forget_login)))
        inner.addWidget(label('الجهاز الآخر يوافق محليًا على المشاركة. الاكتشاف لا يمنح أي صلاحية إدارة.\n'
                              'Domain networks: prefer the real computer/DNS name for Kerberos; IP may be restricted by policy.', 'hint'))
        layout.addWidget(panel)
        layout.addWidget(row(self.button('Find devices', self.find_devices),
                             self.button('Publish discovery', self.publish_device),
                             self.button('List remote shares', self.remote_shares)))
        self.device_table = self.table(['Name', 'IPv4', 'Status'])
        self.device_table.itemSelectionChanged.connect(self.choose_peer)
        self.devices_data: list[dict] = []
        layout.addWidget(self.device_table)
        self.remote_table = self.table(['Name', 'Description'])
        self.remote_data: list[dict] = []
        self.remote_table.itemSelectionChanged.connect(self.choose_share)
        layout.addWidget(self.remote_table)
        layout.addWidget(self.button('Browse remote folder', self.browse_remote))
        self.connection_result = QPlainTextEdit()
        self.connection_result.setReadOnly(True)
        self.connection_result.setMinimumHeight(150)
        layout.addWidget(self.connection_result)
        layout.addWidget(label('Optional discovery: UDP 47623, disabled by default. A Windows firewall prompt may require explicit local approval. '
                               'No firewall setting is silently changed. Manual IP/UNC works without discovery.', 'hint'))

    def _local_endpoint(self) -> tuple[str, int]:
        adapter = self.selected_adapter()
        if adapter['status'] != 'Up' or not adapter['ipv4']:
            raise ValidationError('اختر محولًا متصلًا وله IPv4 في صفحة الشبكة.')
        address = adapter['ipv4'][0]
        return address['ip'], int(address['prefix'])

    def find_devices(self) -> None:
        try:
            ip, prefix = self._local_endpoint()
            def show(result):
                self.devices_data = result
                self.fill(self.device_table, [[x['name'], x['ip'], x['trust']] for x in result])
                if not result:
                    self.connection_result.setPlainText('لم يظهر جهاز. فعّل الاكتشاف على الطرف الآخر أو استخدم IP يدويًا. / No advertisements received.')
            self.submit(lambda _: discovery.discover(ip, prefix, self.service.node_id), show)
        except Exception as exc:
            self.error(str(exc))

    def publish_device(self) -> None:
        try:
            if self.service.discovery.running:
                self.service.discovery.close()
                self.info('Discovery disabled / تم إيقاف الإعلان.')
            else:
                ip, prefix = self._local_endpoint()
                self.service.discovery.start(ip, prefix)
                self.info('Discovery enabled for this session/subnet only. No shares or control channel advertised.\nتم تفعيل الإعلان لهذه الجلسة فقط.')
        except Exception as exc:
            self.error(str(exc))

    def choose_peer(self) -> None:
        index = self.device_table.currentRow()
        if 0 <= index < len(self.devices_data):
            self.peer.setText(self.devices_data[index]['ip'])

    def choose_share(self) -> None:
        index = self.remote_table.currentRow()
        if 0 <= index < len(self.remote_data):
            self.unc.setText('\\\\' + self.peer.text().strip() + '\\' + self.remote_data[index]['name'])

    def connect_share(self) -> None:
        try:
            unc = self.unc.text().strip()
            validate_unc(unc)
            dialog = CredentialDialog(unc, self.language, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            account, password = dialog.username.text().strip(), dialog.password.text()
            remember = dialog.remember.isChecked()
            dialog.password.clear()
            creds = Credentials(account, password) if account or password else None
            def connect(_):
                try:
                    return self.service.credentials.connect(unc, creds, remember=remember)
                finally:
                    if creds:
                        creds.password = ''
            self.submit(connect, lambda _: self.info('SMB session connected / تم الاتصال بالمشاركة.'))
        except Exception as exc:
            self.error(str(exc))

    def forget_login(self) -> None:
        try:
            server = host(self.peer.text().strip())
            self.submit(lambda _: self.service.credentials.forget(server), lambda _: self.info('Saved credential removed. Existing Windows sessions remain connected.'))
        except Exception as exc:
            self.error(str(exc))

    def test_connection(self) -> None:
        try:
            peer = host(self.peer.text().strip())
            adapter = self.selected_adapter() if self.adapter_table.currentRow() >= 0 else None
            unc = self.unc.text().strip()
            self.submit(lambda _: connectivity(peer, adapter=adapter, unc=unc),
                        lambda data: self.connection_result.setPlainText(json.dumps(data, ensure_ascii=False, indent=2)))
        except Exception as exc:
            self.error(str(exc))

    def remote_shares(self) -> None:
        try:
            server = host(self.peer.text().strip())
            def show(data):
                self.remote_data = data
                self.fill(self.remote_table, [[x['name'], x['remark']] for x in data])
            self.submit(lambda _: self.service.credentials.shares(server), show)
        except Exception as exc:
            self.error(str(exc))

    def browse_remote(self) -> None:
        try:
            unc = self.unc.text().strip()
            validate_unc(unc)
            path = QFileDialog.getExistingDirectory(self, self.t('Browse remote folder'), unc)
            if path:
                mode, ok = QInputDialog.getItem(self, 'Use folder', 'Use this authorized remote folder as:',
                                               [self.t('Source selection'), self.t('Destination')], 0, False)
                if ok:
                    if mode == self.t('Source selection'):
                        self.add_paths([path])
                    else:
                        self.destination.setText(path)
                    self.navigate(4)
        except Exception as exc:
            self.error(str(exc))

    def shares_page(self, layout: QVBoxLayout) -> None:
        layout.addWidget(label('SMB 2/3 • Share permissions ∩ NTFS ACL', 'eyebrow'))
        self.share_table = self.table(['Name', 'Path', 'Access', 'Status'])
        layout.addWidget(self.share_table)
        layout.addWidget(row(self.button('Refresh', self.refresh_shares),
                             self.button('Create share', self.create_share, primary=True, sensitive=True),
                             self.button('Copy UNC', self.copy_unc)))
        layout.addWidget(row(self.button('Open folder', self.open_share),
                             self.button('Remove app share', self.remove_share, sensitive=True),
                             self.button('Restore saved change', self.restore_change, sensitive=True)))
        panel, inner = card(self.t('Security by Windows'))
        inner.addWidget(label('Read-only is the default. Select a specific authorized Windows account. '
                              'Enabling Write changes Share permission to Change; it does not override NTFS denies.\n'
                              'للاستقبال على هذا الجهاز: أنشئ مشاركة Write للمجلد الذي اخترته كوجهة. '
                              'للإرسال منه: تكفي مشاركة Read. لا توجد إدارة آلية للجهاز الآخر.', 'muted'))
        inner.addWidget(self.button('Allow SMB peer', self.allow_smb, sensitive=True))
        layout.addWidget(panel)

    def refresh_shares(self) -> None:
        def show(data):
            self.share_data = data
            self.fill(self.share_table, [[x['name'], x['path'], '; '.join(f"{a['account']}: {a['right']} ({a['type']})" for a in x['access']),
                                          'App-owned' if (x['description'] or '').startswith('LANBridgePro:') else 'Windows / other'] for x in data])
        self.submit(lambda _: self.service.shares(), show)

    def create_share(self) -> None:
        dialog = ShareDialog(self.language, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            payload = self.service.new_share(dialog.path.text().strip(), dialog.account.text().strip(),
                                             dialog.write.isChecked(), dialog.ntfs.isChecked())
            if not self.confirm(json.dumps(payload, ensure_ascii=False, indent=2)):
                return
            def done(result):
                unc = f"\\\\{socket.gethostname()}\\{payload['name']}"
                QApplication.clipboard().setText(unc)
                self.info('UNC copied / تم نسخ مسار المشاركة:\n' + unc)
                self.refresh_shares()
            self.submit(lambda _: self.service.local_change('share_create', payload), done, critical=True)
        except Exception as exc:
            self.error(str(exc))

    def selected_share(self) -> dict:
        index = self.share_table.currentRow()
        if not 0 <= index < len(self.share_data):
            raise ValidationError('اختر مشاركة من الجدول. / Select a share.')
        return self.share_data[index]

    def copy_unc(self) -> None:
        try:
            share = self.selected_share()
            QApplication.clipboard().setText(f"\\\\{socket.gethostname()}\\{share['name']}")
            self.statusBar().showMessage('UNC copied', 4000)
        except Exception as exc:
            self.error(str(exc))

    def open_share(self) -> None:
        try:
            self.open_path(Path(self.selected_share()['path']))
        except Exception as exc:
            self.error(str(exc))

    def remove_share(self) -> None:
        try:
            share = self.selected_share()
            match = next((x for x in self.service.store.operations() if x['action'] == 'share_create'
                          and json.loads(x['payload'])['name'] == share['name']), None)
            if not match:
                raise ValidationError('Only app-created shares with a saved snapshot may be removed here.')
            self.submit(lambda _: self.service.rollback(match), lambda _: self.refresh_shares(), critical=True)
        except Exception as exc:
            self.error(str(exc))

    def transfer_page(self, layout: QVBoxLayout) -> None:
        inputs, box = card(self.t('New session'))
        self.transfer_inputs = inputs
        layout.addWidget(self.button('Show / hide source selection', lambda: inputs.setVisible(not inputs.isVisible())))
        self.flow = QComboBox()
        self.flow.addItems([self.t('Send: local → remote share'), self.t('Receive: remote share → local')])
        box.addWidget(label(self.t('Sender / Receiver workflow'), 'muted'))
        box.addWidget(self.flow)
        self.flow_hint = label('', 'hint')
        box.addWidget(self.flow_hint)
        box.addWidget(label(self.t('Source selection'), 'cardTitle'))
        self.sources = QListWidget()
        self.sources.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.sources.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.sources.setMinimumHeight(115)
        box.addWidget(self.sources)
        box.addWidget(row(self.button('Add files', self.add_files, sensitive=True),
                          self.button('Add folders', self.add_folders, sensitive=True),
                          self.button('Remove selected', self.remove_paths, sensitive=True)))
        self.destination = ltr(QLineEdit())
        self.destination.setPlaceholderText(r'D:\Received  /  \\PC-B\LANBridge_abc123')
        self.flow.currentIndexChanged.connect(self.flow_changed)
        self.flow_changed(0)
        box.addWidget(label(self.t('Destination'), 'cardTitle'))
        box.addWidget(row(self.destination, self.button('Browse', self.choose_destination, sensitive=True)))
        self.copy_mode = QComboBox()
        self.copy_mode.addItems([self.t('Copy folder itself'), self.t('Copy contents only')])
        box.addWidget(self.copy_mode)
        self._sensitive += [self.sources, self.destination, self.copy_mode, self.flow]
        box.addWidget(label('Copy only • no /MIR, /PURGE, /MOVE • destination-only files are retained.\n'
                            'تغيير الاتجاه يتم باختيار المصدر والوجهة المناسبين؛ نفس التطبيق يعمل على أي طرف. '
                            'الملفات المختلفة الموجودة في الوجهة قد تُستبدل بعد معاينتك.', 'hint'))
        layout.addWidget(inputs)
        layout.addWidget(row(self.button('Preflight preview', self.preflight, sensitive=True),
                             self.button('Dry run', lambda: self.start_transfer(dry=True), sensitive=True),
                             self.button('Start transfer', self.start_transfer, primary=True, sensitive=True, image='transfer')))
        self.pause_button = self.button('Pause safely', self.pause_transfer)
        self.cancel_button = self.button('Cancel task', lambda: self.pause_transfer(cancel=True))
        self.force_button = self.button('Force stop', self.force_stop)
        self.force_button.setEnabled(False)
        layout.addWidget(row(self.pause_button, self.cancel_button, self.force_button))
        panel, box = card(self.t('Overall progress'))
        self.progress_title = label(self.t('Ready'), 'cardTitle')
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        self.progress_detail = label('—', 'hint')
        box.addWidget(self.progress_title)
        box.addWidget(self.progress_bar)
        box.addWidget(self.progress_detail)
        self.metrics: dict[str, Metric] = {}
        metric_grid = QGridLayout()
        keys = ['Total size', 'Matched size', 'Remaining size', 'Total files', 'Completed files',
                'Skipped files', 'Failed files', 'Verified files', 'Current speed (estimated)',
                'Average speed (estimated)', 'Elapsed', 'ETA (estimated)']
        for i, key in enumerate(keys):
            widget = Metric(self.t(key))
            widget.value.setStyleSheet('font-size: 14pt; font-weight: 700;')
            metric_grid.addWidget(widget, i // 3, i % 3)
            self.metrics[key] = widget
        box.addLayout(metric_grid)
        self.scope = label('—', 'hint')
        self.scope.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        box.addWidget(label(self.t('Current scope / verifying file'), 'muted'))
        box.addWidget(self.scope)
        self.chart = ActivityChart()
        box.addWidget(self.chart)
        self.pc_scope = label('—', 'hint')
        self.pc_scope.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        box.addWidget(self.pc_scope)
        layout.addWidget(panel)

    def add_paths(self, paths: list[str]) -> None:
        existing = {self.sources.item(i).text().casefold() for i in range(self.sources.count())}
        for path in paths:
            if path.casefold() not in existing:
                item = QListWidgetItem(path)
                item.setToolTip(path)
                self.sources.addItem(item)
                existing.add(path.casefold())

    def add_files(self) -> None:
        start = self.unc.text().strip() if self.flow.currentIndex() == 1 else ''
        paths, _ = QFileDialog.getOpenFileNames(self, self.t('Add files'), start)
        self.add_paths(paths)

    def add_folders(self) -> None:
        dialog = QFileDialog(self, self.t('Add folders'))
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        if self.flow.currentIndex() == 1 and self.unc.text().strip():
            dialog.setDirectory(self.unc.text().strip())
        for view_type in (QListView, QTreeView):
            for view in dialog.findChildren(view_type):
                view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_paths(dialog.selectedFiles())

    def flow_changed(self, index: int) -> None:
        if index == 0:
            self.flow_hint.setText('الإرسال: اختر ملفاتك المحلية، واجعل الوجهة مشاركة Write أنشأها مستخدم الجهاز الآخر.\n'
                                   'Send: choose local sources and an authorized writable remote share.')
            self.destination.setPlaceholderText(r'\\PC-B\LANBridge_abc123')
        else:
            self.flow_hint.setText('الاستقبال: اختر مصدرًا من مشاركة Read على الجهاز الآخر، ووجهة محلية على هذا الجهاز.\n'
                                   'Receive: choose an authorized readable remote source and a local destination.')
            self.destination.setPlaceholderText(r'D:\Received')

    def remove_paths(self) -> None:
        for item in self.sources.selectedItems():
            self.sources.takeItem(self.sources.row(item))

    def choose_destination(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.t('Destination'), self.destination.text())
        if path:
            self.destination.setText(path)

    def current_spec(self) -> TaskSpec:
        sources = [self.sources.item(i).text() for i in range(self.sources.count())]
        if not sources or not self.destination.text().strip():
            raise ValidationError('اختر مصدرًا واحدًا على الأقل ومجلد الوجهة. / Select sources and a destination.')
        settings = self.transfer_settings()
        trial = TaskSpec(tuple(sources), self.destination.text().strip(), self.copy_mode.currentIndex() == 0, settings)
        if self.task and dataclasses.replace(trial, id=self.task.id) == self.task:
            return self.task
        self.service.store.create(trial)
        self.task = trial
        return trial

    def transfer_settings(self) -> TransferSettings:
        return TransferSettings(threads=self.threads.value(), hash_verify=self.hash_check.isChecked(),
                                fat_times=self.fat_check.isChecked(), rate_mib=self.rate_limit.value())

    def preflight(self) -> None:
        try:
            spec = self.current_spec()
            self.submit(lambda notify: self.service.prepare(spec, notify),
                        lambda result: self.info(self.summary(result)), critical=True, progress=True)
        except Exception as exc:
            self.error(str(exc))

    def summary(self, result) -> str:
        source_summary = '\n\n'.join(f"{x['name']} | {x['type']} | {size_text(x['bytes'])} | {x['files']:,} files\n{x['path']}\n→ {x['destination']}" for x in result.sources)
        return (f'{self.t("Total files")}: {result.total_files:,}\n{self.t("Total size")}: {size_text(result.total_bytes)}\n'
                f'Already matching: {size_text(result.matched_bytes)}\nFree: {size_text(result.free_bytes)}\n'
                f'Filesystem: {result.filesystem}\nExtra destination entries kept: {result.extras}\n'
                f'Excluded reparse/special entries: {result.excluded}\n\n' + '\n'.join(result.warnings)
                + '\n\nSources / المصادر:\n' + source_summary)

    def start_transfer(self, *, dry: bool = False, spec: TaskSpec | None = None) -> None:
        try:
            spec = spec or self.current_spec()
            self.task = spec
            def prepared(result):
                if not dry and not self.confirm(self.summary(result) + '\n\nStart copy and allow replacement of differing destination files?\nبدء النسخ مع السماح باستبدال الملفات المختلفة؟'):
                    return
                self.chart.values.clear()
                self.transfer_inputs.setVisible(False)
                self.submit(lambda notify: self.service.transfer(spec, notify, dry=dry, preflight_done=True),
                            critical=True, progress=True)
            self.submit(lambda notify: self.service.prepare(spec, notify), prepared, critical=True, progress=True)
        except Exception as exc:
            self.error(str(exc))

    def pause_transfer(self, *, cancel=False) -> None:
        if not self.service.engine.active:
            return
        self.service.engine.stop(cancel=cancel)
        self.progress_title.setText(self.t('stopping'))
        self.progress_detail.setText('Waiting for Robocopy to exit; completed files remain. / انتظار خروج المحرك؛ الملفات المكتملة محفوظة.')
        QTimer.singleShot(15000, lambda: self.force_button.setEnabled(self.service.engine.active))

    def force_stop(self) -> None:
        if self.confirm('Robocopy has not exited yet. Force stopping can leave a partial file that must be rechecked or recopied.\n'
                        'هل تريد إنهاء العملية بالقوة؟ الملفات المكتملة لن تُحذف.'):
            self.service.engine.stop()
            self.service.engine.runner.force_stop()

    def update_progress(self, progress: Progress) -> None:
        self.progress_title.setText(f'{self.t(progress.state)}  •  {progress.percent:.1f}%')
        self.progress_bar.setValue(int(progress.percent * 10))
        self.progress_detail.setText(progress.message)
        values = {
            'Total size': size_text(progress.total_bytes), 'Matched size': size_text(progress.matched_bytes),
            'Remaining size': size_text(max(0, progress.total_bytes - progress.matched_bytes)),
            'Total files': f'{progress.total_files:,}', 'Completed files': f'{progress.matched_files:,}',
            'Skipped files': f'{progress.skipped_files:,}', 'Failed files': f'{progress.failed_files:,}',
            'Verified files': f'{progress.verified_files:,}', 'Current speed (estimated)': size_text(progress.rate) + '/s',
            'Average speed (estimated)': size_text(progress.average_rate) + '/s', 'Elapsed': time_text(progress.elapsed),
            'ETA (estimated)': time_text(progress.eta),
        }
        for key, value in values.items():
            self.metrics[key].value.setText(value)
        self.scope.setText(progress.current_file or '—')
        self.chart.add(progress.rate)
        if self.task:
            def computer(path: str) -> str:
                if path.startswith('\\\\'):
                    return path[2:].split('\\')[0]
                return socket.gethostname()
            source_pcs = sorted({computer(p) for p in self.task.sources})
            self.pc_scope.setText('Source: ' + ', '.join(source_pcs) + '    →    Destination: ' + computer(self.task.destination))
        if progress.final:
            self.force_button.setEnabled(False)
            self.refresh_history()
            if self.service.store.get_setting('notifications', True):
                self.tray.showMessage('LANBridge Pro', self.t(progress.state) + '\n' + progress.message,
                                      QSystemTrayIcon.MessageIcon.Information, 6000)

    def history_page(self, layout: QVBoxLayout) -> None:
        self.history_table = self.table(['Task', 'Status', 'Updated', 'Destination', 'Message'])
        layout.addWidget(self.history_table)
        layout.addWidget(row(self.button('Refresh', self.refresh_history),
                             self.button('Resume selected', self.resume_selected, primary=True, sensitive=True),
                             self.button('Retry failed files', self.retry_failed, sensitive=True)))
        layout.addWidget(row(self.button('Session logs', self.session_logs),
                             self.button('Verified move cleanup', self.move_cleanup, sensitive=True),
                             self.button('Open logs', lambda: self.open_path(self.service.data_dir / 'logs'))))
        layout.addWidget(label('Resume rebuilds the source inventory and launches Robocopy /Z. Completed files with matching metadata are skipped. '
                               'An interrupted file from another tool may need a full recopy.\n'
                               'بعد إغلاق الجهازين: افتحهما، تأكد من العناوين والمشاركة والاعتماد، ثم اختر المهمة واضغط استكمال.', 'hint'))

    def refresh_history(self) -> None:
        self.history_data = self.service.store.tasks()
        self.fill(self.history_table, [[x['id'][:8], self.t(x['state']),
            dt.datetime.fromtimestamp(x['updated']).strftime('%Y-%m-%d %H:%M'),
            json.loads(x['spec'])['destination'], x['message']] for x in self.history_data])
        recoverable = sum(x['state'] != TaskState.COMPLETED for x in self.history_data)
        self.dashboard_metrics[1].value.setText(str(len(self.history_data)))
        self.dashboard_metrics[2].value.setText(str(recoverable))

    def selected_task(self) -> TaskSpec:
        index = self.history_table.currentRow()
        if not 0 <= index < len(self.history_data):
            raise ValidationError('اختر مهمة من السجل. / Select a task from history.')
        return self.service.store.spec(self.history_data[index]['id'])

    def load_task(self, spec: TaskSpec) -> None:
        self.task = spec
        self.transfer_inputs.setVisible(True)
        self.sources.clear()
        self.add_paths(list(spec.sources))
        self.destination.setText(spec.destination)
        self.copy_mode.setCurrentIndex(0 if spec.folder_itself else 1)
        self.threads.setValue(spec.settings.threads)
        self.hash_check.setChecked(spec.settings.hash_verify)
        self.fat_check.setChecked(spec.settings.fat_times)
        self.rate_limit.setValue(spec.settings.rate_mib)

    def resume_selected(self) -> None:
        try:
            spec = self.selected_task()
            self.load_task(spec)
            self.navigate(4)
            self.start_transfer(spec=spec)
        except Exception as exc:
            self.error(str(exc))

    def retry_failed(self) -> None:
        try:
            spec = self.selected_task()
            if not self.confirm('Retry only recorded failed files, then verify the whole inventory.\n'
                                'If the source changed after the original scan, use Resume instead. Existing differing files may be replaced.'):
                return
            self.load_task(spec)
            self.navigate(4)
            self.submit(lambda notify: self.service.transfer(spec, notify, failed_only=True), critical=True, progress=True)
        except Exception as exc:
            self.error(str(exc))

    def session_logs(self) -> None:
        try:
            spec = self.selected_task()
            sessions = self.service.store.sessions(spec.id)
            if not sessions:
                self.info('No Robocopy sessions yet.')
                return
            names = [f"{x['id']} | exit={x['exit_code']} | {x['note']} | {Path(x['log']).name}" for x in sessions]
            selected, ok = QInputDialog.getItem(self, self.t('Session logs'), 'Unicode Robocopy log:', names, 0, False)
            if ok:
                self.open_path(Path(sessions[names.index(selected)]['log']))
        except Exception as exc:
            self.error(str(exc))

    def move_cleanup(self) -> None:
        try:
            from lanbridge.transfer.move import verified_cleanup
            spec = self.selected_task()
            phrase, ok = QInputDialog.getText(self, self.t('Verified move cleanup'),
                'Deletes SOURCE FILES only after a fresh locked-handle SHA-256 check of BOTH copies.\n'
                'Folders are kept. This action cannot be undone by LANBridge. Windows source only.\n'
                'سيحذف ملفات المصدر بعد التحقق. اكتب DELETE VERIFIED SOURCES للتأكيد:')
            if not ok or phrase != 'DELETE VERIFIED SOURCES':
                return
            def clean(_):
                with self.service.exclusive():
                    return verified_cleanup(self.service.store, spec)
            self.submit(clean, lambda result: self.info(json.dumps(result, ensure_ascii=False, indent=2)), critical=True)
        except Exception as exc:
            self.error(str(exc))

    def diagnostics_page(self, layout: QVBoxLayout) -> None:
        layout.addWidget(row(self.button('Run diagnostics', self.run_diagnostics, primary=True),
                             self.button('Export report', self.export_report),
                             self.button('Open logs', lambda: self.open_path(self.service.data_dir / 'logs'))))
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.report_view.setMinimumHeight(520)
        layout.addWidget(self.report_view)
        layout.addWidget(label('التقرير يوضح Link/Subnet/Ping/TCP445/SMB وأذونات القراءة وسياسات Windows. '
                               'زر تشغيل التشخيص يعيد الاختبار بعد إصلاح الخطأ. لا يتم تخفيف سياسة أمان تلقائيًا.\n'
                               'Reports contain local paths, computer names and network settings; review before sharing.', 'hint'))
        self.write_probe = QCheckBox('اختبار الكتابة في مسار UNC المحدد (إنشاء وحذف ملف مؤقت) / Write permission probe')
        layout.addWidget(self.write_probe)

    def run_diagnostics(self) -> None:
        peer, unc = self.peer.text().strip(), self.unc.text().strip()
        adapter = self.selected_adapter() if self.adapter_table.currentRow() >= 0 else None
        write_test = self.write_probe.isChecked()
        def run(_):
            result = self.service.diagnostic_report(peer, adapter, unc)
            if write_test and peer and unc:
                result['write_probe'] = connectivity(peer, adapter=adapter, unc=unc, write_test=True)
            return result
        def show(result):
            self.last_report = result
            self.report_view.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.submit(run, show)

    def export_report(self) -> None:
        if self.last_report is None:
            self.info('Run Diagnostics first / شغّل التشخيص أولًا.')
            return
        path, _ = QFileDialog.getSaveFileName(self, self.t('Export report'), 'LANBridge-Diagnostics.json', 'JSON (*.json)')
        if path:
            try:
                Path(path).write_text(json.dumps(self.last_report, ensure_ascii=False, indent=2), encoding='utf-8')
                self.statusBar().showMessage('Report saved', 4000)
            except OSError as exc:
                self.error(str(exc))

    def settings_page(self, layout: QVBoxLayout) -> None:
        panel, box = card(self.t('Settings'))
        self.language_box = QComboBox()
        self.language_box.addItems(['العربية — RTL', 'English — LTR'])
        self.language_box.setCurrentIndex(0 if self.language == 'ar' else 1)
        self.theme_box = QComboBox()
        self.theme_box.addItems([self.t('Dark'), self.t('Light')])
        self.theme_box.setCurrentIndex(0 if self.theme == 'dark' else 1)
        self.threads = QSpinBox()
        self.threads.setRange(1, 128)
        self.threads.setValue(self.service.store.get_setting('threads', 8))
        self.rate_limit = QSpinBox()
        self.rate_limit.setRange(0, 10000)
        self.rate_limit.setValue(self.service.store.get_setting('rate_mib', 0))
        self.hash_check = QCheckBox(self.t('SHA-256 verification'))
        self.hash_check.setChecked(self.service.store.get_setting('hash_verify', False))
        self.fat_check = QCheckBox(self.t('FAT timestamp tolerance'))
        self.fat_check.setChecked(self.service.store.get_setting('fat_times', False))
        self.notifications = QCheckBox(self.t('Notifications'))
        self.notifications.setChecked(self.service.store.get_setting('notifications', True))
        self.recovery_prompt = QCheckBox(self.t('Recovery prompt on startup'))
        self.recovery_prompt.setChecked(self.service.store.get_setting('recovery_prompt', True))
        for key, widget in [('Language', self.language_box), ('Theme', self.theme_box),
                            ('Threads', self.threads), ('Speed limit MiB/s (0 = unlimited)', self.rate_limit)]:
            box.addWidget(label(self.t(key), 'muted'))
            box.addWidget(widget)
        for widget in [self.hash_check, self.fat_check, self.notifications, self.recovery_prompt]:
            box.addWidget(widget)
        box.addWidget(label('8 threads is a balanced default. More threads may help SSDs and many small files; HDDs can slow down.\n'
                            'SHA-256 reads both copies and adds substantial disk/network time. /IORATE is optional and requires support in the installed Robocopy.\n'
                            'Recovery offers a prompt after restart; no scheduled task, automatic network changes or unattended source deletion is installed.', 'hint'))
        box.addWidget(self.button('Save settings', self.save_settings, primary=True, sensitive=True))
        self._sensitive += [self.threads, self.rate_limit, self.hash_check, self.fat_check]
        layout.addWidget(panel)
        layout.addWidget(self.button('Open logs', lambda: self.open_path(self.service.data_dir / 'logs')))

    def save_settings(self) -> None:
        language = 'ar' if self.language_box.currentIndex() == 0 else 'en'
        theme = 'dark' if self.theme_box.currentIndex() == 0 else 'light'
        for key, value in {'language': language, 'theme': theme, 'threads': self.threads.value(),
                           'rate_mib': self.rate_limit.value(), 'hash_verify': self.hash_check.isChecked(),
                           'fat_times': self.fat_check.isChecked(), 'notifications': self.notifications.isChecked(),
                           'recovery_prompt': self.recovery_prompt.isChecked()}.items():
            self.service.store.setting(key, value)
        self.theme = theme
        QApplication.instance().setStyleSheet(stylesheet(theme))
        self.info('تم حفظ الإعدادات. تغيير اللغة يظهر بعد إعادة تشغيل التطبيق.\nSettings saved. Language changes apply on the next launch.')

    def submit(self, fn, result=None, *, critical=False, progress=False) -> None:
        worker = Worker(fn)
        self._workers.add(worker)
        if critical:
            self._critical_jobs += 1
            self._update_busy()
        if result:
            worker.signals.result.connect(result)
        worker.signals.error.connect(self.error)
        if progress:
            worker.signals.progress.connect(self.update_progress)
        def finished():
            self._workers.discard(worker)
            if critical:
                self._critical_jobs -= 1
                self._update_busy()
            if self._close_pending and not self._critical_jobs:
                self.close()
        worker.signals.finished.connect(finished)
        self.pool.start(worker)

    def _update_busy(self) -> None:
        enabled = self._critical_jobs == 0
        for widget in self._sensitive:
            widget.setEnabled(enabled)
        if enabled:
            self.refresh_history()

    def confirm(self, message: str) -> bool:
        return PreviewDialog(message, self.language, self, confirmation=True).exec() == QDialog.DialogCode.Accepted

    def error(self, error: str | BaseException) -> None:
        if isinstance(error, CredentialError) and error.auto_prompt:
            self.unc.setText(error.unc)
            self.peer.setText(validate_unc(error.unc)[0])
            self.navigate(2)
            self.connection_result.setPlainText(str(error) + '\nبعد الاتصال: عد للمهمة واضغط بدء/استكمال. / After connecting, retry the task.')
            self.connect_share()
            return
        message = str(error)
        if self.service.engine.stop_event.is_set() and 'stopped' in message.casefold():
            self.progress_detail.setText(message)
            return
        QMessageBox.warning(self, 'LANBridge Pro — تعذر إكمال العملية', message)

    def info(self, message: str) -> None:
        PreviewDialog(message, self.language, self).exec()

    @staticmethod
    def open_path(path: Path) -> None:
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _recovery_notice(self) -> None:
        self.navigate(5)
        self.info(f'{self.service.recovered} interrupted task(s) recovered.\n'
                  'تم العثور على مهام غير مكتملة. أعد توصيل الجهازين ثم اختر المهمة واضغط استكمال.')

    def closeEvent(self, event) -> None:
        if self._critical_jobs:
            if self.service.engine.active and self.confirm('Stop safely and close after the copy process exits?\nإيقاف آمن ثم إغلاق البرنامج؟'):
                self._close_pending = True
                self.service.engine.stop()
            else:
                self.statusBar().showMessage('Wait for the active operation before closing.')
            event.ignore()
            return
        self.service.discovery.close()
        self.tray.hide()
        # Read-only workers have short OS timeouts. Avoid deleting their signal objects while running.
        if self._workers:
            self._close_pending = True
            event.ignore()
            return
        super().closeEvent(event)
