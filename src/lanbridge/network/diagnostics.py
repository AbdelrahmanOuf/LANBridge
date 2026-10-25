from __future__ import annotations

import dataclasses
import os
import socket
import subprocess
import tempfile

from lanbridge.core.paths import validate_unc
from lanbridge.platform.powershell import PowerShell
from lanbridge.platform.windows_paths import system_directory
from lanbridge.security.credentials import classify_error

from .validation import host, same_subnet


@dataclasses.dataclass(slots=True)
class Check:
    name: str
    status: str
    detail: str
    remedy: str = ''


def as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def connectivity(peer: str, *, adapter: dict | None = None, unc: str = '',
                 write_test: bool = False) -> list[dict]:
    peer = host(peer)
    result: list[Check] = []
    if adapter:
        up = adapter.get('status') == 'Up'
        result.append(Check('Ethernet link', 'ok' if up else 'error',
                            f"{adapter.get('name')} • {adapter.get('status')} • {adapter.get('speed')}",
                            '' if up else 'الكابل مفصول أو المحول متوقف: راجع الكابل والمنفذ وحالة المحول.'))
    try:
        remote_ip = socket.gethostbyname(peer)
        result.append(Check('Name / IP', 'ok', remote_ip))
    except OSError:
        return [dataclasses.asdict(Check('Name / IP', 'error', 'تعذر تحديد عنوان الجهاز.',
                                        'راجع اسم الجهاز أو اكتب IPv4 الصحيح.'))]
    if adapter:
        ips = as_list(adapter.get('ipv4'))
        compatible = any(same_subnet(x['ip'], int(x['prefix']), remote_ip) for x in ips)
        result.append(Check('Subnet', 'ok' if compatible else 'warning',
                            'العنوانان داخل شبكة واحدة.' if compatible else 'العنوانان في شبكتين مختلفتين.',
                            '' if compatible else 'في الكابل المباشر استخدم عنوانين مختلفين من نفس Subnet؛ عبر Router قد توجد Route صالحة.'))
    if os.name == 'nt':
        ping = system_directory() / 'ping.exe'
        code = subprocess.run([str(ping), '-n', '1', '-w', '1200', remote_ip], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                              timeout=4, check=False).returncode
        result.append(Check('ICMP Ping', 'ok' if code == 0 else 'warning',
                            'رد الجهاز.' if code == 0 else 'لا يوجد رد Ping؛ هذا وحده لا يثبت فشل الشبكة.',
                            '' if code == 0 else 'استمر في اختبار TCP 445؛ قد يكون ICMP محظورًا.'))
    try:
        with socket.create_connection((remote_ip, 445), timeout=2):
            result.append(Check('TCP 445', 'ok', 'SMB TCP port reachable.'))
    except OSError:
        result.append(Check('TCP 445', 'error', 'منفذ SMB غير متاح.',
                            'راجع الكابل والعناوين وخدمة Server وFirewall/Profile. الحظر احتمال، وليس نتيجة مؤكدة من اختبار واحد.'))
    if unc:
        server, _ = validate_unc(unc)
        if socket.gethostbyname(server) != remote_ip:
            raise ValueError('The UNC server must match the device being diagnosed.')
        try:
            with os.scandir(unc) as items:
                next(items, None)
            result.append(Check('SMB share / read', 'ok', 'تم الوصول إلى المشاركة باستخدام جلسة Windows الحالية.'))
            if write_test:
                with tempfile.NamedTemporaryFile(prefix='.lanbridge-permission-', dir=unc):
                    pass
                result.append(Check('SMB write', 'ok', 'نجح إنشاء وحذف ملف اختبار مؤقت.'))
        except OSError as exc:
            result.append(Check('SMB share access', 'error', classify_error(getattr(exc, 'winerror', 5) or 5),
                                'سجّل الدخول بحساب مصرح له ثم راجع Share Permissions وNTFS ACL واسم المشاركة.'))
    return [dataclasses.asdict(x) for x in result]


def report(ps: PowerShell, errors: list[dict], peer: str = '', adapter: dict | None = None,
           unc: str = '') -> dict:
    data = ps.call('diagnostics')
    checks = connectivity(peer, adapter=adapter, unc=unc) if peer else []
    return {'format': 'LANBridgePro diagnostics v1', 'local': data, 'checks': checks,
            'task_errors': errors,
            'policy_note': 'Reported policy settings are observations. Domain effective policy may need an administrator. Signing/guest settings were not changed.'}
