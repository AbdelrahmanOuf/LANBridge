from __future__ import annotations

import ipaddress
import json
import re
import secrets
import socket
import threading
import time
import uuid

from lanbridge.core.errors import PlatformError, ValidationError

PORT = 47623
MAX_PACKET = 768


def decode(data: bytes) -> dict:
    if len(data) > MAX_PACKET:
        raise ValidationError('Discovery packet exceeds limit.')
    obj = json.loads(data)
    if not isinstance(obj, dict) or obj.get('app') != 'LANBridgePro' or obj.get('v') != 1:
        raise ValidationError('Unknown discovery protocol.')
    if not re.fullmatch(r'[a-f0-9]{32}', obj.get('nonce', '')):
        raise ValidationError('Invalid challenge.')
    return obj


def discover(local_ip: str, prefix: int, node_id: str, timeout: float = 2.5) -> list[dict]:
    network = ipaddress.IPv4Network(f'{local_ip}/{prefix}', strict=False)
    challenge = secrets.token_hex(16)
    request = {'app': 'LANBridgePro', 'v': 1, 'kind': 'probe', 'nonce': challenge}
    result: dict[str, dict] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((local_ip, 0))
        sock.settimeout(0.2)
        sock.sendto(json.dumps(request).encode(), (str(network.broadcast_address), PORT))
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            try:
                packet, peer = sock.recvfrom(MAX_PACKET + 1)
                if ipaddress.IPv4Address(peer[0]) not in network:
                    continue
                obj = decode(packet)
                if obj.get('nonce') != challenge or obj.get('kind') != 'device':
                    continue
                device_id = str(uuid.UUID(obj.get('id', '')))
                name = obj.get('name', '')
                if device_id == node_id or not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,63}', name):
                    continue
                result[peer[0]] = {'ip': peer[0], 'name': name, 'id': device_id,
                                   'trust': 'Unverified advertisement; SMB credentials still required'}
            except TimeoutError:
                continue
            except (ValueError, ValidationError, TypeError):
                continue
    return list(result.values())


class DiscoveryResponder:
    def __init__(self, node_id: str):
        self.node_id = str(uuid.UUID(node_id))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, local_ip: str, prefix: int) -> None:
        self.close()
        network = ipaddress.IPv4Network(f'{local_ip}/{prefix}', strict=False)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            # Broadcast is received on wildcard binding, then strictly subnet-filtered.
            sock.bind(('', PORT))
            sock.settimeout(0.3)
        except OSError as exc:
            sock.close()
            raise PlatformError(f'Cannot listen for discovery on UDP {PORT}: {exc}') from exc
        self._socket = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, args=(sock, network), daemon=True,
                                        name='LANBridge discovery (read-only)')
        self._thread.start()

    def _serve(self, sock: socket.socket, network: ipaddress.IPv4Network) -> None:
        recent: dict[str, float] = {}
        while not self._stop.is_set():
            try:
                packet, peer = sock.recvfrom(MAX_PACKET + 1)
                now = time.monotonic()
                if ipaddress.IPv4Address(peer[0]) not in network or now - recent.get(peer[0], 0) < 0.5:
                    continue
                if len(recent) > 512:
                    recent.clear()
                recent[peer[0]] = now
                obj = decode(packet)
                if obj.get('kind') != 'probe':
                    continue
                name = re.sub(r'[^A-Za-z0-9_.-]', '_', socket.gethostname())[:63] or 'Windows-PC'
                response = {'app': 'LANBridgePro', 'v': 1, 'kind': 'device', 'nonce': obj['nonce'],
                            'id': self.node_id, 'name': name}
                sock.sendto(json.dumps(response).encode(), peer)
            except TimeoutError:
                continue
            except (ValueError, ValidationError, TypeError):
                continue
            except OSError:
                return

    def close(self) -> None:
        self._stop.set()
        if self._socket:
            self._socket.close()
        if self._thread:
            self._thread.join(timeout=1)
        self._thread, self._socket = None, None
