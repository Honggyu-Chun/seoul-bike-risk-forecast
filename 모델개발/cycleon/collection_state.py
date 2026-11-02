"""Helpers for accounting uninterrupted collection windows."""

from .common import instant, stamp


CONTINUITY_SCHEMA_VERSION = 1


def begin_continuous_window(previous, started, interval_seconds, max_capture_duration_seconds):
    """Return a trustworthy trial start and auditable continuity metadata.

    A legacy state has no proof that its prior ``trial_started_at`` survived
    every gap. Reset it once when migrating to this schema. Later restarts may
    preserve the window only when the previous collector was active and its
    last successful capture is within one configured interval plus the
    configured maximum capture duration.
    """
    gap_limit = max(0.0, float(interval_seconds)) + max(0.0, float(max_capture_duration_seconds))
    gap_seconds = None
    capture = previous.get("latest_capture") if isinstance(previous, dict) else None
    completed_at = capture.get("completed_at") if isinstance(capture, dict) else None
    if completed_at:
        try:
            gap_seconds = (instant(started) - instant(completed_at)).total_seconds()
        except (TypeError, ValueError, AttributeError):
            gap_seconds = None

    def reset(reason):
        return stamp(started), {
            "continuity_schema_version": CONTINUITY_SCHEMA_VERSION,
            "continuity_gap_limit_seconds": gap_limit,
            "continuity_previous_capture_gap_seconds": gap_seconds,
            "continuity_reset_reason": reason,
        }

    if not previous:
        return reset("first_run")
    if previous.get("continuity_schema_version") != CONTINUITY_SCHEMA_VERSION:
        return reset("legacy_state_unverified")
    if previous.get("status") != "collecting":
        return reset("previous_run_not_collecting")
    if gap_seconds is None:
        return reset("previous_capture_time_missing_or_invalid")
    if gap_seconds < 0:
        return reset("previous_capture_in_future")
    if gap_seconds > gap_limit:
        return reset("previous_capture_gap_exceeded")

    try:
        previous_start = stamp(instant(previous.get("trial_started_at")))
    except (TypeError, ValueError, AttributeError):
        return reset("previous_window_start_missing_or_invalid")
    return previous_start, {
        "continuity_schema_version": CONTINUITY_SCHEMA_VERSION,
        "continuity_gap_limit_seconds": gap_limit,
        "continuity_previous_capture_gap_seconds": round(gap_seconds, 3),
        "continuity_reset_reason": None,
    }
