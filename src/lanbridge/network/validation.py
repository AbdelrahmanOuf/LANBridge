from __future__ import annotations

import ipaddress
import re
import uuid

from lanbridge.core.errors import ValidationError


def ipv4(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValidationError("Enter a valid IPv4 address.") from exc
    if address.is_multicast or address.is_loopback or address.is_unspecified or str(address) == '255.255.255.255':
        raise ValidationError("Use a unicast IPv4 address on the LAN.")
    return str(address)


def host(value: str) -> str:
    if re.fullmatch(r"[0-9.]+", value):
        return ipv4(value)
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value):
        raise ValidationError("Use an IPv4 address or a valid Windows/DNS computer name.")
    if any(not part or len(part) > 63 or part.startswith('-') or part.endswith('-') for part in value.split('.')):
        raise ValidationError("Invalid DNS name.")
    return value


def validate_adapter(payload: dict) -> None:
    if type(payload.get("index")) is not int or not 1 <= payload["index"] <= 1_000_000:
        raise ValidationError("Select one specific network adapter.")
    uuid.UUID(payload.get("guid", ""))


def static_config(payload: dict) -> dict:
    validate_adapter(payload)
    if payload.get("mode") not in ('dhcp', 'static'):
        raise ValidationError("Choose DHCP or static IPv4.")
    clean = {"index": payload["index"], "guid": payload["guid"], "mode": payload["mode"]}
    if clean['mode'] == 'dhcp':
        return clean
    ip = ipv4(payload.get('ip', ''))
    try:
        prefix_input = payload.get('prefix', 24)
        network = ipaddress.IPv4Network(f"{ip}/{prefix_input}", strict=False)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Enter a prefix (e.g. 24) or a contiguous subnet mask.") from exc
    if not 1 <= network.prefixlen <= 30 or ipaddress.IPv4Address(ip) in (network.network_address, network.broadcast_address):
        raise ValidationError("Choose a usable host address and prefix /1–/30.")
    gateway = payload.get('gateway', '')
    if gateway:
        gateway = ipv4(gateway)
        if ipaddress.IPv4Address(gateway) not in network or gateway == ip:
            raise ValidationError("Gateway must be a different usable address in the same subnet.")
        if ipaddress.IPv4Address(gateway) in (network.network_address, network.broadcast_address):
            raise ValidationError("Gateway is a network/broadcast address.")
    dns = payload.get('dns', [])
    if not isinstance(dns, list) or len(dns) > 4:
        raise ValidationError("Enter at most four DNS IPv4 addresses.")
    return clean | {"ip": ip, "prefix": network.prefixlen, "gateway": gateway, "dns": [ipv4(d) for d in dns]}


def same_subnet(local: str, prefix: int, remote: str) -> bool:
    return ipaddress.IPv4Address(remote) in ipaddress.IPv4Network(f"{local}/{prefix}", strict=False)
