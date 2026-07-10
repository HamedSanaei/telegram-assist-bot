"""Use case: test a post's VPN configs through the Iran worker."""

from __future__ import annotations

from src.domain.enums import VpnProtocol, VpnTestStatus
from src.domain.interfaces import PostRepository, VpnConnectivityTester
from src.shared.errors import VpnConnectivityTestError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


class VpnTestService:
    """
    Runs Iran connectivity tests for all configs attached to a post and
    persists the per-config results.

    A post becomes eligible for VPN channel publishing only when at
    least one of its configs works from the Iran network.

    Example:
        service = VpnTestService(tester, posts)
        eligible = await service.test_post_configs(post_id)
    """

    def __init__(self, tester: VpnConnectivityTester, posts: PostRepository) -> None:
        """
        Args:
            tester: Connectivity tester (Iran worker HTTP client in production).
            posts: Post repository used to load and update the post.
        """
        self._tester = tester
        self._posts = posts

    async def test_post_configs(self, post_id: str) -> bool:
        """
        Test every VPN config of a post and store the results.

        Args:
            post_id: Internal id of the post to test.

        Returns:
            ``True`` when at least one config works from Iran, making the
            post eligible for VPN channel publishing.

        Raises:
            RepositoryError: When persistence fails.
        """
        post = await self._posts.get(post_id)
        if post is None:
            logger.warning("VPN test skipped: post not found id=%s", post_id)
            return False

        any_working = False
        for config in post.vpn_configs:
            if config.test_status != VpnTestStatus.PENDING:
                any_working = any_working or (
                    config.test_status == VpnTestStatus.WORKING
                )
                continue
            if config.protocol not in {VpnProtocol.VMESS, VpnProtocol.VLESS}:
                config.test_status = VpnTestStatus.UNSUPPORTED
                logger.info(
                    "VPN test unsupported post=%s protocol=%s",
                    post_id,
                    config.protocol.value,
                )
                continue
            try:
                result = await self._tester.test(config)
            except VpnConnectivityTestError as exc:
                config.test_status = VpnTestStatus.FAILED
                logger.warning(
                    "VPN test error post=%s host=%s error=%s", post_id, config.host, exc
                )
                continue
            config.test_status = (
                VpnTestStatus.WORKING if result.working else VpnTestStatus.FAILED
            )
            if result.working:
                any_working = True
            logger.info(
                "VPN test post=%s host=%s working=%s latency=%sms",
                post_id,
                config.host,
                result.working,
                result.latency_ms,
            )

        await self._posts.update_vpn_configs(post_id, post.vpn_configs)
        return any_working
