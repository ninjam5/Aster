"""Gmail tools — read/modify/send, gated by config.GOOGLE_ACCESS_TIER.

Read functions work at every tier. Modify functions (archive/mark-read/label)
require Partial or Autonomous. Replying always drafts first via the Gmail API
itself (so the pending state IS the Gmail draft, not a parallel structure
Aster has to invent) — Partial always waits for a text approval; Autonomous
sends immediately unless the safety guardrail trips (no prior correspondence
with the sender, or the daily send cap is hit), in which case it falls back
to the same approval flow rather than silently dropping the reply.

The pending-approval mechanism lives in tools/google_auth.py (shared with
Calendar, so "send it"/"discard" is never ambiguous about which pending
action it resolves) and mirrors tools/sentry.py's WAITING_FOR_ID idiom:
resolved by the next free-text reply the user sends (checked in main.py's
Telegram catch-all handler) rather than by any new button/callback
infrastructure.
"""

import base64
import re
from email.mime.text import MIMEText

import config
import tools.google_auth as google_auth


# ============================================================================
# READ TIER — available at Limited, Partial, and Autonomous
# ============================================================================

def _extract_plain_text(payload: dict) -> str:
    """Recursively walks a Gmail message payload for the first text/plain
    part, falling back to tag-stripped text/html if no plain part exists."""
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    for part in payload.get("parts", []) or []:
        text = _extract_plain_text(part)
        if text:
            return text
    if payload.get("mimeType") == "text/html" and "data" in payload.get("body", {}):
        html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        return re.sub("<[^<]+?>", "", html)
    return ""


