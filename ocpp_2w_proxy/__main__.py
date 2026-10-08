"""Command-line entry point: python -m ocpp_2w_proxy --config config.toml"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
import sys
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, load
from .server import ProxyServer

logger = logging.getLogger("ocpp_2w_proxy")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ocpp-2w-proxy", description="Two-way OCPP 1.6J proxy")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, default=Path("config.toml"), help="TOML config file")
    parser.add_argument(
        "--healthcheck", action="store_true", help="exit 0 if the configured port accepts connections, else 1"
    )
    return parser.parse_args(argv)


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
    await server.start()
    try:
        await stop.wait()
    finally:
        logger.info("shutting down")
        await server.close()


def _healthcheck(config: Config) -> int:
    """For the container health check: the port the proxy was configured with, not a fixed one."""
    host = "127.0.0.1" if config.proxy.listen in ("0.0.0.0", "") else config.proxy.listen
    try:
        socket.create_connection((host, config.proxy.port), 3).close()
    except OSError as exc:
        print(f"health check failed: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = load(args.config)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if args.healthcheck:
        return _healthcheck(config)
    _configure_logging(config.proxy.log_level)
    logger.info("ocpp-2w-proxy %s, config %s", __version__, args.config)
    asyncio.run(_serve(ProxyServer(config)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
