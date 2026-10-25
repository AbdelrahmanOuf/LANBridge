from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import struct
import subprocess
import sys
import uuid
from pathlib import Path

from lanbridge.core.errors import PlatformError, ValidationError
from lanbridge.core.paths import safe_path
from lanbridge.core.store import Store
from lanbridge.network.validation import ipv4, static_config, validate_adapter
from lanbridge.platform.powershell import PowerShell
from lanbridge.platform.windows_paths import program_files, windows_directory
from lanbridge.transfer.robocopy import helper_command

MAX_PACKET = 1024 * 1024
ACTIONS = {'network_apply', 'network_restore', 'profile_private', 'profile_restore',
           'share_create', 'share_remove', 'firewall_add', 'firewall_remove'}


def validate_action(action: str, payload: dict) -> dict:
    if action not in ACTIONS or not isinstance(payload, dict):
        raise ValidationError("Administrative operation not allowed.")
    if len(json.dumps(payload)) > 64_000:
        raise ValidationError("Administrative request is too large.")
    if action == 'network_apply':
        return static_config(payload)
    if action in ('network_restore', 'profile_restore'):
        s = payload.get('snapshot', {})
        validate_adapter(s)
        if type(s.get('dhcp')) is not bool or len(s.get('ips', [])) > 32 or len(s.get('routes', [])) > 16:
            raise ValidationError("Invalid network snapshot.")
        for address in s.get('ips', []):
            static_config(s | {'mode': 'static', 'ip': address['ip'], 'prefix': address['prefix'], 'gateway': '', 'dns': []})
        for route in s.get('routes', []):
            ipv4(route['gateway'])
            if type(route['metric']) is not int or not 0 <= route['metric'] <= 999999:
                raise ValidationError("Invalid route metric.")
        for dns in s.get('dns', []):
            ipv4(dns)
        if s.get('profile') not in ('', 'Private', 'Public', 'DomainAuthenticated'):
            raise ValidationError("Invalid network profile.")
    if action in ('profile_private', 'firewall_add'):
        validate_adapter(payload)
    if action.startswith('firewall_'):
        uuid.UUID(payload.get('owner', ''))
        if payload.get('rule') != 'LANBridgePro-' + payload['owner']:
            raise ValidationError("Invalid app-owned firewall rule.")
        if action == 'firewall_add':
            ipv4(payload.get('peer', ''))
    if action.startswith('share_'):
        uuid.UUID(payload.get('owner', ''))
        if not re.fullmatch(r'LANBridge_[A-Za-z0-9_]{4,48}', payload.get('name', '')):
            raise ValidationError("Use an app-generated LANBridge_ share name.")
        path = safe_path(payload.get('path', ''), must_exist=True)
        if not path.is_dir() or str(path).startswith('\\\\') or path.parent == path:
            raise ValidationError("A local folder below a drive root is required.")
        if os.name == 'nt':
            protected = [windows_directory(), program_files()]
            from lanbridge.core.paths import contains
            if any(contains(p.resolve(), path) for p in protected):
                raise ValidationError("Windows and Program Files folders cannot be shared by this app.")
        if action == 'share_create':
            account = payload.get('account', '')
            if not isinstance(account, str) or not 1 <= len(account) <= 256 or any(ord(c) < 32 for c in account):
                raise ValidationError("Choose an authorized Windows account, e.g. PC-A\\transferuser.")
            if type(payload.get('write')) is not bool or type(payload.get('grant_ntfs')) is not bool:
                raise ValidationError("Explicit permission settings are required.")
        else:
            for key in ('dacl_before', 'dacl_after'):
                if payload.get('grant_ntfs') and not str(payload.get(key, '')).startswith('D:'):
                    raise ValidationError("Invalid permission snapshot.")
    return payload


def _send(pipe, value: dict) -> None:
    import win32file
    data = json.dumps(value, ensure_ascii=False).encode('utf-8')
    if len(data) > MAX_PACKET:
        raise PlatformError("IPC message exceeds the allowed size.")
    win32file.WriteFile(pipe, struct.pack('!I', len(data)) + data)


def _receive(pipe) -> dict:
    import win32file

    def exact(count: int) -> bytes:
        result = bytearray()
        while len(result) < count:
            _, data = win32file.ReadFile(pipe, count - len(result))
            if not data:
                raise PlatformError("Local administrator helper disconnected.")
            result.extend(data)
        return bytes(result)
    count = struct.unpack('!I', exact(4))[0]
    if count > MAX_PACKET:
        raise PlatformError("IPC packet rejected.")
    result = json.loads(exact(count).decode('utf-8'))
    if not isinstance(result, dict):
        raise PlatformError("Invalid IPC packet.")
    return result