def summarize_unread_emails(n: int = 10) -> str:
    if not google_auth.tier_allows("read"):
        return "Blocked: Gmail access is not configured."
    try:
        service = google_auth.get_gmail_service()
        result = service.users().messages().list(
            userId="me", q="category:primary is:unread", maxResults=max(1, min(n, 25))
        ).execute()
        msg_refs = result.get("messages", [])
        if not msg_refs:
            return "No unread emails in your primary inbox."
        lines = []
        for ref in msg_refs:
            msg = service.users().messages().get(
                userId="me", id=ref["id"], format="metadata", metadataHeaders=["From", "Subject"]
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            lines.append(f"- From {headers.get('From', '?')}: \"{headers.get('Subject', '(no subject)')}\" — {msg.get('snippet', '')}")
        return f"{len(msg_refs)} unread email(s):\n" + "\n".join(lines)
    except RuntimeError as e:
        return f"Google account issue: {e}"
    except Exception as e:
        return f"Failed to fetch unread emails: {e}"


def search_emails(query: str) -> str:
    if not google_auth.tier_allows("read"):
        return "Blocked: Gmail access is not configured."
    try:
        service = google_auth.get_gmail_service()
        result = service.users().messages().list(userId="me", q=query, maxResults=10).execute()
        msg_refs = result.get("messages", [])
        if not msg_refs:
            return f"No emails matched '{query}'."
        lines = []
        for ref in msg_refs:
            msg = service.users().messages().get(
                userId="me", id=ref["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            lines.append(f"- [{ref['id']}] {headers.get('Date', '?')} From {headers.get('From', '?')}: \"{headers.get('Subject', '(no subject)')}\"")
        return f"{len(msg_refs)} result(s) for '{query}':\n" + "\n".join(lines)
    except RuntimeError as e:
        return f"Google account issue: {e}"
    except Exception as e:
        return f"Search failed: {e}"


def read_email(email_id: str) -> str:
    if not google_auth.tier_allows("read"):
        return "Blocked: Gmail access is not configured."
    try:
        service = google_auth.get_gmail_service()
        msg = service.users().messages().get(userId="me", id=email_id, format="full").execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        body = _extract_plain_text(msg.get("payload", {})) or msg.get("snippet", "")
        return (f"From: {headers.get('From', '?')}\nSubject: {headers.get('Subject', '(no subject)')}\n"
                f"Date: {headers.get('Date', '?')}\n\n{body[:3000]}")
    except RuntimeError as e:
        return f"Google account issue: {e}"
    except Exception as e:
        return f"Failed to read email: {e}"


def get_unread_primary_count() -> int:
    """Non-raising unread count for the Awareness daemon's email check-in."""
    if not google_auth.tier_allows("read") or not google_auth.is_connected():
        return 0
    try:
        service = google_auth.get_gmail_service()
        result = service.users().messages().list(userId="me", q="category:primary is:unread", maxResults=50).execute()
        return len(result.get("messages", []))
    except Exception:
        return 0


# ============================================================================
# MODIFY TIER — Partial or Autonomous only
# ============================================================================

def archive_email(email_id: str) -> str:
    if not google_auth.tier_allows("modify"):
        return "Blocked: archiving requires Partial or Autonomous access (currently Limited)."
    try:
        service = google_auth.get_gmail_service()
        service.users().messages().modify(userId="me", id=email_id, body={"removeLabelIds": ["INBOX"]}).execute()
        return "Archived."
    except RuntimeError as e:
        return f"Google account issue: {e}"
    except Exception as e:
        return f"Failed to archive: {e}"


def mark_email_read(email_id: str) -> str:
    if not google_auth.tier_allows("modify"):
        return "Blocked: marking mail read requires Partial or Autonomous access (currently Limited)."
    try:
        service = google_auth.get_gmail_service()
        service.users().messages().modify(userId="me", id=email_id, body={"removeLabelIds": ["UNREAD"]}).execute()
        return "Marked as read."
    except RuntimeError as e:
        return f"Google account issue: {e}"
    except Exception as e:
        return f"Failed to mark as read: {e}"


def label_email(email_id: str, label_name: str) -> str:
    if not google_auth.tier_allows("modify"):
        return "Blocked: labeling requires Partial or Autonomous access (currently Limited)."
    try:
        service = google_auth.get_gmail_service()
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
        match = next((l for l in labels if l["name"].lower() == label_name.lower()), None)
        if not match:
            match = service.users().labels().create(
                userId="me", body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
            ).execute()
        service.users().messages().modify(userId="me", id=email_id, body={"addLabelIds": [match["id"]]}).execute()
        return f"Labeled '{label_name}'."
    except RuntimeError as e:
        return f"Google account issue: {e}"
    except Exception as e:
        return f"Failed to label: {e}"


# ============================================================================
# SEND PATH — draft-first, tier-policy-gated
# ============================================================================

def _build_raw_reply(to_header: str, subject: str, body: str, in_reply_to: str) -> str:
    msg = MIMEText(body)
    msg["to"] = to_header
    msg["subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _has_prior_correspondence(sender_email: str) -> bool:
    """Guardrail signal — checks Gmail's own sent history for mail already
    sent to this address, rather than Aster's own memory (which may predate
    this integration and wouldn't have tracked email correspondence)."""
    try:
        service = google_auth.get_gmail_service()
        result = service.users().messages().list(userId="me", q=f"from:me to:{sender_email}", maxResults=1).execute()
        return bool(result.get("messages"))
    except Exception:
        return False  # fail safe — unknown counts as "no prior correspondence"


def _log_sent_email(recipient: str, subject: str, autonomous: bool) -> None:
    try:
        from core.memory import memorize_fact
        kind = "autonomously (no draft shown)" if autonomous else "after a shown draft was approved"
        memorize_fact(f"Aster sent an email reply to {recipient} {kind} — subject: \"{subject}\"")
    except Exception:
        pass


def reply_to_email(email_id: str, body: str) -> str:
    if not google_auth.tier_allows("send"):
        return "Blocked: replying requires Partial or Autonomous access (currently Limited). Change it in the dashboard."

    if google_auth.has_pending_action():
        return f"There's already a pending action ({google_auth.pending_action_description()}) — resolve that first."

    try:
        service = google_auth.get_gmail_service()
        original = service.users().messages().get(
            userId="me", id=email_id, format="metadata", metadataHeaders=["From", "Subject", "Message-ID"]
        ).execute()
        headers = {h["name"]: h["value"] for h in original.get("payload", {}).get("headers", [])}
        sender = headers.get("From", "")
        sender_email = sender.split("<")[-1].rstrip(">").strip() if "<" in sender else sender.strip()
        subject = headers.get("Subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        thread_id = original.get("threadId")

        raw = _build_raw_reply(sender, subject, body, headers.get("Message-ID", ""))
        draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw, "threadId": thread_id}}).execute()
        draft_id = draft["id"]
        payload = {"draft_id": draft_id, "recipient": sender_email, "subject": subject}
        description = f"email reply to {sender_email} (\"{subject}\")"

        if config.GOOGLE_ACCESS_TIER == "autonomous":
            cap_hit = google_auth.autonomous_sends_today() >= config.AUTONOMOUS_SEND_DAILY_CAP
            no_history = not _has_prior_correspondence(sender_email)
            if not cap_hit and not no_history:
                service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
                google_auth.record_autonomous_send()
                _log_sent_email(sender_email, subject, autonomous=True)
                return f"Replied to {sender_email} autonomously."
            reason = "the daily autonomous-send cap has been reached" if cap_hit else "there's no prior correspondence with this sender on record"
            google_auth.set_pending_action("gmail_reply", description, payload)
            return (f"I drafted a reply to {sender_email} (\"{subject}\") but held it back — {reason}, "
                    f"so this needs your OK. Reply 'send it' or 'discard'.")

        google_auth.set_pending_action("gmail_reply", description, payload)
        return f"I drafted a reply to {sender_email} (\"{subject}\") — reply 'send it' or 'discard'."
    except RuntimeError as e:
        return f"Google account issue: {e}"
    except Exception as e:
        return f"Failed to draft reply: {e}"


def _finish_pending_reply(payload: dict, approve: bool) -> str:
    """Dispatched from google_auth.resolve_pending_action() once the user's
    free-text reply has been classified as approve/deny."""
    try:
        service = google_auth.get_gmail_service()
        if approve:
            service.users().drafts().send(userId="me", body={"id": payload["draft_id"]}).execute()
            _log_sent_email(payload["recipient"], payload["subject"], autonomous=False)
            return f"Sent the reply to {payload['recipient']}."
        service.users().drafts().delete(userId="me", id=payload["draft_id"]).execute()
        return "Discarded the draft."
    except Exception as e:
        return f"Failed to {'send' if approve else 'discard'}: {e}"
