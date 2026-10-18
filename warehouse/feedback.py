"""Lightweight user-feedback capture wired into the hash-chained audit log.

A thumbs up/down plus a free-text correction is appended as a ``feedback``
audit event via :meth:`AuditLog.record`, so feedback inherits the chain's
tamper evidence and is verifiable with :meth:`AuditLog.verify_chain`
alongside every other event.

Corrections are free text from a user, so before they are written, emails,
phone-like numbers and long digit runs (card or account numbers) become
placeholders, and corrections longer than ``MAX_CORRECTION_CHARS`` are
rejected. The audit chain is append-only, so anything written there stays.
"""
import re

ACTION = "feedback"
ACTOR = "user"
RATINGS = ("up", "down")
MAX_CORRECTION_CHARS = 2000
_PII = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"(?:\+?\d[\s().-]?){9,19}\d"), "[NUMBER]"),
]


def redact_correction(text):
    """Replace emails, SSN-shaped numbers and 10+ digit numbers; returns (text, count)."""
    count = 0
    for pattern, placeholder in _PII:
        text, n = pattern.subn(placeholder, text)
        count += n
    return text, count


class FeedbackError(ValueError):
    """Raised when a feedback submission is malformed."""


def _require_nonblank(name, value):
    if not isinstance(value, str) or not value.strip():
        raise FeedbackError(f"{name} must be a non-blank string")
    return value.strip()


def capture_feedback(audit, *, session_id, turn_id, rating, correction=""):
    """Append one feedback event to the audit chain; returns the event.

    ``rating`` must be ``"up"`` or ``"down"``. A ``"down"`` rating requires
    a non-blank ``correction`` describing what was wrong; an ``"up"`` rating
    may omit it. ``session_id`` / ``turn_id`` link the feedback to the
    conversation turn it refers to (supplied by the calling UI/orchestrator).
    """
    session_id = _require_nonblank("session_id", session_id)
    turn_id = _require_nonblank("turn_id", turn_id)
    if rating not in RATINGS:
        raise FeedbackError(
            f"rating must be one of {RATINGS}, got {rating!r}")
    correction = correction.strip() if isinstance(correction, str) else ""
    if rating == "down" and not correction:
        raise FeedbackError("a thumbs-down requires a non-blank correction")
    if len(correction) > MAX_CORRECTION_CHARS:
        raise FeedbackError(f"correction is longer than {MAX_CORRECTION_CHARS} characters")
    correction, redactions = redact_correction(correction)
    details = {
        "session_id": session_id,
        "turn_id": turn_id,
        "rating": rating,
        "correction": correction,
        "pii_redactions": redactions,
    }
    return audit.record(ACTOR, ACTION, details)


def list_feedback(audit):
    """Return all feedback events currently in the chain, in order."""
    return [e for e in audit.events() if e["action"] == ACTION]
