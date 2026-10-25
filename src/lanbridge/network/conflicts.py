from __future__ import annotations

import ctypes
import os
import socket
import struct

from lanbridge.core.errors import ValidationError

from .validation import same_subnet


def check_address(payload: dict, adapters: list[dict]) -> dict:
    """Positive ARP/local conflicts block assignment; silence is never a free-IP guarantee."""
    target = payload['ip']
    selected = next((a for a in adapters if a['index'] == payload['index'] and a['guid'] == payload['guid']), None)
    if not selected:
        raise ValidationError('Adapter identity changed. Refresh and choose again.')
    for adapter in adapters:
        if any(x['ip'] == target for x in adapter['ipv4']):
            if adapter['index'] != selected['index']:
                raise ValidationError('The requested IPv4 is already assigned to another local adapter.')
            return {'state': 'already_on_selected_adapter', 'note': 'Windows DAD is still checked after assignment.'}
    sources = [x['ip'] for x in selected['ipv4'] if same_subnet(x['ip'], x['prefix'], target)]
    if os.name != 'nt' or not sources or selected['status'] != 'Up':
        return {'state': 'unknown', 'note': 'No current on-link ARP probe is possible. Windows DAD checks after assignment.'}
    from ctypes import wintypes
    sender = ctypes.WinDLL('iphlpapi', use_last_error=True).SendARP
    sender.argtypes = [wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG)]
    sender.restype = wintypes.DWORD
    buffer = ctypes.create_string_buffer(32)
    length = wintypes.ULONG(32)
    result = sender(struct.unpack('=I', socket.inet_aton(target))[0],
                    struct.unpack('=I', socket.inet_aton(sources[0]))[0], buffer, ctypes.byref(length))
    if result == 0 and length.value:
        raise ValidationError('Another LAN interface answered ARP for this IPv4. Choose a different address.')
    return {'state': 'unknown', 'note': 'No ARP answer; that does not prove the address is unused.'}
