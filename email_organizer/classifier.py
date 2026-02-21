"""Email classification engine - detects advertising and spam emails."""

import re
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


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
    headers: dict[str, str]
    body_snippet: str


@dataclass
class ClassificationResult:
    category: EmailCategory
    confidence: float  # 0.0 to 1.0
    reasons: list[str]


# --- Known spam / advertising signals ---

ADVERTISING_KEYWORDS = [
    "unsubscribe", "opt out", "opt-out", "email preferences",
    "manage subscriptions", "subscription preferences",
    "view in browser", "view this email in",
    "no longer wish to receive", "update your preferences",
    "promotional", "special offer", "limited time",
    "exclusive deal", "discount code", "coupon",
    "free shipping", "order now", "shop now", "buy now",
    "sale ends", "flash sale", "clearance",
    "newsletter", "weekly digest", "daily digest",
]

SPAM_KEYWORDS = [
    "you have won", "you've won", "congratulations you",
    "claim your prize", "lottery winner",
    "nigerian prince", "wire transfer",
    "viagra", "cialis", "pharmacy",
    "make money fast", "work from home opportunity",
    "double your income", "financial freedom",
    "click here immediately", "act now or",
    "this is not spam", "this isn't spam",
    "your account has been compromised",
    "verify your identity immediately",
    "suspended account", "account verification required",
    "dear valued customer", "dear account holder",
]

ADVERTISING_SENDER_PATTERNS = [
    r"no[-_]?reply@",
    r"noreply@",
    r"newsletter@",
    r"marketing@",
    r"promotions?@",
    r"offers?@",
    r"deals@",
    r"info@",
    r"updates?@",
    r"notifications?@",
    r"hello@",
    r"team@",
    r"news@",
]

SPAM_HEADER_INDICATORS = [
    # Emails with high spam scores from server-side filters
    ("x-spam-status", r"yes", 0.8),
    ("x-spam-flag", r"yes", 0.8),
    # Bulk mail precedence
    ("precedence", r"bulk", 0.3),
    # Missing or suspicious authentication
    ("authentication-results", r"fail", 0.4),
    ("authentication-results", r"none", 0.2),
]

ADVERTISING_HEADER_INDICATORS = [
    ("precedence", r"bulk", 0.3),
    ("list-unsubscribe", r".", 0.5),
    ("x-mailer", r"mailchimp|sendgrid|mailgun|constant.?contact|hubspot|marketo|campaign.?monitor", 0.6),
    ("x-sg-id", r".", 0.5),  # SendGrid
    ("x-mc-user", r".", 0.5),  # Mailchimp
]


class EmailClassifier:
    """Rule-based email classifier for advertising and spam detection."""

    def classify(self, email: EmailInfo) -> ClassificationResult:
        """Classify an email as legitimate, advertising, or spam."""
        spam_score = 0.0
        ad_score = 0.0
        reasons: list[str] = []

        # --- Header analysis ---
        for header_name, pattern, weight in SPAM_HEADER_INDICATORS:
            value = email.headers.get(header_name.lower(), "")
            if re.search(pattern, value, re.IGNORECASE):
                spam_score += weight
                reasons.append(f"Spam header: {header_name} matches '{pattern}'")

        for header_name, pattern, weight in ADVERTISING_HEADER_INDICATORS:
            value = email.headers.get(header_name.lower(), "")
            if re.search(pattern, value, re.IGNORECASE):
                ad_score += weight
                reasons.append(f"Ad header: {header_name} matches '{pattern}'")

        # --- Sender analysis ---
        sender_lower = email.sender.lower()
        for pattern in ADVERTISING_SENDER_PATTERNS:
            if re.search(pattern, sender_lower):
                ad_score += 0.3
                reasons.append(f"Sender matches ad pattern: {pattern}")
                break

        # --- Content analysis (subject + body snippet) ---
        text = (email.subject + " " + email.body_snippet).lower()

        ad_keyword_hits = 0
        for kw in ADVERTISING_KEYWORDS:
            if kw in text:
                ad_keyword_hits += 1
        if ad_keyword_hits > 0:
            weight = min(ad_keyword_hits * 0.15, 0.6)
            ad_score += weight
            reasons.append(f"Found {ad_keyword_hits} advertising keyword(s)")

        spam_keyword_hits = 0
        for kw in SPAM_KEYWORDS:
            if kw in text:
                spam_keyword_hits += 1
        if spam_keyword_hits > 0:
            weight = min(spam_keyword_hits * 0.25, 0.8)
            spam_score += weight
            reasons.append(f"Found {spam_keyword_hits} spam keyword(s)")

        # --- Decision ---
        # Spam takes priority if both scores are high
        if spam_score >= 0.6:
            confidence = min(spam_score, 1.0)
            return ClassificationResult(EmailCategory.SPAM, confidence, reasons)

        if ad_score >= 0.5:
            confidence = min(ad_score, 1.0)
            return ClassificationResult(EmailCategory.ADVERTISING, confidence, reasons)

        # Low scores - mark as legitimate
        reasons.append("No strong spam or advertising signals detected")
        return ClassificationResult(EmailCategory.LEGITIMATE, 1.0 - max(spam_score, ad_score), reasons)
