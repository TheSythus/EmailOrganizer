"""IMAP connection management for Apple Mail / any IMAP provider.

Supports connecting through HTTP proxies via the CONNECT tunnel method,
which is needed in environments where only HTTP proxy access is available.
"""

import imaplib
import os
import socket
import ssl
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _create_proxy_tunnel(proxy_url: str, target_host: str, target_port: int) -> socket.socket:
    """Create a TCP tunnel through an HTTP proxy using the CONNECT method."""
    parsed = urlparse(proxy_url)
    proxy_host = parsed.hostname
    proxy_port = parsed.port or 3128

    logger.info("Tunneling through proxy %s:%d to %s:%d", proxy_host, proxy_port, target_host, target_port)

    sock = socket.create_connection((proxy_host, proxy_port), timeout=30)

    # Build CONNECT request
    connect_req = f"CONNECT {target_host}:{target_port} HTTP/1.1\r\nHost: {target_host}:{target_port}\r\n"

    # Add proxy authentication if present
    if parsed.username:
        import base64
        credentials = f"{parsed.username}:{parsed.password or ''}"
        encoded = base64.b64encode(credentials.encode()).decode()
        connect_req += f"Proxy-Authorization: Basic {encoded}\r\n"

    connect_req += "\r\n"
    sock.sendall(connect_req.encode())

    # Read proxy response
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("Proxy closed connection before completing CONNECT handshake")
        response += chunk

    status_line = response.split(b"\r\n")[0].decode()
    logger.debug("Proxy CONNECT response: %s", status_line)

    if " 200 " not in status_line:
        sock.close()
        raise ConnectionError(f"Proxy CONNECT failed: {status_line}")

    return sock


def _get_proxy_url() -> str | None:
    """Detect HTTP proxy from environment variables."""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        url = os.environ.get(var)
        if url:
            return url
    return None


class IMAP4_SSL_via_proxy(imaplib.IMAP4_SSL):
    """IMAP4_SSL that connects through an HTTP proxy tunnel."""

    def __init__(self, host: str, port: int, proxy_url: str, ssl_context: ssl.SSLContext | None = None):
        self._proxy_url = proxy_url
        self._target_host = host
        self._target_port = port
        self._ssl_context = ssl_context or ssl.create_default_context()
        # IMAP4.__init__ calls self.open() internally
        imaplib.IMAP4.__init__(self, host, port)

    def open(self, host: str = "", port: int = 993, timeout: float | None = None):
        """Override to establish connection via proxy tunnel + SSL."""
        self.host = host or self._target_host
        self.port = port or self._target_port

        # Create tunnel through proxy
        raw_sock = _create_proxy_tunnel(self._proxy_url, self.host, self.port)
        raw_sock.settimeout(60)

        # Wrap in SSL
        self.sock = self._ssl_context.wrap_socket(raw_sock, server_hostname=self.host)
        self.sock.settimeout(60)
        self.file = self.sock.makefile("rb")


class IMAPConnection:
    """Manages IMAP connection lifecycle. Auto-detects and uses HTTP proxy if available."""

    def __init__(self, host: str, port: int, email: str, password: str, use_ssl: bool = True):
        self.host = host
        self.port = port
        self.email = email
        self.password = password
        self.use_ssl = use_ssl
        self.conn: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None

    def connect(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        """Establish IMAP connection and authenticate. Uses proxy if detected."""
        logger.info("Connecting to %s:%d ...", self.host, self.port)

        proxy_url = _get_proxy_url()

        if proxy_url and self.use_ssl:
            ctx = ssl.create_default_context()
            self.conn = IMAP4_SSL_via_proxy(self.host, self.port, proxy_url, ssl_context=ctx)
        elif self.use_ssl:
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
