"""HTTP client for the remote Iran VPN testing worker.

The main server never tests configs itself; it sends them to the Iran
worker API, which reports whether the config works from Iran's network.
"""

from __future__ import annotations

import httpx

from src.domain.entities import VpnConfig
from src.domain.interfaces import VpnTestResult
from src.shared.errors import VpnConnectivityTestError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


class IranWorkerVpnTester:
    """
    Implements :class:`VpnConnectivityTester` by delegating to the
    Iran worker's authenticated ``POST /api/test`` endpoint.

    Example:
        tester = IranWorkerVpnTester("http://iran-server:8088", "secret-token")
        result = await tester.test(config)
    """

    def __init__(self, api_url: str, api_token: str, timeout_seconds: int = 30) -> None:
        """
        Args:
            api_url: Base URL of the Iran worker (e.g. ``http://1.2.3.4:8088``).
            api_token: Shared bearer token from ``configuration.json``.
            timeout_seconds: Total HTTP timeout, which must exceed the
                worker's own per-test timeout.
        """
        self._api_url = api_url.rstrip("/")
        self._api_token = api_token
        self._timeout = timeout_seconds

    async def test(self, config: VpnConfig) -> VpnTestResult:
        """
        Ask the Iran worker to test one config.

        Args:
            config: The parsed VPN configuration (its ``raw`` URI is sent).

        Returns:
            The worker's test result.

        Raises:
            VpnConnectivityTestError: When the worker is unreachable,
                rejects authentication, or returns an invalid response.
        """
        url = f"{self._api_url}/api/test"
        headers = {"Authorization": f"Bearer {self._api_token}"}
        payload = {"raw": config.raw}
        try:
            async with httpx.AsyncClient(timeout=self._timeout + 10) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            raise VpnConnectivityTestError(f"Iran worker request failed: {exc}") from exc
        except ValueError as exc:
            raise VpnConnectivityTestError("Iran worker returned invalid JSON") from exc
        if "working" not in body:
            raise VpnConnectivityTestError("Iran worker response missing 'working'")
        return VpnTestResult(
            working=bool(body["working"]),
            latency_ms=body.get("latency_ms"),
            error=body.get("error"),
        )
