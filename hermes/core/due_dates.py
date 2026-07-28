"""One rule for when a case or a task is due.

A due date is a DAY. Nobody at the agency means "by 8:34:14pm"; they mean "by the
end of Thursday". But ``due_at`` is a timestamptz, and every write path was
putting a different kind of value in it:

  * ``(utcnow() + timedelta(days=n)).isoformat()`` — a naive timestamp carrying
    whatever wall-clock second the case happened to be created at
  * a bare ``YYYY-MM-DD`` typed into the portal
  * midnight UTC, which is 8pm the PREVIOUS DAY in Eastern time — so a case
    opened "due tomorrow" displayed as due today, which is how the whole live
    set of cases ended up reading a day early

So: every due date lands on the same instant of its day — 5pm in the agency's own
timezone, the end of the business day. That is not an arbitrary choice. It is far
enough from midnight in both directions that the date reads the same in every
timezone from Hawaii to Moscow, which a midnight timestamp does not: whoever
looks at the record, "due Aug 15" says Aug 15.

Midnight is treated as a date that lost its time rather than as a real due time,
because that is always what it is here — no one schedules work for 00:00:00.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# The agency's own clock. Both users are in Georgia; anything derived from a day
# ("due in 3 days") has to be reckoned there, not in the container's UTC.
AGENCY_TIMEZONE = os.environ.get("HERMES_AGENCY_TIMEZONE", "America/New_York")
AGENCY_TZ = ZoneInfo(AGENCY_TIMEZONE)

# End of the business day, agency time.
END_OF_BUSINESS_HOUR = 17

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def agency_today(now: datetime | None = None) -> date:
    """Today, as the agency reckons it."""
    moment = now.astimezone(AGENCY_TZ) if now else datetime.now(AGENCY_TZ)
    return moment.date()


def end_of_business(day: date) -> datetime:
    """*day* at the close of business, agency time."""
    return datetime(day.year, day.month, day.day, END_OF_BUSINESS_HOUR, tzinfo=AGENCY_TZ)


def due_in_days(days: int, *, now: datetime | None = None) -> str:
    """A due date *days* from today — the answer to a template's ``due_days``.

    Counted in whole agency days from today's date, so "due in 1 day" is tomorrow
    regardless of what time of day the case was opened. The old
    ``utcnow() + timedelta(days=n)`` moved with the clock and, opened after 8pm
    ET, landed on the wrong date outright.
    """
    return end_of_business(agency_today(now) + timedelta(days=days)).isoformat()


def normalize_due(value: object) -> str | None:
    """Any due date the API is handed → one ISO timestamp, agency close of business.

    Accepts a bare date, a naive timestamp, an aware timestamp, or a ``date``/
    ``datetime`` object. Blank and ``None`` mean "no due date" and come back as
    ``None`` — a case is allowed not to have one.

    A real time of day is kept (a naive one is read as agency time). Only midnight
    is overridden, because in this system midnight is never a chosen time — it is
    what a date turns into on its way through a timestamp column.

    Raises ``ValueError`` on anything unparseable, so a bad value is refused at the
    edge rather than stored and puzzled over later.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        return end_of_business(value).isoformat()
    else:
        text = str(value).strip()
        if not text:
            return None
        if _DATE_ONLY.match(text):
            return end_of_business(date.fromisoformat(text)).isoformat()
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"not a date or timestamp: {value!r}") from exc

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=AGENCY_TZ)
    # Midnight — in whatever zone it arrived — is a date, not a time someone picked.
    # Checked BEFORE converting: midnight UTC has to snap to its own day, not to the
    # 8pm-the-day-before that the same instant is in Eastern time.
    if (moment.hour, moment.minute, moment.second, moment.microsecond) == (0, 0, 0, 0):
        return end_of_business(moment.date()).isoformat()
    # Everything comes back in agency time. Same instant either way — timestamptz
    # does not care — but one representation means a due date can be read off an
    # API response without doing timezone arithmetic in your head.
    return moment.astimezone(AGENCY_TZ).isoformat()
