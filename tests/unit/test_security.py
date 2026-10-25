import base64
import json
from types import SimpleNamespace

import pytest

from lanbridge.core.errors import ValidationError
from lanbridge.platform.powershell import PowerShell
from lanbridge.security.admin import validate_action


def test_powershell_code_and_data_are_separate(monkeypatch, tmp_path):
    import lanbridge.platform.powershell as module
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt', environ={'SystemRoot': str(tmp_path)}))
    monkeypatch.setattr(module, 'system_directory', lambda: tmp_path)
    monkeypatch.setattr(module.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    payload = {'name': "data'; Remove-Item C:\\ -Recurse; $(whoami)"}
    captured = {}
    def run(args, **kwargs):
        captured.update(args=args, **kwargs)
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'data': []}))
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert PowerShell().call('shares', payload) == []
    assert captured['shell'] is False
    assert payload['name'] not in ' '.join(captured['args'])
    assert len(' '.join(captured['args'])) < 32767
    code, data = captured['input'].split('\n', 1)
    assert json.loads(data)['payload'] == payload
    assert b'New-SmbShare' in base64.b64decode(code)
    assert payload['name'].encode() not in base64.b64decode(code)


@pytest.mark.parametrize('action', ['Run-Command', 'disable_signing', 'enable_guest', 'remote_exec'])
def test_admin_allowlist_rejects_other_capabilities(action):
    with pytest.raises(ValidationError):
        validate_action(action, {})


def test_app_owned_share_only(tmp_path):
    import uuid
    payload = {'owner': str(uuid.uuid4()), 'name': 'LANBridge_1234', 'path': str(tmp_path),
               'account': 'PC-B\\user', 'write': False, 'grant_ntfs': False}
    assert validate_action('share_create', payload) == payload
    with pytest.raises(ValidationError):
        validate_action('share_create', payload | {'name': 'C$'})


def test_no_security_downgrade_commands():
    from pathlib import Path
    ps = (Path(__file__).parents[2] / 'src/lanbridge/resources/windows.ps1').read_text()
    assert 'Set-SmbClientConfiguration' not in ps
    assert 'Enable-WindowsOptionalFeature' not in ps
    assert 'Set-NetFirewallProfile' not in ps
    assert '-Profile Private' in ps
    assert '-RemoteAddress $p.peer' in ps


def test_disconnected_parent_before_durable_ack_never_mutates(monkeypatch):
    import sys
    import lanbridge.security.admin as module
    calls = []
    pipe = SimpleNamespace(Close=lambda: None)
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt'))
    windll = SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: True),
                             user32=SimpleNamespace(MessageBoxW=lambda *args: 6))
    monkeypatch.setattr(module.ctypes, 'windll', windll, raising=False)
    monkeypatch.setitem(sys.modules, 'win32file', SimpleNamespace(
        CreateFile=lambda *args: pipe, GENERIC_READ=1, GENERIC_WRITE=2, OPEN_EXISTING=3))
    monkeypatch.setitem(sys.modules, 'win32pipe', SimpleNamespace(WaitNamedPipe=lambda *args: None))
    monkeypatch.setattr(module, '_peer_pid', lambda *args, **kwargs: 123)
    payload = {'index': 4, 'guid': 'e634c111-caaa-4320-a8df-a16412c92942', 'mode': 'dhcp'}
    monkeypatch.setattr(module, '_receive', lambda _: {'nonce': 'abc', 'action': 'network_apply', 'payload': payload})
    def fail_send(*args):
        raise OSError('Parent disconnected before durable snapshot acknowledgement')
    monkeypatch.setattr(module, '_send', fail_send)
    class FakePS:
        def call(self, action, payload):
            calls.append(action)
            return {'index': 4, 'dhcp': True}
    monkeypatch.setattr(module, 'PowerShell', FakePS)
    assert module.worker('\\\\.\\pipe\\LANBridgePro-' + 'a' * 48, 'abc', 123) == 7
    assert calls == ['network_snapshot']


def test_credentials_use_native_apis_only_and_safe_status(monkeypatch, caplog):
    import sys
    from lanbridge.security.credentials import CredentialManager, Credentials
    manager = CredentialManager()
    monkeypatch.setattr(manager, '_require_windows', lambda: None)
    saved = {}
    calls = []
    credential_api = SimpleNamespace(
        CRED_TYPE_GENERIC=1, CRED_PERSIST_LOCAL_MACHINE=2,
        CredWrite=lambda data, flags: saved.update(data),
        CredRead=lambda target, kind, flags: saved.copy())
    class NativeError(Exception):
        def __init__(self, code):
            self.winerror = code
            super().__init__(code)
    monkeypatch.setitem(sys.modules, 'win32cred', credential_api)
    monkeypatch.setitem(sys.modules, 'pywintypes', SimpleNamespace(error=NativeError))
    monkeypatch.setitem(sys.modules, 'win32netcon', SimpleNamespace(RESOURCETYPE_DISK=1))
    monkeypatch.setitem(sys.modules, 'win32wnet', SimpleNamespace(WNetAddConnection2=lambda *args: calls.append(args)))
    secret = Credentials('PC-B\\user', 'secret-سري-&-$(noexec)')
    manager.save('PC-B', secret)
    manager.connect(r'\\PC-B\Data')
    assert calls[0][4:6] == (secret.username, secret.password)
    assert saved['CredentialBlob'] == secret.password.encode('utf-16-le')
    assert manager.status('PC-B') == {'saved_by_app': True, 'account': secret.username,
                                      'storage': 'Windows Credential Manager'}
    assert secret.password not in caplog.text
