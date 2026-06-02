"""Background worker: poll /process and print grouped incident links."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime

from alarm_manager_server.config import settings
from alarm_manager_server.worker.client import ProcessApiClient
from alarm_manager_server.worker.formatter import build_groups, build_tracked_groups, format_groups
from alarm_manager_server.worker.ticket_handlers import (
    dispatch_ticket_handlers,
    load_ticket_handlers,
    parse_handler_specs,
)
from alarm_manager_server.worker.tickets import TicketStore, format_ticket_events, sync_tickets

logger = logging.getLogger(__name__)


async def run_once(
    client: ProcessApiClient,
    *,
    resolve_macros: bool,
    active_only: bool,
    show_responsible: bool,
    track_tickets: bool = False,
    tickets_file: str | None = None,
    ticket_handler_specs: list[str] | None = None,
) -> None:
    incidents, grouping = await client.process(resolve_macros=resolve_macros)
    incidents_by_id = {inc.id: inc for inc in incidents}

    if track_tickets:
        all_tracked = build_tracked_groups(
            incidents,
            grouping,
            settings,
            active_only=False,
            show_responsible=show_responsible,
        )
        visible_tracked = (
            build_tracked_groups(
                incidents,
                grouping,
                settings,
                active_only=True,
                show_responsible=show_responsible,
            )
            if active_only
            else all_tracked
        )
        visible_keys = {g.group_key for g in visible_tracked}
        store = TicketStore(tickets_file or settings.tickets_file)
        sync_result = sync_tickets(store, all_tracked, incidents_by_id=incidents_by_id)
        events = [
            e
            for e in sync_result.events
            if e.action == "closed"
            or (e.group is not None and e.group.group_key in visible_keys)
        ]
        handler_specs = parse_handler_specs(
            cli_handlers=ticket_handler_specs,
            env_value=settings.ticket_handlers,
        )
        if handler_specs:
            handlers = load_ticket_handlers(handler_specs)
            dispatch_ticket_handlers(handlers, store, events)
        groups = [g.display for g in visible_tracked]
        text = format_ticket_events(events)
        incident_rows = sum(len(group.rows) for group in groups)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(
            f"--- {stamp} — tickets: {sync_result.open_count} open; "
            f"+{sync_result.created} ~{sync_result.updated} −{sync_result.closed}; "
            f"{len(groups)} visible group(s), {incident_rows} row(s) ---",
            flush=True,
        )
        if text:
            print(text, flush=True)
        elif not events and not groups:
            print("(no changes, no active groups)", flush=True)
        elif not events:
            print("(no ticket changes)", flush=True)
        return

    groups = build_groups(
        incidents,
        grouping,
        settings,
        active_only=active_only,
        show_responsible=show_responsible,
    )
    text = format_groups(groups)
    incident_rows = sum(len(group.rows) for group in groups)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(
        f"--- {stamp} — {len(groups)} group(s), {incident_rows} incident row(s) ---",
        flush=True,
    )
    if text:
        print(text, flush=True)
    else:
        print("(no incidents)", flush=True)


async def run_loop(
    client: ProcessApiClient,
    *,
    interval_sec: float,
    resolve_macros: bool,
    active_only: bool,
    show_responsible: bool,
    track_tickets: bool = False,
    tickets_file: str | None = None,
    ticket_handler_specs: list[str] | None = None,
) -> None:
    while True:
        try:
            await run_once(
                client,
                resolve_macros=resolve_macros,
                active_only=active_only,
                show_responsible=show_responsible,
                track_tickets=track_tickets,
                tickets_file=tickets_file,
                ticket_handler_specs=ticket_handler_specs,
            )
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
        "--active",
        action="store_true",
        help="Hide groups where all incidents are Cleared",
    )
    parser.add_argument(
        "--responsible",
        action="store_true",
        help="Print resolved responsible parties (enables macro resolution)",
    )
    parser.add_argument(
        "--tickets",
        action="store_true",
        help="Track groups as tickets between runs (create/update/close); needs writable TICKETS_FILE",
    )
    parser.add_argument(
        "--tickets-file",
        default=None,
        help=f"Path to tickets JSON (default: {settings.tickets_file})",
    )
    parser.add_argument(
        "--ticket-handler",
        action="append",
        default=None,
        metavar="MODULE:CLASS",
        help=(
            "Python handler for external ticket system (repeatable). "
            "Also TICKET_HANDLERS env (comma-separated). "
            "Built-in: alarm_manager_server.worker.ticket_handlers:LoggingTicketHandler"
        ),
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
    if args.responsible and args.no_macros:
        parser.error("--responsible cannot be used together with --no-macros")
    resolve_macros = True if args.responsible else not args.no_macros
    active_only = args.active
    show_responsible = args.responsible
    track_tickets = args.tickets
    tickets_file = args.tickets_file
    ticket_handler_specs = args.ticket_handler
    if ticket_handler_specs and not track_tickets:
        parser.error("--ticket-handler requires --tickets")
    client = ProcessApiClient(args.server_url)

    if interval <= 0:
        try:
            asyncio.run(
                run_once(
                    client,
                    resolve_macros=resolve_macros,
                    active_only=active_only,
                    show_responsible=show_responsible,
                    track_tickets=track_tickets,
                    tickets_file=tickets_file,
                    ticket_handler_specs=ticket_handler_specs,
                ),
            )
        except KeyboardInterrupt:
            sys.exit(130)
        except Exception:
            logger.exception("processing failed")
            sys.exit(1)
        return

    logger.info("Worker started: %s every %.0fs", args.server_url, interval)
    try:
        asyncio.run(
            run_loop(
                client,
                interval_sec=interval,
                resolve_macros=resolve_macros,
                active_only=active_only,
                show_responsible=show_responsible,
                track_tickets=track_tickets,
                tickets_file=tickets_file,
                ticket_handler_specs=ticket_handler_specs,
            ),
        )
    except KeyboardInterrupt:
        logger.info("Worker stopped")
        sys.exit(130)
