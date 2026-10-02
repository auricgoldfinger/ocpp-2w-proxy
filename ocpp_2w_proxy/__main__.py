"""Command-line entry point: python -m ocpp_2w_proxy --config config.toml"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load
from .server import ProxyServer

logger = logging.getLogger("ocpp_2w_proxy")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ocpp-2w-proxy", description="Two-way OCPP 1.6J proxy")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, default=Path("config.toml"), help="TOML config file")
    return parser.parse_args()


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=level,
    )
    # websockets logs full handshakes (incl. headers) at DEBUG; keep it quieter.
    logging.getLogger("websockets").setLevel(logging.INFO if level == "DEBUG" else logging.WARNING)


async def _serve(server: ProxyServer) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    ws_server = await server.start()
    await stop.wait()
    logger.info("shutting down")
    ws_server.close()
    await ws_server.wait_closed()


def main() -> int:
    args = _parse_args()
    try:
        config = load(args.config)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    _configure_logging(config.proxy.log_level)
    logger.info("ocpp-2w-proxy %s, config %s", __version__, args.config)
    asyncio.run(_serve(ProxyServer(config)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
