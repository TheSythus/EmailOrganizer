#!/usr/bin/env python3
"""
EmailOrganizer for iPhone (a-Shell)
====================================
A single-file email organizer that uses Claude AI to classify
and sort your iCloud emails into Advertising/Spam folders.

SETUP:
  1. pip install anthropic
  2. Run: python3 email_organizer_iphone.py --setup
  3. Run: python3 email_organizer_iphone.py -v

USAGE:
  python3 email_organizer_iphone.py -v              # Dry run (test, no moves)
  python3 email_organizer_iphone.py --no-dry-run -v # Actually move emails
  python3 email_organizer_iphone.py --days 7 -v     # Only last 7 days
  python3 email_organizer_iphone.py --batch-size 10  # Only 10 emails
"""

import argparse
import configparser
import email
import email.header
import email.utils
import imaplib
import json
import logging
import os
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

# ---------- CONFIG ----------

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")


def run_setup():
    """Interactive setup wizard."""
    print()
    print("=" * 50)
    print("  EmailOrganizer — Setup")
    print("=" * 50)
    print()

    print("Ich brauche 3 Dinge von dir:")
    print()

    print("1) Deine iCloud E-Mail-Adresse")
    email_addr = input("   Email: ").strip()
    print()

    print("2) Ein App-spezifisches Passwort von Apple")
    print("   Geh zu: appleid.apple.com")
    print("   > Anmelden und Sicherheit")
    print("   > App-spezifische Passwoerter")
    print("   > Generieren")
    print("   (Sieht so aus: xxxx-xxxx-xxxx-xxxx)")
    password = input("   Passwort: ").strip()
    print()

    print("3) Dein Anthropic API Key")
    print("   Geh zu: console.anthropic.com/settings/keys")
    print("   (Faengt an mit: sk-ant-...)")
    api_key = input("   API Key: ").strip()
    print()

    config_path = CONFIG_FILE
    with open(config_path, "w") as f:
        f.write(f"""[imap]
host = imap.mail.me.com
port = 993
use_ssl = true

[credentials]
email = {email_addr}
password = {password}

[folders]
source = INBOX
advertising = Werbung
spam = Spam

[claude]
api_key = {api_key}
model = claude-sonnet-4-20250514

[options]
days_to_scan = 30
dry_run = true
batch_size = 20
""")

    print(f"[OK] Config gespeichert: {config_path}")
    print()
    print("Jetzt kannst du starten:")
    print(f"  python3 {os.path.basename(__file__)} -v")
    print()
    sys.exit(0)


# ---------- CLASSIFIER ----------

class EmailCategory(Enum):
    LEGITIMATE = "legitimate"
    ADVERTISING = "advertising"
    SPAM = "spam"


@dataclass
class EmailInfo:
    uid: bytes
    subject: str
    sender: str
    from_name: str
    to: str
    headers: dict
    body_snippet: str


@dataclass
class ClassificationResult:
    category: EmailCategory
    confidence: float
    reasons: list


CLASSIFICATION_PROMPT = """\
You are an email classification assistant. Analyze the following email and classify it into exactly one of these categories:

1. **legitimate** — A real, personal, or important email the user wants to see (work emails, personal messages, transactional receipts, account security alerts, etc.)
2. **advertising** — Promotional/marketing emails, newsletters, product announcements, sale notifications, brand emails the user subscribed to but are not urgent or personal.
3. **spam** — Unsolicited junk mail, scam attempts, phishing, fake offers, deceptive emails the user never signed up for.

Here is the email to classify:

---
**From:** {from_name} <{sender}>
**To:** {to}
**Subject:** {subject}
**Key Headers:**
- List-Unsubscribe: {list_unsubscribe}
- X-Mailer: {x_mailer}
- Precedence: {precedence}

**Body (first 500 chars):**
{body_snippet}
---

Respond with ONLY a JSON object in this exact format, nothing else:
{{"category": "legitimate"|"advertising"|"spam", "confidence": 0.0-1.0, "reasons": ["reason1", "reason2"]}}
"""

logger = logging.getLogger("email_organizer")


