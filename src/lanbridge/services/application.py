from __future__ import annotations

import contextlib
import json
import socket
import threading
import uuid
from pathlib import Path

from lanbridge.core.errors import BusyError, CredentialError, ValidationError
from lanbridge.core.models import TaskSpec, TransferSettings
from lanbridge.core.store import Store
from lanbridge.network.diagnostics import as_list, report
from lanbridge.network.discovery import DiscoveryResponder
from lanbridge.platform.powershell import PowerShell
from lanbridge.security.admin import AdminBroker
from lanbridge.security.credentials import CredentialManager
from lanbridge.transfer.engine import TransferEngine


class ApplicationService:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.store = Store(data_dir / 'lanbridge.sqlite3')
        self.recovered = self.store.recover()
        self.ps = PowerShell()
        self.engine = TransferEngine(self.store, data_dir)
        self.admin = AdminBroker(self.store)
        self.credentials = CredentialManager()
        self.lock = threading.Lock()
        node = self.store.get_setting('node_id') or str(uuid.uuid4())
        self.store.setting('node_id', node)
        self.discovery = DiscoveryResponder(node)
        self.node_id = node

    @property
    def busy(self) -> bool:
        return self.lock.locked() or self.engine.active

    @contextlib.contextmanager
    def exclusive(self):
        if not self.lock.acquire(blocking=False):
            raise BusyError('An operation is active. Stop it before changing network/shares or the destination.')
        try:
            yield
        finally:
            self.lock.release()

    def adapters(self) -> list[dict]:
        return as_list(self.ps.call('adapters'))

    def shares(self) -> list[dict]:
        return as_list(self.ps.call('shares'))

    def local_change(self, action: str, payload: dict) -> dict:
        with self.exclusive():
            if action == 'network_apply' and payload.get('mode') == 'static':
                from lanbridge.network.conflicts import check_address
                check_address(payload, self.adapters())
            return self.admin.execute(action, payload)

    def create_task(self, sources: list[str], destination: str, folder_itself: bool,
                    settings: TransferSettings) -> TaskSpec:
        spec = TaskSpec(tuple(sources), destination, folder_itself, settings)
        self.store.create(spec)
        return spec

    def prepare(self, spec: TaskSpec, notify):
        with self.exclusive():
            self._connect_task_shares(spec)
            return self.engine.prepare(spec, notify)

    def transfer(self, spec: TaskSpec, notify, *, dry=False, failed_only=False, preflight_done=False):
        with self.exclusive():
            self._connect_task_shares(spec)
            return self.engine.run(spec, notify, dry=dry, failed_only=failed_only, preflight_done=preflight_done)

    def _connect_task_shares(self, spec: TaskSpec) -> None:
        from lanbridge.core.paths import validate_unc
        seen: set[tuple[str, str]] = set()
        for path in (*spec.sources, spec.destination):
            if not path.startswith('\\\\'):
                continue
            server, share = validate_unc(path)
            key = (server.casefold(), share.casefold())
            if key in seen:
                continue
            seen.add(key)
            unc = '\\\\' + server + '\\' + share
            try:
                self.credentials.connect(unc)
            except CredentialError as exc:
                exc.auto_prompt = True
                self.store.set_state(spec.id, 'failed', str(exc))
                raise

    def new_share(self, path: str, account: str, write: bool, grant_ntfs: bool) -> dict:
        candidate = Path(path)
        if candidate.is_file():
            candidate = candidate.parent
        if not candidate.is_dir():
            raise ValidationError('Choose an existing local folder or file.')
        owner = str(uuid.uuid4())
        return {'name': 'LANBridge_' + uuid.uuid4().hex[:12], 'owner': owner,
                'path': str(candidate), 'account': account, 'write': write, 'grant_ntfs': grant_ntfs}

    def rollback(self, row: dict) -> dict:
        action = row['action']
        payload = json.loads(row['payload'])
        snapshot = json.loads(row['snapshot'])
        outcome = json.loads(row['outcome'] or '{}')
        if action == 'network_apply':
            return self.local_change('network_restore', {'snapshot': snapshot})
        if action == 'profile_private':
            return self.local_change('profile_restore', {'snapshot': snapshot})
        if action == 'share_create':
            data = outcome.get('data')
            if not data:
                raise ValidationError('Incomplete share operation: inspect local share/ACL state before manual recovery.')
            return self.local_change('share_remove', data)
        if action == 'firewall_add':
            return self.local_change('firewall_remove', payload)
        raise ValidationError('This operation is itself a rollback/removal. Choose the original operation.')

    def diagnostic_report(self, peer: str = '', adapter: dict | None = None, unc: str = '') -> dict:
        errors = [{'task': x['id'], 'state': x['state'], 'message': x['message']}
                  for x in self.store.tasks() if x['state'] in ('failed', 'interrupted')]
        data = report(self.ps, errors, peer, adapter, unc)
        if peer:
            from lanbridge.core.paths import validate_unc
            data['credential_status'] = self.credentials.status(validate_unc(unc)[0] if unc else peer)
        with self.store.connect() as db:
            data['source_cleanup_audit'] = [dict(x) for x in db.execute(
                'SELECT * FROM cleanup_events ORDER BY id DESC LIMIT 500')]
        data['computer'] = socket.gethostname()
        return data