def _peer_pid(pipe, *, server: bool) -> int:
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = kernel.GetNamedPipeServerProcessId if server else kernel.GetNamedPipeClientProcessId
    fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    fn.restype = wintypes.BOOL
    pid = wintypes.ULONG()
    if not fn(int(pipe), ctypes.byref(pid)):
        raise ctypes.WinError(ctypes.get_last_error())
    return pid.value


class AdminBroker:
    """Local-only ACL-protected pipe; per-action UAC helper exits after one plan.

    Elevated worker independently validates, displays the actual plan, then sends
    the preimage to the parent and waits for a durable SQLite acknowledgement.
    No remote listeners, arbitrary scripts, passwords or general admin RPC.
    """

    def __init__(self, store: Store):
        self.store = store

    def execute(self, action: str, payload: dict) -> dict:
        if os.name != 'nt':
            raise PlatformError("UAC administration requires Windows.")
        payload = validate_action(action, payload)
        if getattr(sys, 'frozen', False):
            from lanbridge.core.paths import contains
            protected = [program_files().resolve()]
            if not any(contains(root, Path(sys.executable).resolve()) for root in protected):
                raise PlatformError('Install LANBridge Pro with the included installer under Program Files before UAC changes. Portable mode supports transfers/diagnostics; elevated code must reside in a protected installation.')
        import win32api
        import win32con
        import win32event
        import win32file
        import win32pipe
        import win32security
        from win32com.shell import shell, shellcon

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        sid_text = win32security.ConvertSidToStringSid(sid)
        attributes = __import__('pywintypes').SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f'D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;{sid_text})', 1)
        pipe_name = r'\\.\pipe\LANBridgePro-' + secrets.token_hex(24)
        nonce = secrets.token_hex(32)
        pipe = win32pipe.CreateNamedPipe(pipe_name,
                                        win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED | 0x00080000,
                                        win32pipe.PIPE_TYPE_BYTE | 0x00000008, 1,
                                        MAX_PACKET, MAX_PACKET, 0, attributes)
        process_handle = None
        operation_id = str(uuid.uuid4())
        try:
            overlap = __import__('pywintypes').OVERLAPPED()
            overlap.hEvent = win32event.CreateEvent(None, True, False, None)
            win32pipe.ConnectNamedPipe(pipe, overlap)
            cmd = helper_command('--admin-worker', pipe_name, nonce, str(os.getpid()))
            process = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb='runas',
                                          lpFile=cmd[0], lpParameters=subprocess.list2cmdline(cmd[1:]),
                                          nShow=0)
            process_handle = process['hProcess']
            wait = win32event.WaitForMultipleObjects([overlap.hEvent, process_handle], False, 120000)
            if wait != win32event.WAIT_OBJECT_0:
                raise PlatformError("Administrator helper did not connect (cancelled UAC or timed out).")
            from ctypes import wintypes
            get_pid = ctypes.WinDLL('kernel32', use_last_error=True).GetProcessId
            get_pid.argtypes = [wintypes.HANDLE]
            get_pid.restype = wintypes.DWORD
            expected_pid = get_pid(int(process_handle))
            if _peer_pid(pipe, server=False) != expected_pid:
                raise PlatformError("Administrator helper identity mismatch.")
            # Synchronous I/O on a separate handle is required: this handle was opened
            # overlapped for ConnectNamedPipe. A bounded Overlapped read/write wrapper
            # is installed below for all subsequent traffic on the server.
            channel = _ServerChannel(pipe, process_handle)
            channel.send({'nonce': nonce, 'action': action, 'payload': payload})
            prepared = channel.receive()
            if not prepared.get('ok'):
                raise PlatformError(prepared.get('error', 'Operation cancelled.'))
            self.store.operation(operation_id, action, payload, prepared['snapshot'])
            channel.send({'ack': True})
            result = channel.receive()
            self.store.finish_operation(operation_id, result)
            if not result.get('ok'):
                raise PlatformError(result.get('error', 'Administrative action failed; inspect rollback history.'))
            return result | {'operation_id': operation_id}
        finally:
            pipe.Close()
            if process_handle:
                process_handle.Close()


