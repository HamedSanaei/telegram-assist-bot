"""Local VPN connectivity tester built on the xray-core binary.

This tester runs ONLY on the Iran worker server. For each config it:

1. Builds a minimal xray client configuration with a local SOCKS5 inbound.
2. Starts the xray binary as a subprocess.
3. Sends an HTTP request through the SOCKS proxy to a test URL.
4. Reports success/latency and always terminates the subprocess.
"""

from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from src.domain.entities import VpnConfig
from src.domain.enums import VpnProtocol
from src.domain.interfaces import VpnTestResult
from src.shared.errors import VpnConnectivityTestError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

_STARTUP_WAIT_SECONDS = 1.5


def _free_port() -> int:
    """Return a free local TCP port chosen by the OS."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def build_outbound(config: VpnConfig) -> dict[str, Any]:
    """
    Build the xray outbound object for a vmess/vless config.

    Supports tcp/ws/grpc transports and tls/reality security. Unknown
    fields are ignored so unusual configs still get a best-effort test.

    Args:
        config: The parsed VPN configuration.

    Returns:
        The xray ``outbounds[0]`` JSON object.

    Raises:
        VpnConnectivityTestError: When the protocol is unsupported.
    """
    extra = config.extra
    if config.protocol == VpnProtocol.VMESS:
        user: dict[str, Any] = {
            "id": config.user_id,
            "alterId": int(extra.get("aid", "0") or 0),
            "security": extra.get("scy", "auto") or "auto",
        }
        protocol = "vmess"
    elif config.protocol == VpnProtocol.VLESS:
        user = {"id": config.user_id, "encryption": extra.get("encryption", "none") or "none"}
        if extra.get("flow"):
            user["flow"] = extra["flow"]
        protocol = "vless"
    else:
        raise VpnConnectivityTestError(f"Unsupported protocol: {config.protocol}")

    network = config.transport or extra.get("net") or extra.get("type") or "tcp"
    stream: dict[str, Any] = {"network": network}
    security = (config.security or "").lower()
    sni = extra.get("sni") or extra.get("host") or config.host
    if security == "tls":
        stream["security"] = "tls"
        stream["tlsSettings"] = {"serverName": sni, "allowInsecure": True}
    elif security == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": extra.get("sni", ""),
            "publicKey": extra.get("pbk", ""),
            "shortId": extra.get("sid", ""),
            "fingerprint": extra.get("fp", "chrome") or "chrome",
        }
    if network == "ws":
        stream["wsSettings"] = {
            "path": extra.get("path", "/") or "/",
            "headers": {"Host": extra.get("host", "") or config.host},
        }
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": extra.get("serviceName", "") or extra.get("path", "")}

    return {
        "protocol": protocol,
        "settings": {
            "vnext": [
                {"address": config.host, "port": config.port, "users": [user]}
            ]
        },
        "streamSettings": stream,
    }


class XrayVpnTester:
    """
    Implements :class:`VpnConnectivityTester` using a local xray binary.

    Example:
        tester = XrayVpnTester("/usr/local/bin/xray")
        result = await tester.test(config)
    """

    def __init__(
        self,
        xray_binary_path: str,
        test_url: str = "https://www.gstatic.com/generate_204",
        timeout_seconds: int = 30,
    ) -> None:
        """
        Args:
            xray_binary_path: Path to the xray executable.
            test_url: URL fetched through the proxy to prove connectivity.
            timeout_seconds: Maximum seconds for the proxied test request.
        """
        self._xray_path = xray_binary_path
        self._test_url = test_url
        self._timeout = timeout_seconds

    async def test(self, config: VpnConfig) -> VpnTestResult:
        """
        Test one config end to end through a temporary xray instance.

        Args:
            config: The parsed VPN configuration.

        Returns:
            ``VpnTestResult`` with ``working`` and measured latency.

        Raises:
            VpnConnectivityTestError: When the xray binary is missing or
                cannot be started.
        """
        if not self._xray_path or not Path(self._xray_path).exists():
            raise VpnConnectivityTestError(
                f"xray binary not found at '{self._xray_path}'"
            )
        port = _free_port()
        xray_config = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "listen": "127.0.0.1",
                    "port": port,
                    "protocol": "socks",
                    "settings": {"udp": False},
                }
            ],
            "outbounds": [build_outbound(config)],
        }
        config_file = Path(tempfile.gettempdir()) / f"xray-test-{port}.json"
        config_file.write_text(
            json.dumps(xray_config, ensure_ascii=False), encoding="utf-8"
        )
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                self._xray_path,
                "run",
                "-c",
                str(config_file),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.sleep(_STARTUP_WAIT_SECONDS)
            if process.returncode is not None:
                return VpnTestResult(working=False, error="xray exited immediately")
            return await self._probe(port)
        except OSError as exc:
            raise VpnConnectivityTestError(f"Cannot start xray: {exc}") from exc
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
            config_file.unlink(missing_ok=True)

    async def _probe(self, socks_port: int) -> VpnTestResult:
        """Send one HTTP request through the local SOCKS proxy."""
        proxy = f"socks5://127.0.0.1:{socks_port}"
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                proxy=proxy, timeout=self._timeout, verify=False
            ) as client:
                response = await client.get(self._test_url)
            latency = int((time.monotonic() - started) * 1000)
            working = response.status_code < 400
            return VpnTestResult(
                working=working,
                latency_ms=latency,
                error=None if working else f"HTTP {response.status_code}",
            )
        except httpx.HTTPError as exc:
            return VpnTestResult(working=False, error=str(exc))
