"""Process lifecycle: graceful SIGTERM shutdown and a heartbeat file for container liveness checks.

Consumers have no HTTP port, so liveness is proven by the poll loop touching a file;
`python -m zuvo_common.health` (the compose healthcheck) fails when the file goes stale.
"""
from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Callable, Optional

HEARTBEAT_ENV = "ZUVO_HEARTBEAT_FILE"
DEFAULT_HEARTBEAT_FILE = "/tmp/zuvo-heartbeat"
_MIN_INTERVAL_S = 5.0
_last_beat = 0.0


def heartbeat_path() -> str:
    return os.environ.get(HEARTBEAT_ENV, DEFAULT_HEARTBEAT_FILE)


def heartbeat(force: bool = False) -> None:
    """Touch the heartbeat file (throttled to one write per few seconds)."""
    global _last_beat
    now = time.monotonic()
    if not force and now - _last_beat < _MIN_INTERVAL_S:
        return
    _last_beat = now
    try:
        Path(heartbeat_path()).touch()
    except OSError:
        pass


def heartbeat_age(path: Optional[str] = None) -> Optional[float]:
    try:
        return time.time() - os.stat(path or heartbeat_path()).st_mtime
    except OSError:
        return None


def install_shutdown(on_stop: Callable[[], None], log=None) -> None:
    """SIGTERM/SIGINT -> on_stop() once; the in-flight message finishes and its offset is committed."""
    loop = asyncio.get_running_loop()

    def _handler(name: str) -> None:
        if log:
            log.info(f"{name} received: draining in-flight work, then shutting down")
        on_stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handler, sig.name)
        except (NotImplementedError, RuntimeError):  # non-main thread / unsupported platform
            pass