class _ServerChannel:
    def __init__(self, pipe, process):
        self.pipe, self.process = pipe, process

    def _io(self, *, data: bytes | None = None, size: int = 0) -> bytes:
        import pywintypes
        import win32event
        import win32file
        overlap = pywintypes.OVERLAPPED()
        overlap.hEvent = win32event.CreateEvent(None, True, False, None)
        buffer = win32file.AllocateReadBuffer(size) if data is None else data
        try:
            if data is None:
                win32file.ReadFile(self.pipe, buffer, overlap)
            else:
                win32file.WriteFile(self.pipe, buffer, overlap)
            # User may spend time reviewing the independent elevated confirmation.
            while True:
                result = win32event.WaitForMultipleObjects([overlap.hEvent, self.process], False, 1000)
                if result == win32event.WAIT_OBJECT_0:
                    count = win32file.GetOverlappedResult(self.pipe, overlap, False)
                    return bytes(buffer[:count]) if data is None else b''
                if result == win32event.WAIT_OBJECT_0 + 1:
                    # The final reply can already be buffered when the worker exits.
                    if win32event.WaitForSingleObject(overlap.hEvent, 200) == win32event.WAIT_OBJECT_0:
                        count = win32file.GetOverlappedResult(self.pipe, overlap, False)
                        return bytes(buffer[:count]) if data is None else b''
                    raise PlatformError("Administrator helper exited before replying.")
        finally:
            overlap.hEvent.Close()

    def send(self, value: dict) -> None:
        data = json.dumps(value, ensure_ascii=False).encode('utf-8')
        if len(data) > MAX_PACKET:
            raise PlatformError('IPC message too large.')
        self._io(data=struct.pack('!I', len(data)) + data)

    def receive(self) -> dict:
        def exact(size: int) -> bytes:
            result = bytearray()
            while len(result) < size:
                part = self._io(size=size - len(result))
                if not part:
                    raise PlatformError('Administrator helper disconnected.')
                result.extend(part)
            return bytes(result)
        count = struct.unpack('!I', exact(4))[0]
        if count > MAX_PACKET:
            raise PlatformError('IPC message too large.')
        return json.loads(exact(count))


def worker(pipe_name: str, nonce: str, parent_pid: int) -> int:
    if os.name != 'nt' or not ctypes.windll.shell32.IsUserAnAdmin():
        return 2
    if not re.fullmatch(r'\\\\\.\\pipe\\LANBridgePro-[a-f0-9]{48}', pipe_name):
        return 3
    import win32file
    import win32pipe
    win32pipe.WaitNamedPipe(pipe_name, 15000)
    pipe = win32file.CreateFile(pipe_name, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                               0, None, win32file.OPEN_EXISTING, 0, None)
    snapshot: dict = {}
    action = ''
    payload: dict = {}
    mutation_started = False
    ps = PowerShell()
    try:
        if _peer_pid(pipe, server=True) != parent_pid:
            return 4
        request = _receive(pipe)
        if not secrets.compare_digest(request.get('nonce', ''), nonce):
            return 5
        action = request['action']
        payload = validate_action(action, request['payload'])
        if action in ('network_apply', 'profile_private'):
            snapshot = ps.call('network_snapshot', payload)
        elif action == 'share_create':
            snapshot = ps.call('share_snapshot', payload)
        elif action in ('network_restore', 'profile_restore'):
            snapshot = ps.call('network_snapshot', payload['snapshot'])
        elif action == 'share_remove':
            snapshot = ps.call('share_current', payload)
        elif action == 'firewall_remove':
            snapshot = ps.call('firewall_current', payload)
        else:
            snapshot = payload.copy()
        preview = ('LOCAL COMPUTER ONLY / هذا الجهاز فقط\n\n' + action + '\n\n' +
                   json.dumps(payload, ensure_ascii=False, indent=2) + '\n\nPrevious state / الإعدادات السابقة:\n' +
                   json.dumps(snapshot, ensure_ascii=False, indent=2) +
                   '\n\nNetwork access may be interrupted. ACL inheritance affects inheriting children.\n'
                   'Domain security policies will not be bypassed. Apply this exact local change?')
        # Default No; helper owns the final authoritative confirmation.
        choice = ctypes.windll.user32.MessageBoxW(None, preview, 'LANBridge Pro — Confirm local change',
                                                 0x4 | 0x30 | 0x100 | 0x40000)
        if choice != 6:
            _send(pipe, {'ok': False, 'error': 'Local change cancelled.'})
            return 0
        _send(pipe, {'ok': True, 'snapshot': snapshot})
        if _receive(pipe) != {'ack': True}:
            return 6
        mutation_started = True
        call_payload = payload | ({'snapshot': snapshot} if action == 'share_create' else {})
        result = ps.call(action, call_payload)
        _send(pipe, {'ok': True, 'data': result})
        return 0
    except Exception as exc:
        rollback = None
        if mutation_started and snapshot and action == 'network_apply':
            try:
                rollback = ps.call('network_restore', {'snapshot': snapshot})
            except Exception as restore_error:
                rollback = {'error': str(restore_error)}
        try:
            _send(pipe, {'ok': False, 'error': str(exc), 'rollback': rollback})
        except Exception:
            return 7
        return 1
    finally:
        pipe.Close()
