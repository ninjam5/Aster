"""Google Calendar tools — read/modify/invite, gated by config.GOOGLE_ACCESS_TIER.

Reading and purely personal (no-guest) event changes only need Partial or
Autonomous access ("modify"). Anything that notifies or affects other people
— creating/updating an event that has guests, or responding to someone
else's invite — is treated as the Calendar equivalent of Gmail's "send" and
routed through the same shared tier policy (tools/google_auth.send_policy):
Partial always asks first; Autonomous acts immediately unless the guardrail
trips (no prior calendar history with an attendee, or the shared daily
autonomous-action cap is hit), falling back to asking instead of silently
dropping the action. Pending actions go through the same shared slot Gmail
uses (tools/google_auth.set_pending_action), so there's never ambiguity
about which pending action a "send it"/"discard" reply resolves.
"""

from datetime import datetime

import config
import tools.google_auth as google_auth


def _event_body(summary=None, start_iso=None, end_iso=None, description=None, guests=None):
    body = {}
    if summary is not None:
        body["summary"] = summary
    if description is not None:
        body["description"] = description
    if start_iso is not None:
        body["start"] = {"dateTime": start_iso}
    if end_iso is not None:
        body["end"] = {"dateTime": end_iso}
    if guests:
        body["attendees"] = [{"email": g} for g in guests]
    return body


def _has_prior_calendar_history(attendee_email: str) -> bool:
    """Guardrail signal — has this person ever appeared on a past event?
    Fails safe: any error counts as 'no prior history'."""
    try:
        service = google_auth.get_calendar_service()
        now = datetime.utcnow().isoformat() + "Z"
        result = service.events().list(
            calendarId="primary", timeMax=now, q=attendee_email, maxResults=1, singleEvents=True
        ).execute()
        return bool(result.get("items"))
    except Exception:
        return False


# ============================================================================
# READ TIER
# ============================================================================

def list_upcoming_events(n: int = 10) -> str:
    if not google_auth.tier_allows("read"):
        return "Blocked: Calendar access is not configured."
    try:
        service = google_auth.get_calendar_service()
        now = datetime.utcnow().isoformat() + "Z"
        result = service.events().list(
            calendarId="primary", timeMin=now, maxResults=max(1, min(n, 25)),
            singleEvents=True, orderBy="startTime",
        ).execute()
        events = result.get("items", [])
        if not events:
            return "No upcoming events."
        lines = []
        for e in events:
            start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "?"))
            guests = [a["email"] for a in e.get("attendees", []) if not a.get("self")]
            guest_note = f" (with {', '.join(guests)})" if guests else ""
            lines.append(f"- [{e['id']}] {start}: {e.get('summary', '(no title)')}{guest_note}")
        return f"{len(events)} upcoming event(s):\n" + "\n".join(lines)
    except RuntimeError as e:
        return f"Google account issue: {e}"
    except Exception as e:
        return f"Failed to fetch events: {e}"


# ============================================================================
# MODIFY / GUEST-AFFECTING — Partial or Autonomous only
# ============================================================================

def create_calendar_event(summary: str, start_iso: str, end_iso: str, guests: list = None, description: str = "") -> str:
    if not google_auth.tier_allows("modify"):
        return "Blocked: creating events requires Partial or Autonomous access (currently Limited)."
    guests = guests or []
    body = _event_body(summary, start_iso, end_iso, description, guests)

    if not guests:
        try:
            service = google_auth.get_calendar_service()
            service.events().insert(calendarId="primary", body=body).execute()
            return f"Created '{summary}' ({start_iso} - {end_iso})."
        except RuntimeError as e:
            return f"Google account issue: {e}"
        except Exception as e:
            return f"Failed to create event: {e}"

    if google_auth.has_pending_action():
        return f"There's already a pending action ({google_auth.pending_action_description()}) — resolve that first."

    guardrail_ok = (
        google_auth.autonomous_sends_today() < config.AUTONOMOUS_SEND_DAILY_CAP
        and all(_has_prior_calendar_history(g) for g in guests)
    )
    policy = google_auth.send_policy(guardrail_ok)
    if policy == "send":
        try:
            service = google_auth.get_calendar_service()
            service.events().insert(calendarId="primary", body=body, sendUpdates="all").execute()
            google_auth.record_autonomous_send()
            return f"Created '{summary}' and invited {', '.join(guests)} autonomously."
        except Exception as e:
            return f"Failed to create event: {e}"

    google_auth.set_pending_action(
        "calendar_create", f"calendar invite '{summary}' to {', '.join(guests)}",
        {"event_body": body, "summary": summary},
    )
    return f"I'd like to create '{summary}' and invite {', '.join(guests)} — reply 'send it' or 'discard'."


