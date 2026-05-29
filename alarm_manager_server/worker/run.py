"""Background worker: poll /process and print grouped incident links."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime

from alarm_manager_server.config import settings
from alarm_manager_server.worker.client import ProcessApiClient
from alarm_manager_server.worker.formatter import build_groups, format_groups

logger = logging.getLogger(__name__)


async def run_once(client: ProcessApiClient, *, resolve_macros: bool) -> None:
    incidents, grouping = await client.process(resolve_macros=resolve_macros)
    groups = build_groups(incidents, grouping, settings)
    text = format_groups(groups)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"--- {stamp} — {len(groups)} group(s) ---", flush=True)
    if text:
        print(text, flush=True)
    else:
        print("(no incidents)", flush=True)


async def run_loop(
    client: ProcessApiClient,
    *,
    interval_sec: float,
    resolve_macros: bool,
) -> None:
    while True:
        try:
            await run_once(client, resolve_macros=resolve_macros)
        except Exception:
            logger.exception("processing failed")
        await asyncio.sleep(interval_sec)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Poll alarm-manager-server, group incidents, print links to stdout.",
    )
    parser.add_argument(
        "--server-url",
        default=settings.server_url,
        help=f"alarm-manager-server base URL (default: {settings.server_url})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=settings.worker_interval_sec,
        help=f"Poll interval in seconds; 0 = run once (default: {settings.worker_interval_sec})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle and exit (same as --interval 0)",
    )
    parser.add_argument(
        "--no-macros",
        action="store_true",
        help="Skip responsible-party macro resolution on the server",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    interval = 0.0 if args.once else args.interval
    resolve_macros = not args.no_macros
    client = ProcessApiClient(args.server_url)

    if interval <= 0:
        try:
            asyncio.run(run_once(client, resolve_macros=resolve_macros))
        except KeyboardInterrupt:
            sys.exit(130)
        except Exception:
            logger.exception("processing failed")
            sys.exit(1)
        return

    logger.info("Worker started: %s every %.0fs", args.server_url, interval)
    try:
        asyncio.run(
            run_loop(client, interval_sec=interval, resolve_macros=resolve_macros),
        )
    except KeyboardInterrupt:
        logger.info("Worker stopped")
        sys.exit(130)
