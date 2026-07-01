"""Unit tests for the VPN connectivity test use case."""

from __future__ import annotations

from src.application.vpn_test_service import VpnTestService
from src.domain.entities import Post, VpnConfig
from src.domain.enums import VpnProtocol, VpnTestStatus
from tests.unit.application.fakes import FakePostRepository, FakeVpnTester


def _config(host: str) -> VpnConfig:
    """Build a minimal vless config for a host."""
    return VpnConfig(
        protocol=VpnProtocol.VLESS,
        raw=f"vless://uuid@{host}:443",
        host=host,
        port=443,
        user_id="uuid",
    )


def _post_with_configs(hosts: list[str]) -> Post:
    """Build a post carrying one config per host."""
    return Post(
        post_id="p1",
        source_chat_id=-1,
        source_message_id=1,
        text="کانفیگ",
        content_hash="h",
        vpn_configs=[_config(h) for h in hosts],
    )


class TestVpnTestService:
    """Tests for :class:`VpnTestService`."""

    async def test_eligible_when_one_config_works(self) -> None:
        posts = FakePostRepository()
        await posts.save(_post_with_configs(["dead.example", "alive.example"]))
        service = VpnTestService(FakeVpnTester({"alive.example"}), posts)
        eligible = await service.test_post_configs("p1")
        assert eligible is True
        stored = await posts.get("p1")
        statuses = {c.host: c.test_status for c in stored.vpn_configs}
        assert statuses["alive.example"] == VpnTestStatus.WORKING
        assert statuses["dead.example"] == VpnTestStatus.FAILED
        assert stored.has_working_vpn_config()

    async def test_not_eligible_when_all_fail(self) -> None:
        posts = FakePostRepository()
        await posts.save(_post_with_configs(["dead.example"]))
        service = VpnTestService(FakeVpnTester(set()), posts)
        assert await service.test_post_configs("p1") is False
        stored = await posts.get("p1")
        assert not stored.has_working_vpn_config()

    async def test_missing_post_returns_false(self) -> None:
        service = VpnTestService(FakeVpnTester(), FakePostRepository())
        assert await service.test_post_configs("missing") is False