class EmailClassifier:
    def __init__(self, api_key=None, model="claude-sonnet-4-20250514"):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model

    def classify(self, email_info):
        prompt = CLASSIFICATION_PROMPT.format(
            from_name=email_info.from_name,
            sender=email_info.sender,
            to=email_info.to,
            subject=email_info.subject,
            list_unsubscribe=email_info.headers.get("list-unsubscribe", "(none)"),
            x_mailer=email_info.headers.get("x-mailer", "(none)"),
            precedence=email_info.headers.get("precedence", "(none)"),
            body_snippet=email_info.body_snippet[:500] if email_info.body_snippet else "(empty)",
        )
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()
            result = json.loads(raw)
            category = EmailCategory(result["category"])
            confidence = float(result.get("confidence", 0.8))
            reasons = result.get("reasons", [])
            return ClassificationResult(category=category, confidence=confidence, reasons=reasons)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Failed to parse response for '%s': %s", email_info.subject[:40], e)
            return ClassificationResult(category=EmailCategory.LEGITIMATE, confidence=0.0, reasons=[f"Parse error: {e}"])
        except self.anthropic.APIError as e:
            logger.error("API error for '%s': %s", email_info.subject[:40], e)
            return ClassificationResult(category=EmailCategory.LEGITIMATE, confidence=0.0, reasons=[f"API error: {e}"])


# ---------- IMAP CONNECTION ----------

class IMAPConnection:
    def __init__(self, host, port, email_addr, password, use_ssl=True):
        self.host = host
        self.port = port
        self.email_addr = email_addr
        self.password = password
        self.use_ssl = use_ssl
        self.conn = None

    def connect(self):
        logger.info("Connecting to %s:%d ...", self.host, self.port)
        if self.use_ssl:
            ctx = ssl.create_default_context()
            self.conn = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx)
        else:
            self.conn = imaplib.IMAP4(self.host, self.port)
        self.conn.login(self.email_addr, self.password)
        logger.info("Authenticated as %s", self.email_addr)
        return self.conn

    def ensure_folder(self, folder_name):
        if self.conn is None:
            raise RuntimeError("Not connected")
        status, folders = self.conn.list()
        if status != "OK":
            raise RuntimeError("Failed to list folders")
        existing = set()
        for item in folders:
            if item is None:
                continue
            decoded = item.decode() if isinstance(item, bytes) else item
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

    def disconnect(self):
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


# ---------- ORGANIZER ----------

def _decode_header(raw):
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


def _extract_body_snippet(msg, max_len=500):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")[:max_len]
        for part in msg.walk():
            if part.get_content_type() == "text/html":
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
    def __init__(self, connection, classifier, source_folder="INBOX",
                 ad_folder="Advertising", spam_folder="Spam",
                 days_to_scan=30, batch_size=0, dry_run=True):
        self.connection = connection
        self.classifier = classifier
        self.source_folder = source_folder
        self.ad_folder = ad_folder
        self.spam_folder = spam_folder
        self.days_to_scan = days_to_scan
        self.batch_size = batch_size
        self.dry_run = dry_run

    def run(self):
        conn = self.connection.conn
        if conn is None:
            raise RuntimeError("Not connected to IMAP server")
        self.connection.ensure_folder(self.ad_folder)
        self.connection.ensure_folder(self.spam_folder)
        status, data = conn.select(self.source_folder)
        if status != "OK":
            raise RuntimeError(f"Failed to select folder '{self.source_folder}'")
        total = int(data[0])
        logger.info("'%s' has %d messages", self.source_folder, total)
        if self.days_to_scan > 0:
            since = datetime.now(timezone.utc) - timedelta(days=self.days_to_scan)
            search = f'(SINCE {since.strftime("%d-%b-%Y")})'
        else:
            search = "ALL"
        status, msg_ids = conn.search(None, search)
        if status != "OK":
            raise RuntimeError(f"Search failed: {status}")
        uid_list = msg_ids[0].split()
        if not uid_list:
            logger.info("No messages found")
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
                logger.exception("Error processing UID %s", uid)
        logger.info("Done: %d legitimate, %d advertising, %d spam",
                     counts["legitimate"], counts["advertising"], counts["spam"])
        return counts

    def _process_email(self, conn, uid):
        status, data = conn.fetch(uid, "(RFC822)")
        if status != "OK" or data[0] is None:
            raise RuntimeError(f"Failed to fetch UID {uid}")
        msg = email.message_from_bytes(data[0][1])
        subject = _decode_header(msg.get("Subject"))
        from_full = _decode_header(msg.get("From"))
        from_name, from_addr = email.utils.parseaddr(from_full)
        to_full = _decode_header(msg.get("To"))
        body_snippet = _extract_body_snippet(msg)
        headers = {}
        for key in msg.keys():
            headers[key.lower()] = _decode_header(msg.get(key))
        info = EmailInfo(
            uid=uid, subject=subject, sender=from_addr,
            from_name=from_name, to=to_full,
            headers=headers, body_snippet=body_snippet,
        )
        result = self.classifier.classify(info)
        prefix = "[DRY RUN] " if self.dry_run else ""
        if result.category == EmailCategory.ADVERTISING:
            logger.info("%sADVERTISING (%.0f%%): '%s' from %s -- %s",
                        prefix, result.confidence * 100, subject[:60],
                        from_addr, "; ".join(result.reasons))
            if not self.dry_run:
                self._move_email(conn, uid, self.ad_folder)
        elif result.category == EmailCategory.SPAM:
            logger.info("%sSPAM (%.0f%%): '%s' from %s -- %s",
                        prefix, result.confidence * 100, subject[:60],
                        from_addr, "; ".join(result.reasons))
            if not self.dry_run:
                self._move_email(conn, uid, self.spam_folder)
        else:
            logger.debug("LEGITIMATE: '%s' from %s", subject[:60], from_addr)
        return result

    def _move_email(self, conn, uid, dest):
        status, _ = conn.copy(uid, dest)
        if status != "OK":
            raise RuntimeError(f"Failed to copy UID {uid} to '{dest}'")
        conn.store(uid, "+FLAGS", "\\Deleted")
        conn.expunge()
        logger.debug("Moved UID %s -> '%s'", uid, dest)


