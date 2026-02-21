"""IMAP connection management for Apple Mail / any IMAP provider."""

import imaplib
import ssl
import logging

logger = logging.getLogger(__name__)


class IMAPConnection:
    """Manages IMAP connection lifecycle."""

    def __init__(self, host: str, port: int, email: str, password: str, use_ssl: bool = True):
        self.host = host
        self.port = port
        self.email = email
        self.password = password
        self.use_ssl = use_ssl
        self.conn: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None

    def connect(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        """Establish IMAP connection and authenticate."""
        logger.info("Connecting to %s:%d ...", self.host, self.port)

        if self.use_ssl:
            ctx = ssl.create_default_context()
            self.conn = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx)
        else:
            self.conn = imaplib.IMAP4(self.host, self.port)

        self.conn.login(self.email, self.password)
        logger.info("Authenticated as %s", self.email)
        return self.conn

    def ensure_folder(self, folder_name: str) -> None:
        """Create an IMAP folder if it doesn't already exist."""
        if self.conn is None:
            raise RuntimeError("Not connected")

        status, folders = self.conn.list()
        if status != "OK":
            raise RuntimeError(f"Failed to list folders: {status}")

        existing = set()
        for item in folders:
            if item is None:
                continue
            decoded = item.decode() if isinstance(item, bytes) else item
            # Folder names appear after the last ") " in the LIST response
            parts = decoded.rsplit(') "', 1)
            if len(parts) == 2:
                name = parts[1].strip().strip('"').rsplit('" ', 1)[-1].strip('"')
                existing.add(name)

        if folder_name not in existing:
            logger.info("Creating folder: %s", folder_name)
            status, _ = self.conn.create(folder_name)
            if status != "OK":
                raise RuntimeError(f"Failed to create folder '{folder_name}'")
            self.conn.subscribe(folder_name)
        else:
            logger.debug("Folder already exists: %s", folder_name)

    def disconnect(self) -> None:
        """Close the IMAP connection."""
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            try:
                self.conn.logout()
            except Exception:
                pass
            self.conn = None
            logger.info("Disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
