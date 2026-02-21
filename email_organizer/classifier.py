"""Email classification engine using Claude AI to intelligently categorize emails."""

import json
import logging
from dataclasses import dataclass
from enum import Enum

import anthropic

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


CLASSIFICATION_PROMPT = """\
You are an email classification assistant. Analyze the following email and classify it into exactly one of these categories:

1. **legitimate** — A real, personal, or important email the user wants to see (work emails, personal messages, transactional receipts for purchases the user made, account security alerts from services they use, etc.)
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


class EmailClassifier:
    """Claude AI-powered email classifier."""

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-20250514"):
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model

    def classify(self, email: EmailInfo) -> ClassificationResult:
        """Classify an email using Claude AI."""
        prompt = CLASSIFICATION_PROMPT.format(
            from_name=email.from_name,
            sender=email.sender,
            to=email.to,
            subject=email.subject,
            list_unsubscribe=email.headers.get("list-unsubscribe", "(none)"),
            x_mailer=email.headers.get("x-mailer", "(none)"),
            precedence=email.headers.get("precedence", "(none)"),
            body_snippet=email.body_snippet[:500] if email.body_snippet else "(empty)",
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            result = json.loads(raw)

            category = EmailCategory(result["category"])
            confidence = float(result.get("confidence", 0.8))
            reasons = result.get("reasons", [])

            return ClassificationResult(
                category=category,
                confidence=confidence,
                reasons=reasons,
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                "Failed to parse Claude response for '%s': %s — raw: %s",
                email.subject[:40],
                e,
                raw if "raw" in dir() else "(no response)",
            )
            # Fall back to legitimate to avoid moving emails incorrectly
            return ClassificationResult(
                category=EmailCategory.LEGITIMATE,
                confidence=0.0,
                reasons=[f"Classification failed: {e}"],
            )
        except anthropic.APIError as e:
            logger.error("Claude API error classifying '%s': %s", email.subject[:40], e)
            return ClassificationResult(
                category=EmailCategory.LEGITIMATE,
                confidence=0.0,
                reasons=[f"API error: {e}"],
            )
