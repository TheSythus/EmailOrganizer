"""Email organizer - fetches, classifies, and moves emails into folders."""

import email
import email.header
import email.utils
import imaplib
import logging
from datetime import datetime, timedelta, timezone

from .classifier import ClassificationResult, EmailCategory, EmailClassifier, EmailInfo
from .connection import IMAPConnection

logger = logging.getLogger(__name__)


def _decode_header(raw: str | None) -> str:
    """Decode an RFC 2047 encoded email header into a plain string."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def _extract_body_snippet(msg: email.message.Message, max_len: int = 500) -> str:
    """Extract a plain-text snippet from the email body."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")[:max_len]
        # Fallback: try text/html
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")[:max_len]
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")[:max_len]
    return ""


class EmailOrganizer:
    """Fetches emails from IMAP, classifies them, and moves them to folders."""

    def __init__(
        self,
        connection: IMAPConnection,
        classifier: EmailClassifier,
        source_folder: str = "INBOX",
        ad_folder: str = "Advertising",
        spam_folder: str = "Spam",
        days_to_scan: int = 30,
        batch_size: int = 0,
        dry_run: bool = True,
    ):
        self.connection = connection
        self.classifier = classifier
        self.source_folder = source_folder
        self.ad_folder = ad_folder
        self.spam_folder = spam_folder
        self.days_to_scan = days_to_scan
        self.batch_size = batch_size
        self.dry_run = dry_run

    def run(self) -> dict[str, int]:
        """Execute the full organize pipeline. Returns counts per category."""
        conn = self.connection.conn
        if conn is None:
            raise RuntimeError("Not connected to IMAP server")

        # Ensure target folders exist
        self.connection.ensure_folder(self.ad_folder)
        self.connection.ensure_folder(self.spam_folder)

        # Select source folder
        status, data = conn.select(self.source_folder)
        if status != "OK":
            raise RuntimeError(f"Failed to select folder '{self.source_folder}': {status}")

        total_messages = int(data[0])
        logger.info("Source folder '%s' has %d messages", self.source_folder, total_messages)

        # Build search criteria
        if self.days_to_scan > 0:
            since_date = datetime.now(timezone.utc) - timedelta(days=self.days_to_scan)
            date_str = since_date.strftime("%d-%b-%Y")
            search_criteria = f'(SINCE {date_str})'
        else:
            search_criteria = "ALL"

        status, msg_ids = conn.search(None, search_criteria)
        if status != "OK":
            raise RuntimeError(f"Search failed: {status}")

        uid_list = msg_ids[0].split()
        if not uid_list:
            logger.info("No messages found matching criteria")
            return {"legitimate": 0, "advertising": 0, "spam": 0}

        if self.batch_size > 0:
            uid_list = uid_list[:self.batch_size]

        logger.info("Processing %d emails...", len(uid_list))

        counts = {"legitimate": 0, "advertising": 0, "spam": 0}

        for uid in uid_list:
            try:
                result = self._process_email(conn, uid)
                counts[result.category.value] += 1
            except Exception:
                logger.exception("Error processing email UID %s", uid)

        logger.info(
            "Done. Results: %d legitimate, %d advertising, %d spam",
            counts["legitimate"],
            counts["advertising"],
            counts["spam"],
        )
        return counts

    def _process_email(
        self, conn: imaplib.IMAP4 | imaplib.IMAP4_SSL, uid: bytes
    ) -> ClassificationResult:
        """Fetch, classify, and optionally move a single email."""
        # Fetch headers + partial body
        status, data = conn.fetch(uid, "(RFC822)")
        if status != "OK" or data[0] is None:
            raise RuntimeError(f"Failed to fetch UID {uid}")

        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        # Build EmailInfo
        subject = _decode_header(msg.get("Subject"))
        from_full = _decode_header(msg.get("From"))
        from_name, from_addr = email.utils.parseaddr(from_full)
        to_full = _decode_header(msg.get("To"))
        body_snippet = _extract_body_snippet(msg)

        # Collect headers as dict
        headers: dict[str, str] = {}
        for key in msg.keys():
            headers[key.lower()] = _decode_header(msg.get(key))

        info = EmailInfo(
            uid=uid,
            subject=subject,
            sender=from_addr,
            from_name=from_name,
            to=to_full,
            headers=headers,
            body_snippet=body_snippet,
        )

        # Classify
        result = self.classifier.classify(info)

        log_prefix = "[DRY RUN] " if self.dry_run else ""

        if result.category == EmailCategory.ADVERTISING:
            logger.info(
                "%sADVERTISING (%.0f%%): '%s' from %s — %s",
                log_prefix,
                result.confidence * 100,
                subject[:60],
                from_addr,
                "; ".join(result.reasons),
            )
            if not self.dry_run:
                self._move_email(conn, uid, self.ad_folder)

        elif result.category == EmailCategory.SPAM:
            logger.info(
                "%sSPAM (%.0f%%): '%s' from %s — %s",
                log_prefix,
                result.confidence * 100,
                subject[:60],
                from_addr,
                "; ".join(result.reasons),
            )
            if not self.dry_run:
                self._move_email(conn, uid, self.spam_folder)

        else:
            logger.debug(
                "LEGITIMATE: '%s' from %s",
                subject[:60],
                from_addr,
            )

        return result

    def _move_email(
        self, conn: imaplib.IMAP4 | imaplib.IMAP4_SSL, uid: bytes, dest_folder: str
    ) -> None:
        """Move an email to a destination folder via COPY + delete flag."""
        status, _ = conn.copy(uid, dest_folder)
        if status != "OK":
            raise RuntimeError(f"Failed to copy UID {uid} to '{dest_folder}'")

        conn.store(uid, "+FLAGS", "\\Deleted")
        conn.expunge()
        logger.debug("Moved UID %s to '%s'", uid, dest_folder)
