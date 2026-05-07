"""Gmail thread ingestor.

Pulls threads matching a list of Gmail labels — focuses on substantive
multi-message threads (configurable via min_thread_length) since single
emails rarely contain the policy / decision context we want to capture.

google-auth-* and google-api-python-client are optional dependencies; they
are imported lazily inside methods so this module is importable in
environments where Gmail isn't connected.

Two modes:
  Demo:  GmailIngestor().build_chunks([])           -> []  (no live fetch)
  Live:  GmailIngestor(credentials_json=..., label_filters=[...]).process(None)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from brain.ingestors import BaseIngestor, Chunk
from brain.text_utils import cap_tokens

logger = logging.getLogger("flowithm.ingest_gmail")


class GmailIngestor(BaseIngestor):
    """All constructor params optional — keeps importability test-friendly."""

    def __init__(
        self,
        credentials_json: str | None = None,
        label_filters: list[str] | None = None,
        since: datetime | None = None,
        min_thread_length: int = 2,
    ) -> None:
        self.credentials_json = credentials_json
        self.label_filters = label_filters or []
        self.since = since
        self.min_thread_length = max(1, int(min_thread_length))
        self._service: Any = None  # built on first fetch

    def build_chunks(self, raw_data: Any = None) -> list[Chunk]:
        if not self.credentials_json or not self.label_filters:
            return []

        chunks: list[Chunk] = []
        service = self._get_service()
        for label in self.label_filters:
            try:
                threads = self._fetch_threads_by_label(service, label)
            except Exception as exc:
                logger.error("Gmail label %r fetch failed: %s", label, exc)
                continue
            for thread in threads:
                chunk = self._thread_to_chunk(thread, label)
                if chunk:
                    chunks.append(chunk)
        return chunks

    # ------------------------------------------------------------------
    # Service / auth (lazy)
    # ------------------------------------------------------------------

    def _get_service(self):
        if self._service is None:
            self._service = self._build_service()
        return self._service

    def _build_service(self):
        # Lazy imports so the module is importable without google-* installed.
        from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        import json
        creds_dict = json.loads(self.credentials_json or "{}")
        creds = Credentials.from_authorized_user_info(creds_dict)
        # cache_discovery=False suppresses the noisy oauth2client warning
        # the discovery layer emits on Python 3.11+.
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def _fetch_threads_by_label(self, service, label: str) -> list[dict]:
        labels_resp = service.users().labels().list(userId="me").execute()
        label_map = {l["name"]: l["id"] for l in labels_resp.get("labels", [])}
        if label not in label_map:
            logger.warning("Gmail label %r not found on account", label)
            return []

        # Gmail's `q` operator accepts label:NAME (handles user labels) and
        # after:UNIX_TIMESTAMP for incremental sync.
        # TODO: add _quote_label() helper if label names contain spaces —
        # Gmail requires quoting (label:"my label") in the q= param.
        q = f"label:{label}"
        if self.since:
            q += f" after:{int(self.since.timestamp())}"

        threads_resp = service.users().threads().list(
            userId="me", q=q, maxResults=50
        ).execute()
        out: list[dict] = []
        for stub in threads_resp.get("threads", []):
            try:
                full = service.users().threads().get(
                    userId="me", id=stub["id"], format="full"
                ).execute()
            except Exception as exc:
                logger.warning("Gmail thread %s get failed: %s", stub.get("id"), exc)
                continue
            out.append(full)
        return out

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def _thread_to_chunk(self, thread: dict, label: str) -> Chunk | None:
        messages = thread.get("messages") or []
        if len(messages) < self.min_thread_length:
            return None

        subject = ""
        lines: list[str] = []
        for msg in messages:
            payload = msg.get("payload") or {}
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
            if not subject:
                subject = headers.get("Subject", "No subject")
            sender = headers.get("From", "Unknown")
            date = headers.get("Date", "")
            body = self._extract_body(payload).strip()
            if body:
                lines.append(f"{sender} [{date}]:\n{body}")

        if not lines:
            return None

        content = f"Subject: {subject}\n\n" + "\n\n---\n\n".join(lines)
        # Threads grow long fast; resolution is at the bottom — preserve head + tail.
        content = cap_tokens(content, self.MAX_CHUNK_TOKENS, strategy="middle_out")

        return Chunk(
            source_type="gmail",
            source_name=f"Email thread: {subject}",
            content=content,
            metadata={
                "thread_id": thread.get("id"),
                "subject": subject,
                "label": label,
                "message_count": len(messages),
            },
        )

    @staticmethod
    def _extract_body(payload: dict) -> str:
        """Return the plain-text body. Recurses into multipart messages.

        Gmail's base64url encoding sometimes omits trailing '=' padding;
        appending '==' is harmless overpadding that the stdlib accepts.
        """
        import base64

        if payload.get("mimeType") == "text/plain":
            data = (payload.get("body") or {}).get("data", "")
            if data:
                try:
                    return base64.urlsafe_b64decode(data + "==").decode(
                        "utf-8", errors="ignore"
                    )
                except Exception:
                    return ""

        for part in payload.get("parts") or []:
            text = GmailIngestor._extract_body(part)
            if text:
                return text
        return ""
