"""Sample process resources without external services or message payloads."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def process_resources() -> dict[str, int | float]:
    """Return CPU seconds and resident bytes; omit unavailable memory samples.

    CPU is cumulative process user plus system time, not container CPU. Linux
    current RSS comes from procfs; peak RSS uses getrusage on POSIX platforms.
    Sampling happens per crawl, never per message.
    """
    times = os.times()
    result: dict[str, int | float] = {"cpu_seconds": times.user + times.system}
    if sys.platform == "linux":
        try:
            fields = Path("/proc/self/statm").read_text(encoding="utf-8").split()
            result["rss_bytes"] = int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            pass  # procfs can be unavailable in a restricted container.
    if sys.platform != "win32":
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["peak_rss_bytes"] = int(
            peak if sys.platform == "darwin" else peak * 1024
        )
    elif sys.platform == "win32":
        result.update(_windows_memory())
    return result


def _windows_memory() -> dict[str, int]:
    """Read Windows counters, or return no samples on other platforms."""
    result: dict[str, int] = {}
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MemoryCounters(ctypes.Structure):
            _fields_ = (
                ("cb", wintypes.DWORD),
                ("page_faults", wintypes.DWORD),
                ("peak_working_set", ctypes.c_size_t),
                ("working_set", ctypes.c_size_t),
                ("peak_paged_pool", ctypes.c_size_t),
                ("paged_pool", ctypes.c_size_t),
                ("peak_nonpaged_pool", ctypes.c_size_t),
                ("nonpaged_pool", ctypes.c_size_t),
                ("pagefile", ctypes.c_size_t),
                ("peak_pagefile", ctypes.c_size_t),
            )

        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(MemoryCounters),
            wintypes.DWORD,
        )
        if psapi.GetProcessMemoryInfo(
            kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            result = {
                "rss_bytes": counters.working_set,
                "peak_rss_bytes": counters.peak_working_set,
            }
    return result
