"""Pure parsing and extraction of common proxy configuration URIs.

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

_CONFIG_RE = re.compile(
    r"(?i)\b(?:vmess|vless|ssr|ss|trojan|hysteria2|hy2|tuic)://[^\s'\"<>`]+"
)


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


def _parse_standard_uri(raw: str, protocol: VpnProtocol) -> VpnConfig:
    """Parse a username/password style proxy URI into the common domain model."""
    parts = urlsplit(raw)
    host = parts.hostname or ""
    try:
        port = parts.port
    except ValueError as exc:
        raise VpnConfigParseError(f"{protocol.value} port is invalid") from exc
    credential = unquote(parts.username or parts.password or "")
    if not host or port is None or not credential:
        raise VpnConfigParseError(
            f"{protocol.value} URI is missing credential, host, or port"
        )
    query = {key: values[0] for key, values in parse_qs(parts.query).items() if values}
    if parts.password:
        query["password"] = unquote(parts.password)
    return VpnConfig(
        protocol=protocol,
        raw=raw,
        host=host,
        port=port,
        user_id=credential,
        transport=query.get("type") or query.get("network"),
        security=query.get("security"),
        remark=unquote(parts.fragment) if parts.fragment else None,
        extra=query,
    )


def parse_shadowsocks(raw: str) -> VpnConfig:
    """Parse SIP002 and legacy ``ss://`` configuration forms."""
    if not raw.lower().startswith("ss://"):
        raise VpnConfigParseError("Not an ss URI")
    body, _, fragment = raw[5:].partition("#")
    if "@" in body:
        credentials_raw, endpoint = body.rsplit("@", maxsplit=1)
        try:
            credentials = base64.urlsafe_b64decode(
                credentials_raw + "=" * (-len(credentials_raw) % 4)
            ).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            credentials = unquote(credentials_raw)
        decoded = f"{credentials}@{endpoint}"
    else:
        try:
            decoded = base64.urlsafe_b64decode(
                body + "=" * (-len(body) % 4)
            ).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise VpnConfigParseError(f"Invalid ss payload: {exc}") from exc
    match = re.fullmatch(r"([^:]+):(.+)@([^:]+):(\d+)", decoded)
    if match is None:
        raise VpnConfigParseError("Invalid ss credential or endpoint")
    method, password, host, port = match.groups()
    return VpnConfig(
        protocol=VpnProtocol.SHADOWSOCKS,
        raw=raw,
        host=host,
        port=int(port),
        user_id=password,
        security=method,
        remark=unquote(fragment) if fragment else None,
        extra={"method": method, "password": password},
    )


def parse_shadowsocks_r(raw: str) -> VpnConfig:
    """Parse a best-effort ``ssr://`` configuration payload."""
    if not raw.lower().startswith("ssr://"):
        raise VpnConfigParseError("Not an ssr URI")
    payload = raw[6:].split("#", maxsplit=1)[0]
    try:
        decoded = base64.urlsafe_b64decode(
            payload + "=" * (-len(payload) % 4)
        ).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise VpnConfigParseError(f"Invalid ssr payload: {exc}") from exc
    main = decoded.split("/?", maxsplit=1)[0]
    parts = main.split(":")
    if len(parts) < 6:
        raise VpnConfigParseError("Invalid ssr payload fields")
    host, port, protocol_name, method, obfs, password_raw = parts[:6]
    try:
        password = base64.urlsafe_b64decode(
            password_raw + "=" * (-len(password_raw) % 4)
        ).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        password = password_raw
    return VpnConfig(
        protocol=VpnProtocol.SHADOWSOCKS_R,
        raw=raw,
        host=host,
        port=int(port),
        user_id=password,
        security=method,
        extra={"protocol": protocol_name, "method": method, "obfs": obfs},
    )


def parse_proxy_config(raw: str) -> VpnConfig:
    """Parse one supported proxy URI by its scheme."""
    scheme = raw.split(":", maxsplit=1)[0].lower()
    if scheme == "vmess":
        return parse_vmess(raw)
    if scheme == "vless":
        return parse_vless(raw)
    if scheme == "ss":
        return parse_shadowsocks(raw)
    if scheme == "ssr":
        return parse_shadowsocks_r(raw)
    protocol_map = {
        "trojan": VpnProtocol.TROJAN,
        "hysteria2": VpnProtocol.HYSTERIA2,
        "hy2": VpnProtocol.HYSTERIA2,
        "tuic": VpnProtocol.TUIC,
    }
    protocol = protocol_map.get(scheme)
    if protocol is None:
        raise VpnConfigParseError(f"Unsupported proxy scheme: {scheme}")
    return _parse_standard_uri(raw, protocol)


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
    for match in _CONFIG_RE.finditer(text):
        raw = match.group(0).rstrip(".,،؛!?)]}")
        try:
            configs.append(parse_proxy_config(raw))
        except VpnConfigParseError:
            continue
    return configs
