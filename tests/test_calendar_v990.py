from __future__ import annotations

from datetime import UTC, datetime
from email.message import EmailMessage
import unittest

from postmaster.calendar_v990 import build_calendar_request, detect_calendar_parts, parse_ics


class CalendarV990Tests(unittest.TestCase):
    def test_roundtrip_request_and_video_link(self):
        text = build_calendar_request(
            uid="job-123@postmaster",
            summary="Project sync",
            start_utc=datetime(2026, 9, 21, 9, 0, tzinfo=UTC),
            end_utc=datetime(2026, 9, 21, 9, 30, tzinfo=UTC),
            organizer_email="owner@example.com",
            attendees=["guest@example.com"],
            description="Agenda",
            video_url="https://meet.google.com/abc-defg-hij",
        )
        parsed = parse_ics(text)
        self.assertTrue(parsed["is_invitation"])
        event = parsed["events"][0]
        self.assertEqual(event["uid"], "job-123@postmaster")
        self.assertEqual(event["summary"], "Project sync")
        self.assertEqual(event["attendees"][0]["address"], "guest@example.com")
        self.assertEqual(event["video_urls"], ["https://meet.google.com/abc-defg-hij"])

    def test_mime_inspection_is_local_and_detects_inline_calendar(self):
        msg = EmailMessage()
        msg["Subject"] = "Invite"
        msg["From"] = "owner@example.com"
        msg["To"] = "guest@example.com"
        msg.set_content("Calendar invitation")
        ics = build_calendar_request(
            uid="u1@example.com",
            summary="Demo",
            start_utc=datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
            end_utc=datetime(2026, 9, 21, 11, 0, tzinfo=UTC),
            organizer_email="owner@example.com",
            attendees=["guest@example.com"],
        )
        msg.add_alternative(ics, subtype="calendar", params={"method": "REQUEST"})
        result = detect_calendar_parts(msg.as_bytes())
        self.assertTrue(result["calendar_candidate"])
        self.assertTrue(result["is_invitation"])
        self.assertFalse(result["network_access"])
        self.assertFalse(result["external_actions"])

    def test_cancel_is_invitation_semantics_without_external_action(self):
        ics = "BEGIN:VCALENDAR\r\nMETHOD:CANCEL\r\nBEGIN:VEVENT\r\nUID:x\r\nSUMMARY:Cancelled\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        parsed = parse_ics(ics)
        self.assertTrue(parsed["is_invitation"])
        self.assertFalse(parsed["external_actions"])


if __name__ == "__main__":
    unittest.main()
