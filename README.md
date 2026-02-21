# Apple Mail Email Organizer

A Python tool that connects to your email via IMAP and uses **Claude AI** to intelligently read and classify your inbox emails, automatically moving **advertising/promotional emails** into an `Advertising` folder and **spam** into a `Spam` folder.

Works with **Apple Mail (iCloud)**, Gmail, Outlook, and any IMAP-compatible provider.

## How It Works

1. Connects to your mailbox via IMAP
2. Fetches emails from your inbox (configurable date range and batch size)
3. Sends each email's subject, sender, headers, and body snippet to **Claude** for classification
4. Claude reads the email and decides: **legitimate**, **advertising**, or **spam**
5. Moves advertising and spam emails into their respective folders

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Get a Claude API key

Sign up at [console.anthropic.com](https://console.anthropic.com) and create an API key.

Set it as an environment variable:
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or add it to the config file (see below).

### 3. Configure your email account

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

### 4. Run (dry run first)

```bash
python -m email_organizer
```

By default, it runs in **dry run mode** — it logs which emails would be moved without actually moving them. Review the output to confirm the classification looks correct.

### 5. Run for real

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

Each email is sent to Claude with its subject, sender, key headers, and a body snippet. Claude analyzes the content and classifies it as:

- **Legitimate** — Personal messages, work emails, important transactional emails
- **Advertising** — Newsletters, promotional offers, marketing campaigns
- **Spam** — Scam attempts, phishing, unsolicited junk

If the Claude API call fails for any reason, the email is left in the inbox (safe fallback).

## Folder Structure

```
email_organizer/
  __init__.py
  __main__.py        # python -m entry point
  main.py            # CLI argument parsing and orchestration
  connection.py      # IMAP connection management
  classifier.py      # Claude AI email classification
  organizer.py       # Fetch, classify, and move emails
config.example.ini   # Template configuration
```
