"""Unit tests for vmess/vless parsing and extraction."""

from __future__ import annotations

import base64
import json

import pytest

from src.domain.enums import VpnProtocol
from src.domain.services.vpn_parser import (
    extract_vpn_configs,
    parse_vless,
    parse_vmess,
)
from src.shared.errors import VpnConfigParseError


def _make_vmess(payload: dict[str, object]) -> str:
    """Build a vmess URI from a JSON payload."""
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    return "vmess://" + encoded


VMESS_PAYLOAD = {
    "v": "2",
    "ps": "تست کانفیگ",
    "add": "example.com",
    "port": "443",
    "id": "b831381d-6324-4d53-ad4f-8cda48b30811",
    "aid": "0",
    "net": "ws",
    "host": "cdn.example.com",
    "path": "/ws",
    "tls": "tls",
}

VLESS_URI = (
    "vless://b831381d-6324-4d53-ad4f-8cda48b30811@server.example.com:8443"
    "?type=grpc&security=reality&pbk=publickey123&sid=ab12&sni=cloud.example.com"
    "#%D8%B3%D8%B1%D9%88%D8%B1%20%D8%A7%DB%8C%D8%B1%D8%A7%D9%86"
)


class TestParseVmess:
    """Tests for :func:`parse_vmess`."""

    def test_parses_all_fields(self) -> None:
        config = parse_vmess(_make_vmess(VMESS_PAYLOAD))
        assert config.protocol == VpnProtocol.VMESS
        assert config.host == "example.com"
        assert config.port == 443
        assert config.user_id == "b831381d-6324-4d53-ad4f-8cda48b30811"
        assert config.transport == "ws"
        assert config.security == "tls"
        assert config.remark == "تست کانفیگ"
        assert config.extra["path"] == "/ws"
        assert config.extra["host"] == "cdn.example.com"

    def test_invalid_base64_raises(self) -> None:
        with pytest.raises(VpnConfigParseError):
            parse_vmess("vmess://!!!not-base64!!!")

    def test_missing_host_raises(self) -> None:
        payload = dict(VMESS_PAYLOAD)
        payload["add"] = ""
        with pytest.raises(VpnConfigParseError):
            parse_vmess(_make_vmess(payload))

    def test_non_numeric_port_raises(self) -> None:
        payload = dict(VMESS_PAYLOAD)
        payload["port"] = "abc"
        with pytest.raises(VpnConfigParseError):
            parse_vmess(_make_vmess(payload))


class TestParseVless:
    """Tests for :func:`parse_vless`."""

    def test_parses_all_fields(self) -> None:
        config = parse_vless(VLESS_URI)
        assert config.protocol == VpnProtocol.VLESS
        assert config.host == "server.example.com"
        assert config.port == 8443
        assert config.user_id == "b831381d-6324-4d53-ad4f-8cda48b30811"
        assert config.transport == "grpc"
        assert config.security == "reality"
        assert config.extra["pbk"] == "publickey123"
        assert config.remark == "سرور ایران"

    def test_missing_port_raises(self) -> None:
        with pytest.raises(VpnConfigParseError):
            parse_vless("vless://uuid@host")

    def test_missing_user_raises(self) -> None:
        with pytest.raises(VpnConfigParseError):
            parse_vless("vless://server.example.com:443")


class TestExtractVpnConfigs:
    """Tests for :func:`extract_vpn_configs`."""

    def test_extracts_from_mixed_persian_text(self) -> None:
        text = (
            "کانفیگ‌های جدید امروز:\n"
            f"{_make_vmess(VMESS_PAYLOAD)}\n"
            "و این هم vless:\n"
            f"{VLESS_URI}\n"
            "لطفا تست کنید."
        )
        configs = extract_vpn_configs(text)
        assert len(configs) == 2
        assert {c.protocol for c in configs} == {VpnProtocol.VMESS, VpnProtocol.VLESS}

    def test_skips_invalid_candidates(self) -> None:
        text = "خراب: vmess://broken-payload ولی متن ادامه دارد"
        assert extract_vpn_configs(text) == []

    def test_no_configs_in_plain_news(self) -> None:
        assert extract_vpn_configs("خبر فوری: قیمت دلار افزایش یافت") == []
