"""Pure parsing and extraction of vmess/vless configurations.

This module uses only the Python standard library so it can live in the
domain layer. It extracts configuration URIs from arbitrary post text
(including Persian text) and parses them into :class:`VpnConfig` values.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

from src.domain.entities import VpnConfig
from src.domain.enums import VpnProtocol
from src.shared.errors import VpnConfigParseError

_VMESS_RE = re.compile(r"vmess://[A-Za-z0-9+/=_-]+")
_VLESS_RE = re.compile(r"vless://[^\s'\"<>`]+")


def parse_vmess(raw: str) -> VpnConfig:
    """
    Parse a ``vmess://`` URI (base64-encoded JSON payload).

    Args:
        raw: The full vmess URI.

    Returns:
        The parsed :class:`VpnConfig`.

    Raises:
        VpnConfigParseError:
            When the payload is not valid base64/JSON or lacks the
            required ``add``, ``port``, or ``id`` fields.

    Example:
        config = parse_vmess("vmess://eyJhZGQiOiAi...")
    """
    if not raw.startswith("vmess://"):
        raise VpnConfigParseError("Not a vmess URI")
    payload = raw[len("vmess://") :]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload).decode("utf-8")
        data = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VpnConfigParseError(f"Invalid vmess payload: {exc}") from exc
    if not isinstance(data, dict):
        raise VpnConfigParseError("vmess payload must be a JSON object")

    host = str(data.get("add", "")).strip()
    user_id = str(data.get("id", "")).strip()
    try:
        port = int(str(data.get("port", "")).strip())
    except ValueError as exc:
        raise VpnConfigParseError("vmess port is not a number") from exc
    if not host or not user_id:
        raise VpnConfigParseError("vmess config is missing 'add' or 'id'")

    extra = {
        key: str(value)
        for key, value in data.items()
        if key not in {"add", "port", "id", "ps"} and str(value) != ""
    }
    security = str(data.get("tls", "")).strip() or None
    return VpnConfig(
        protocol=VpnProtocol.VMESS,
        raw=raw,
        host=host,
        port=port,
        user_id=user_id,
        transport=str(data.get("net", "")).strip() or None,
        security=security,
        remark=str(data.get("ps", "")).strip() or None,
        extra=extra,
    )


def parse_vless(raw: str) -> VpnConfig:
    """
    Parse a ``vless://uuid@host:port?params#remark`` URI.

    Args:
        raw: The full vless URI.

    Returns:
        The parsed :class:`VpnConfig`.

    Raises:
        VpnConfigParseError:
            When the URI has no user id, host, or valid port.

    Example:
        config = parse_vless("vless://uuid@example.com:443?security=tls#remark")
    """
    if not raw.startswith("vless://"):
        raise VpnConfigParseError("Not a vless URI")
    parts = urlsplit(raw)
    user_id = unquote(parts.username or "")
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError as exc:
        raise VpnConfigParseError("vless port is not a number") from exc
    if not user_id or not host or port is None:
        raise VpnConfigParseError("vless URI is missing uuid, host, or port")

    query = {key: values[0] for key, values in parse_qs(parts.query).items() if values}
    remark = unquote(parts.fragment) if parts.fragment else None
    return VpnConfig(
        protocol=VpnProtocol.VLESS,
        raw=raw,
        host=host,
        port=port,
        user_id=user_id,
        transport=query.get("type") or None,
        security=query.get("security") or None,
        remark=remark,
        extra=query,
    )


def extract_vpn_configs(text: str) -> list[VpnConfig]:
    """
    Extract all valid vmess/vless configurations from free-form text.

    Invalid or truncated candidates are skipped silently so that one
    broken URI never blocks collection of a post.

    Args:
        text: Arbitrary post text, possibly containing Persian content.

    Returns:
        A list of parsed configs, in order of appearance.

    Example:
        configs = extract_vpn_configs("کانفیگ جدید:\\nvless://...")
    """
    configs: list[VpnConfig] = []
    for match in _VMESS_RE.finditer(text):
        try:
            configs.append(parse_vmess(match.group(0)))
        except VpnConfigParseError:
            continue
    for match in _VLESS_RE.finditer(text):
        try:
            configs.append(parse_vless(match.group(0)))
        except VpnConfigParseError:
            continue
    return configs