# ---------- MAIN ----------

def main():
    parser = argparse.ArgumentParser(description="Email Organizer — sortiert Werbung & Spam")
    parser.add_argument("--setup", action="store_true", help="Interactive setup wizard")
    parser.add_argument("-c", "--config", default=CONFIG_FILE, help="Config file path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--dry-run", action="store_true", default=None, help="Test mode (default)")
    parser.add_argument("--no-dry-run", action="store_true", help="Actually move emails")
    parser.add_argument("--days", type=int, default=None, help="Only scan last N days")
    parser.add_argument("--batch-size", type=int, default=None, help="Max emails to process")
    args = parser.parse_args()

    if args.setup:
        run_setup()

    # Logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    # Load config
    if not Path(args.config).exists():
        print(f"Config nicht gefunden: {args.config}")
        print(f"Zuerst Setup ausfuehren:  python3 {os.path.basename(__file__)} --setup")
        sys.exit(1)
    config = configparser.ConfigParser()
    config.read(args.config)

    # Read settings
    host = config.get("imap", "host")
    port = config.getint("imap", "port", fallback=993)
    use_ssl = config.getboolean("imap", "use_ssl", fallback=True)
    email_addr = config.get("credentials", "email")
    password = config.get("credentials", "password")
    source = config.get("folders", "source", fallback="INBOX")
    ad_folder = config.get("folders", "advertising", fallback="Werbung")
    spam_folder = config.get("folders", "spam", fallback="Spam")
    days = args.days if args.days is not None else config.getint("options", "days_to_scan", fallback=30)
    batch_size = args.batch_size if args.batch_size is not None else config.getint("options", "batch_size", fallback=0)
    api_key = config.get("claude", "api_key", fallback="") or None
    model = config.get("claude", "model", fallback="claude-sonnet-4-20250514")

    if args.no_dry_run:
        dry_run = False
    elif args.dry_run:
        dry_run = True
    else:
        dry_run = config.getboolean("options", "dry_run", fallback=True)

    if dry_run:
        logger.info("=== TEST MODUS — keine Emails werden verschoben ===")
    else:
        logger.info("=== LIVE MODUS — Emails WERDEN verschoben ===")

    # Run
    conn_obj = IMAPConnection(host, port, email_addr, password, use_ssl)
    classifier = EmailClassifier(api_key=api_key, model=model)
    with conn_obj:
        organizer = EmailOrganizer(
            connection=conn_obj, classifier=classifier,
            source_folder=source, ad_folder=ad_folder,
            spam_folder=spam_folder, days_to_scan=days,
            batch_size=batch_size, dry_run=dry_run,
        )
        counts = organizer.run()

    print("\n--- Ergebnis ---")
    print(f"  Legitim:  {counts['legitimate']}")
    print(f"  Werbung:  {counts['advertising']}")
    print(f"  Spam:     {counts['spam']}")
    moved = counts["advertising"] + counts["spam"]
    if dry_run:
        print(f"\n  Wuerde {moved} Email(s) verschieben.")
        print(f"  Zum wirklich verschieben:  python3 {os.path.basename(__file__)} --no-dry-run -v")
    else:
        print(f"\n  {moved} Email(s) verschoben.")


if __name__ == "__main__":
    main()
