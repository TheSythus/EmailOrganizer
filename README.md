# Apple Mail Email Organizer

A Python tool that connects to your email via IMAP and automatically organizes your inbox by moving **advertising/promotional emails** into an `Advertising` folder and **spam** into a `Spam` folder.

Works with **Apple Mail (iCloud)**, Gmail, Outlook, and any IMAP-compatible provider.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure your account

```bash
cp config.example.ini config.ini
```

Edit `config.ini` with your email credentials:

**For iCloud Mail:**
- Host: `imap.mail.me.com`
- Port: `993`
- Generate an app-specific password at [https://appleid.apple.com](https://appleid.apple.com) (Account Security > App-Specific Passwords)

**For Gmail:**
- Host: `imap.gmail.com`
- Port: `993`
- Generate an app-specific password at [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)

### 3. Run (dry run first)

```bash
python -m email_organizer
```

By default, it runs in **dry run mode** — it logs which emails would be moved without actually moving them. Review the output to confirm the classification looks correct.

### 4. Run for real

```bash
python -m email_organizer --no-dry-run
```

## CLI Options

| Flag | Description |
|------|-------------|
| `-c`, `--config` | Path to config file (default: `config.ini`) |
| `-v`, `--verbose` | Enable debug logging |
| `--dry-run` | Preview mode (no emails moved) |
| `--no-dry-run` | Live mode (actually move emails) |
| `--days N` | Only scan emails from the last N days |
| `--batch-size N` | Limit number of emails processed |

## How Classification Works

The classifier uses rule-based detection analyzing:

- **Email headers**: `List-Unsubscribe`, `X-Mailer` (Mailchimp, SendGrid, etc.), spam flags
- **Sender patterns**: `noreply@`, `newsletter@`, `marketing@`, etc.
- **Content keywords**: "unsubscribe", "special offer", "you have won", etc.

Emails are scored on both advertising and spam signals. High-confidence matches are moved; uncertain emails are left in the inbox.

## Folder Structure

```
email_organizer/
  __init__.py
  __main__.py        # python -m entry point
  main.py            # CLI argument parsing and orchestration
  connection.py      # IMAP connection management
  classifier.py      # Rule-based email classification
  organizer.py       # Fetch, classify, and move emails
config.example.ini   # Template configuration
```
