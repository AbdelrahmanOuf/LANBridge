from __future__ import annotations

import os
from dataclasses import dataclass, field

from lanbridge.core.errors import CredentialError, PlatformError
from lanbridge.core.paths import validate_unc


@dataclass(repr=False, slots=True)
class Credentials:
    username: str
    password: str = field(repr=False)


def classify_error(code: int) -> str:
    return {
        5: 'Access denied / تم رفض الوصول. Check both Share and NTFS permissions.',
        53: 'Network path unavailable / مسار الشبكة غير متاح. Check IP, cable, profile and TCP 445.',
        67: 'Share not found / المشاركة غير موجودة. Check the exact share name.',
        86: 'Incorrect password / كلمة المرور غير صحيحة.',
        1219: 'Another account is already connected to this server / اتصال بحساب آخر. Disconnect it in Windows before retrying; the app will not remove unrelated connections.',
        1326: 'Credentials required or incorrect / بيانات اعتماد مطلوبة أو غير صحيحة. Use PC-NAME\\user or DOMAIN\\user and the account password, not the Windows Hello PIN.',
        1330: 'Account password has expired / انتهت صلاحية كلمة المرور.',
        1385: 'Windows logon policy denies this account / سياسة Windows تمنع الدخول عبر الشبكة. Contact the administrator.',
        1272: 'Guest/SMB policy restriction / قيود أمان SMB. Use an authorized account; signing and guest policies will not be weakened.',
    }.get(code, f'Windows SMB error {code}. Inspect Diagnostics; policy restrictions may require your administrator.')


class CredentialManager:
    """Secrets go only to Win32 in-process APIs; never argv/PowerShell/SQLite/logs."""

    PREFIX = 'LANBridgePro/SMB/'

    @staticmethod
    def _require_windows() -> None:
        if os.name != 'nt':
            raise PlatformError('Windows Credential Manager requires Windows.')

    def save(self, server: str, credentials: Credentials) -> None:
        self._require_windows()
        import win32cred
        win32cred.CredWrite({'Type': win32cred.CRED_TYPE_GENERIC,
                            'TargetName': self.PREFIX + server.casefold(),
                            'UserName': credentials.username,
                            'CredentialBlob': credentials.password.encode('utf-16-le'),
                            'Persist': win32cred.CRED_PERSIST_LOCAL_MACHINE}, 0)

    def load(self, server: str) -> Credentials | None:
        self._require_windows()
        import pywintypes
        import win32cred
        try:
            data = win32cred.CredRead(self.PREFIX + server.casefold(), win32cred.CRED_TYPE_GENERIC, 0)
            blob = data['CredentialBlob']
            return Credentials(data['UserName'], blob.decode('utf-16-le') if isinstance(blob, bytes) else blob)
        except pywintypes.error as exc:
            if exc.winerror == 1168:
                return None
            raise PlatformError('Windows could not read this saved credential.') from None

    def forget(self, server: str) -> None:
        self._require_windows()
        import pywintypes
        import win32cred
        try:
            win32cred.CredDelete(self.PREFIX + server.casefold(), win32cred.CRED_TYPE_GENERIC, 0)
        except pywintypes.error as exc:
            if exc.winerror != 1168:
                raise PlatformError('Windows could not delete this credential.') from None

    def status(self, server: str) -> dict:
        """Safe metadata only; credential blobs/passwords never leave this service."""
        item = self.load(server)
        if item is None:
            return {'saved_by_app': False, 'note': 'Windows integrated authentication may still be available.'}
        try:
            return {'saved_by_app': True, 'account': item.username, 'storage': 'Windows Credential Manager'}
        finally:
            item.password = ''

    def connect(self, unc: str, credentials: Credentials | None = None, *, remember: bool = False) -> None:
        self._require_windows()
        server, share = validate_unc(unc)
        root = f'\\\\{server}\\{share}'
        import pywintypes
        import win32netcon
        import win32wnet
        if credentials is None:
            credentials = self.load(server)
        try:
            win32wnet.WNetAddConnection2(win32netcon.RESOURCETYPE_DISK, None, root, None,
                                        credentials.username if credentials else None,
                                        credentials.password if credentials else None, 0)
        except pywintypes.error as exc:
            if exc.winerror in (86, 1326):
                raise CredentialError(unc, classify_error(exc.winerror)) from None
            raise PlatformError(classify_error(exc.winerror)) from None
        if remember and credentials:
            self.save(server, credentials)

    def shares(self, server: str) -> list[dict]:
        self._require_windows()
        from lanbridge.network.validation import host
        server = host(server)
        import pywintypes
        import win32net
        try:
            result: list[dict] = []
            resume = 0
            while True:
                rows, _, resume = win32net.NetShareEnum('\\\\' + server, 1, resume)
                result += [{'name': row['netname'], 'remark': row['remark']} for row in rows
                           if row['type'] & 0xFFFF == 0 and not row['netname'].endswith('$')]
                if not resume:
                    return result
        except pywintypes.error as exc:
            raise PlatformError(classify_error(exc.winerror)) from None