def update_calendar_event(event_id: str, summary: str = None, start_iso: str = None, end_iso: str = None, description: str = None) -> str:
    if not google_auth.tier_allows("modify"):
        return "Blocked: updating events requires Partial or Autonomous access (currently Limited)."
    try:
        service = google_auth.get_calendar_service()
        existing = service.events().get(calendarId="primary", eventId=event_id).execute()
    except RuntimeError as e:
        return f"Google account issue: {e}"
    except Exception as e:
        return f"Failed to look up event: {e}"

    guests = [a["email"] for a in existing.get("attendees", []) if not a.get("self")]
    body = _event_body(summary, start_iso, end_iso, description)
    label = existing.get("summary", event_id)

    if not guests:
        try:
            service.events().patch(calendarId="primary", eventId=event_id, body=body).execute()
            return f"Updated '{label}'."
        except Exception as e:
            return f"Failed to update event: {e}"

    if google_auth.has_pending_action():
        return f"There's already a pending action ({google_auth.pending_action_description()}) — resolve that first."

    guardrail_ok = google_auth.autonomous_sends_today() < config.AUTONOMOUS_SEND_DAILY_CAP
    policy = google_auth.send_policy(guardrail_ok)
    if policy == "send":
        try:
            service.events().patch(calendarId="primary", eventId=event_id, body=body, sendUpdates="all").execute()
            google_auth.record_autonomous_send()
            return f"Updated '{label}' and notified attendees autonomously."
        except Exception as e:
            return f"Failed to update event: {e}"

    google_auth.set_pending_action(
        "calendar_update", f"update to '{label}' (notifies {', '.join(guests)})",
        {"event_id": event_id, "event_body": body, "summary": label},
    )
    return f"I'd like to update '{label}', which will notify {', '.join(guests)} — reply 'send it' or 'discard'."


def respond_to_invite(event_id: str, response: str) -> str:
    response = response.strip().lower()
    if response not in ("accepted", "declined", "tentative"):
        return "response must be 'accepted', 'declined', or 'tentative'."
    if not google_auth.tier_allows("modify"):
        return "Blocked: responding to invites requires Partial or Autonomous access (currently Limited)."

    try:
        service = google_auth.get_calendar_service()
        event = service.events().get(calendarId="primary", eventId=event_id).execute()
    except RuntimeError as e:
        return f"Google account issue: {e}"
    except Exception as e:
        return f"Failed to look up event: {e}"

    attendees = event.get("attendees", [])
    for a in attendees:
        if a.get("self"):
            a["responseStatus"] = response
    label = event.get("summary", event_id)

    if google_auth.has_pending_action():
        return f"There's already a pending action ({google_auth.pending_action_description()}) — resolve that first."

    guardrail_ok = google_auth.autonomous_sends_today() < config.AUTONOMOUS_SEND_DAILY_CAP
    policy = google_auth.send_policy(guardrail_ok)
    if policy == "send":
        try:
            service.events().patch(calendarId="primary", eventId=event_id, body={"attendees": attendees}, sendUpdates="all").execute()
            google_auth.record_autonomous_send()
            return f"Responded '{response}' to '{label}' autonomously."
        except Exception as e:
            return f"Failed to respond: {e}"

    google_auth.set_pending_action(
        "calendar_respond", f"{response} response to invite '{label}'",
        {"event_id": event_id, "attendees": attendees, "summary": label, "response": response},
    )
    return f"I'd like to respond '{response}' to '{label}' — reply 'send it' or 'discard'."


def _finish_pending_action(kind: str, payload: dict, approve: bool) -> str:
    """Dispatched from google_auth.resolve_pending_action()."""
    if not approve:
        return "Discarded."
    try:
        service = google_auth.get_calendar_service()
        if kind == "calendar_create":
            service.events().insert(calendarId="primary", body=payload["event_body"], sendUpdates="all").execute()
            return f"Created '{payload['summary']}' and sent invites."
        if kind == "calendar_update":
            service.events().patch(calendarId="primary", eventId=payload["event_id"], body=payload["event_body"], sendUpdates="all").execute()
            return f"Updated '{payload['summary']}' and notified attendees."
        if kind == "calendar_respond":
            service.events().patch(calendarId="primary", eventId=payload["event_id"], body={"attendees": payload["attendees"]}, sendUpdates="all").execute()
            return f"Responded '{payload['response']}' to '{payload['summary']}'."
        return "Unknown calendar action."
    except Exception as e:
        return f"Failed to complete the action: {e}"
