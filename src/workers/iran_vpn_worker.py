"""Iran VPN testing worker: token-protected HTTP API around xray.

Runs on the Iran server (``python -m src.workers.iran_vpn_worker``) and
exposes ``POST /api/test`` which receives a raw vmess/vless URI, tests
it through the local xray binary against the Iranian network, and
returns whether it works.
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from src.domain.interfaces import VpnConnectivityTester
from src.domain.services.vpn_parser import parse_vless, parse_vmess
from src.infrastructure.vpn.xray_tester import XrayVpnTester
from src.shared.config import load_configuration, validate_worker_config
from src.shared.errors import VpnConfigParseError, VpnConnectivityTestError
from src.shared.logging_setup import get_logger, setup_logging

logger = get_logger(__name__)


class TestRequest(BaseModel):
    """Request body: the raw vmess/vless URI to test."""

    raw: str


class TestResponse(BaseModel):
    """Response body describing one connectivity test result."""

    working: bool
    latency_ms: int | None = None
    error: str | None = None


def create_app(tester: VpnConnectivityTester, api_token: str) -> FastAPI:
    """
    Build the FastAPI application for the worker.

    Args:
        tester: The connectivity tester (xray-based in production).
        api_token: Shared bearer token required on every request.

    Returns:
        The configured FastAPI app.

    Example:
        app = create_app(XrayVpnTester("/usr/local/bin/xray"), "secret")
    """
    app = FastAPI(title="Iran VPN Testing Worker", docs_url=None, redoc_url=None)

    @app.post("/api/test", response_model=TestResponse)
    async def test_config(
        request: TestRequest, authorization: str = Header(default="")
    ) -> TestResponse:
        """Authenticate, parse, and test one VPN configuration."""
        if authorization != f"Bearer {api_token}":
            raise HTTPException(status_code=401, detail="Invalid token")
        raw = request.raw.strip()
        try:
            if raw.startswith("vmess://"):
                config = parse_vmess(raw)
            elif raw.startswith("vless://"):
                config = parse_vless(raw)
            else:
                raise VpnConfigParseError("Only vmess:// and vless:// are supported")
        except VpnConfigParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            result = await tester.test(config)
        except VpnConnectivityTestError as exc:
            logger.error("VPN test execution failed host=%s error=%s", config.host, exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        logger.info(
            "Tested config host=%s port=%s working=%s", config.host, config.port, result.working
        )
        return TestResponse(
            working=result.working, latency_ms=result.latency_ms, error=result.error
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Unauthenticated liveness probe."""
        return {"status": "ok"}

    return app


def main() -> None:
    """
    Synchronous entrypoint for ``python -m src.workers.iran_vpn_worker``.

    Raises:
        ConfigurationError: When worker configuration is incomplete.
    """
    import uvicorn

    config = load_configuration()
    setup_logging(config.logging.level, config.logging.file)
    validate_worker_config(config)
    tester = XrayVpnTester(
        xray_binary_path=config.vpn_testing.xray_binary_path,
        test_url=config.vpn_testing.test_url,
        timeout_seconds=config.vpn_testing.test_timeout_seconds,
    )
    app = create_app(tester, config.vpn_testing.worker_api_token)
    uvicorn.run(
        app,
        host=config.vpn_testing.worker_listen_host,
        port=config.vpn_testing.worker_listen_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
