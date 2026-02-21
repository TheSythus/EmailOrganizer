"""CLI entry point for the Apple Mail Email Organizer."""

import argparse
import configparser
import logging
import sys
from pathlib import Path

from .classifier import EmailClassifier
from .connection import IMAPConnection
from .organizer import EmailOrganizer

DEFAULT_CONFIG = "config.ini"


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path: str) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    if not Path(path).exists():
        print(f"Error: Config file '{path}' not found.")
        print(f"Copy config.example.ini to {path} and fill in your credentials.")
        sys.exit(1)
    config.read(path)
    return config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apple Mail Email Organizer — classify and sort advertising & spam emails"
    )
    parser.add_argument(
        "-c", "--config",
        default=DEFAULT_CONFIG,
        help=f"Path to config file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Log what would be moved without actually moving emails",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually move emails (overrides config file dry_run setting)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only scan emails from the last N days",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Max number of emails to process",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    logger = logging.getLogger(__name__)

    config = load_config(args.config)

    # Build connection
    host = config.get("imap", "host")
    port = config.getint("imap", "port", fallback=993)
    use_ssl = config.getboolean("imap", "use_ssl", fallback=True)
    email_addr = config.get("credentials", "email")
    password = config.get("credentials", "password")

    # Folder settings
    source = config.get("folders", "source", fallback="INBOX")
    ad_folder = config.get("folders", "advertising", fallback="Advertising")
    spam_folder = config.get("folders", "spam", fallback="Spam")

    # Options
    days = args.days if args.days is not None else config.getint("options", "days_to_scan", fallback=30)
    batch_size = args.batch_size if args.batch_size is not None else config.getint("options", "batch_size", fallback=0)

    if args.no_dry_run:
        dry_run = False
    elif args.dry_run:
        dry_run = True
    else:
        dry_run = config.getboolean("options", "dry_run", fallback=True)

    if dry_run:
        logger.info("=== DRY RUN MODE — no emails will be moved ===")
    else:
        logger.info("=== LIVE MODE — emails WILL be moved ===")

    conn = IMAPConnection(host, port, email_addr, password, use_ssl)
    classifier = EmailClassifier()

    with conn:
        organizer = EmailOrganizer(
            connection=conn,
            classifier=classifier,
            source_folder=source,
            ad_folder=ad_folder,
            spam_folder=spam_folder,
            days_to_scan=days,
            batch_size=batch_size,
            dry_run=dry_run,
        )
        counts = organizer.run()

    print("\n--- Summary ---")
    print(f"  Legitimate:  {counts['legitimate']}")
    print(f"  Advertising: {counts['advertising']}")
    print(f"  Spam:        {counts['spam']}")
    total_moved = counts["advertising"] + counts["spam"]
    if dry_run:
        print(f"\n  Would move {total_moved} email(s). Run with --no-dry-run to apply.")
    else:
        print(f"\n  Moved {total_moved} email(s).")


if __name__ == "__main__":
    main()
