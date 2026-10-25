from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QLineEdit, QPlainTextEdit, QVBoxLayout,
)

from lanbridge.network.validation import static_config

from .i18n import text
from .style import ltr
from .widgets import label, row


class FormDialog(QDialog):
    def __init__(self, title: str, language: str, parent=None):
        super().__init__(parent)
        self.language = language
        self.setWindowTitle(title)
        self.setMinimumWidth(540)
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(24, 24, 24, 24)
        self.layout_box.setSpacing(15)
        self.form = QFormLayout()
        self.form.setSpacing(12)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.layout_box.addLayout(self.form)

    def tr_key(self, key: str) -> str:
        return text(key, self.language)

    def finish(self, hint: str = '') -> None:
        if hint:
            self.layout_box.addWidget(label(hint, 'hint'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr_key('Apply'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr_key('Cancel'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.layout_box.addWidget(buttons)


class NetworkDialog(FormDialog):
    def __init__(self, adapter: dict, language: str, parent=None):
        super().__init__(text('Configure IP', language), language, parent)
        self.adapter = adapter
        self.form.addRow(label(f"{adapter['name']}  •  {adapter['description']}"))
        self.mode = QComboBox()
        for item in ('Automatic DHCP', 'Manual static IP', 'Quick PC A', 'Quick PC B'):
            self.mode.addItem(self.tr_key(item))
        self.ip = ltr(QLineEdit('192.168.50.1'))
        self.prefix = ltr(QLineEdit('24'))
        self.gateway = ltr(QLineEdit())
        self.dns = ltr(QLineEdit())
        for title, field in [('Mode', self.mode), ('IPv4', self.ip), ('Prefix / Mask', self.prefix),
                             ('Gateway', self.gateway), ('DNS (comma-separated)', self.dns)]:
            self.form.addRow(title, field)
        self.explicit = QCheckBox('أوافق على اختيار هذا المحول حتى لو كان Wi-Fi/Virtual. / Explicit non-Ethernet selection')
        self.form.addRow(self.explicit)
        self.mode.currentIndexChanged.connect(self.changed)
        self.changed(0)
        self.finish('يُطبّق التغيير على هذا الجهاز فقط. قد ينقطع الاتصال مؤقتًا. استخدم A على جهاز وB على الآخر.\n'
                    'Snapshots include manual IPv4, default routes, DHCP and DNS mode. IPv6 is not changed.')

    def changed(self, index: int) -> None:
        for widget in (self.ip, self.prefix, self.gateway, self.dns):
            widget.setEnabled(index != 0)
        if index in (2, 3):
            self.ip.setText('192.168.50.1' if index == 2 else '192.168.50.2')
            self.prefix.setText('24')
            self.gateway.clear()
            self.dns.clear()

    def payload(self) -> dict:
        return static_config({'index': self.adapter['index'], 'guid': self.adapter['guid'],
                              'mode': 'dhcp' if self.mode.currentIndex() == 0 else 'static',
                              'ip': self.ip.text().strip(), 'prefix': self.prefix.text().strip(),
                              'gateway': self.gateway.text().strip(),
                              'dns': [x.strip() for x in self.dns.text().split(',') if x.strip()]})


class ShareDialog(FormDialog):
    def __init__(self, language: str, parent=None):
        super().__init__(text('Create share', language), language, parent)
        from PySide6.QtWidgets import QPushButton
        self.path = ltr(QLineEdit())
        browse = QPushButton(self.tr_key('Browse'))
        browse.clicked.connect(self.browse)
        self.account = ltr(QLineEdit())
        self.account.setPlaceholderText(r'PC-NAME\user  /  DOMAIN\user')
        self.write = QCheckBox(self.tr_key('Optional write permission'))
        self.ntfs = QCheckBox(self.tr_key('Grant inheritable NTFS permission'))
        self.form.addRow(self.tr_key('Path'), row(self.path, browse))
        self.form.addRow(self.tr_key('Account'), self.account)
        self.form.addRow(self.write)
        self.form.addRow(self.ntfs)
        self.finish('الافتراضي: Read فقط لحساب مصرح. أذونات Share تختلف عن NTFS؛ الوصول الفعلي يخضع لكليهما.\n'
                    'إذا اخترت ملفًا واحدًا فستُشارك الحافظة التي تحتويه ويمكن للحساب الوصول إلى ملفاتها المسموح بها.\n'
                    'NTFS اختيار مستقل؛ يُحفظ DACL للمجلد ويمكن التراجع بشرط عدم حدوث تغيير خارجي. '
                    'المجلدات ذات الوراثة المحمية قد تحتاج إعدادًا يدويًا.')

    def browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr_key('Browse'))
        if path:
            self.path.setText(path)


class CredentialDialog(FormDialog):
    def __init__(self, unc: str, language: str, parent=None):
        super().__init__(text('Connect', language), language, parent)
        self.form.addRow(label(unc))
        self.username = ltr(QLineEdit())
        self.username.setPlaceholderText(r'PC-B\transferuser  /  DOMAIN\user')
        self.password = ltr(QLineEdit())
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox(self.tr_key('Remember credentials'))
        self.form.addRow(self.tr_key('Account'), self.username)
        self.form.addRow(self.tr_key('Password'), self.password)
        self.form.addRow(self.remember)
        self.finish('اترك الحقول فارغة لاستخدام جلسة Windows أو الاعتماد المحفوظ. استخدم كلمة مرور الحساب، وليس PIN.\n'
                    'الحفظ يتم في Windows Credential Manager لحساب المستخدم الحالي. لا تتغير سياسات SMB/Guest.')


class PreviewDialog(QDialog):
    def __init__(self, message: str, language: str, parent=None, *, confirmation: bool = False):
        super().__init__(parent)
        self.setWindowTitle('LANBridge Pro — Preview / معاينة')
        self.resize(740, 560)
        layout = QVBoxLayout(self)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(message)
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                                  if confirmation else QDialogButtonBox.StandardButton.Close)
        if confirmation:
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText(text('Apply', language))
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(text('Cancel', language))
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
