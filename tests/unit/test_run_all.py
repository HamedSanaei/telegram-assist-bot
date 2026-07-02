"""Unit tests for the all-in-one entrypoint supervisor logic."""

from __future__ import annotations

import asyncio

import pytest

from src.run_all import supervise
from src.shared.errors import ConfigurationError


class TestSupervise:
    """Tests for :func:`supervise`."""

    async def test_configuration_error_stops_component_permanently(self) -> None:
        calls = 0

        async def broken_component() -> None:
            nonlocal calls
            calls += 1
            raise ConfigurationError("api_id missing")

        await supervise("collector", broken_component, restart_delay_seconds=0.001)
        assert calls == 1

    async def test_crashing_component_is_restarted(self) -> None:
        calls = 0
        done = asyncio.Event()

        async def flaky_component() -> None:
            nonlocal calls
            calls += 1
            if calls >= 3:
                done.set()
                await asyncio.sleep(3600)
            raise RuntimeError("boom")

        task = asyncio.create_task(
            supervise("main-app", flaky_component, restart_delay_seconds=0.001)
        )
        await asyncio.wait_for(done.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == 3

    async def test_clean_exit_is_also_restarted(self) -> None:
        calls = 0
        done = asyncio.Event()

        async def short_lived_component() -> None:
            nonlocal calls
            calls += 1
            if calls >= 2:
                done.set()
                await asyncio.sleep(3600)

        task = asyncio.create_task(
            supervise("main-app", short_lived_component, restart_delay_seconds=0.001)
        )
        await asyncio.wait_for(done.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == 2

    async def test_cancellation_propagates_immediately(self) -> None:
        started = asyncio.Event()

        async def long_running_component() -> None:
            started.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(supervise("main-app", long_running_component))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
