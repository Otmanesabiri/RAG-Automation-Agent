"""Outbound notifications: webhook (JSON POST) and e-mail (SMTP)."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

import requests

from ..observability.logging import get_logger

logger = get_logger(__name__)


class Notifier:
    """Sends a subject + Markdown body to the configured channels.

    Channels are configured server-side: NOTIFY_WEBHOOK_URL (Slack/Teams/
    generic JSON endpoint) and/or SMTP_HOST + NOTIFY_EMAIL_TO.
    """

    def __init__(self, webhook_url: Optional[str] = None, email_to: Optional[List[str]] = None, session=None) -> None:
        self.webhook_url = webhook_url if webhook_url is not None else os.getenv("NOTIFY_WEBHOOK_URL", "")
        raw_to = os.getenv("NOTIFY_EMAIL_TO", "")
        self.email_to = email_to if email_to is not None else [a.strip() for a in raw_to.split(",") if a.strip()]
        self.session = session or requests.Session()

    @property
    def channels(self) -> List[str]:
        return [
            name for name, on in (("webhook", self.webhook_url), ("email", self.email_to and os.getenv("SMTP_HOST"))) if on
        ]

    def send(self, subject: str, body: str, data: Optional[Dict[str, Any]] = None) -> List[str]:
        """Deliver to every channel; returns the channels that succeeded (never raises)."""
        delivered = []
        if self.webhook_url:
            try:
                # "text" is understood by Slack and Teams incoming webhooks.
                payload = {"text": f"*{subject}*\n{body}", "subject": subject, "body": body, "data": data or {}}
                self.session.post(self.webhook_url, json=payload, timeout=15).raise_for_status()
                delivered.append("webhook")
            except Exception as exc:
                logger.error(f"Webhook notification failed: {exc}")
        if self.email_to and os.getenv("SMTP_HOST"):
            try:
                self._send_email(subject, body)
                delivered.append("email")
            except Exception as exc:
                logger.error(f"E-mail notification failed: {exc}")
        return delivered

    def _send_email(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = os.getenv("SMTP_FROM", "rag-agent@localhost")
        message["To"] = ", ".join(self.email_to)
        message.set_content(body)
        port = int(os.getenv("SMTP_PORT", "587"))
        with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=30) as smtp:
            if os.getenv("SMTP_STARTTLS", "true").lower() == "true":
                smtp.starttls()
            if os.getenv("SMTP_USERNAME"):
                smtp.login(os.environ["SMTP_USERNAME"], os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(message)
