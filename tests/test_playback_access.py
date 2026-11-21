"""Regression tests for lecture access decisions, using offline responses."""

import unittest
from unittest.mock import Mock, patch

import main
from src.api.icourse import ICourseClient


class PlaybackAccessTest(unittest.TestCase):
    def setUp(self):
        self.vpn = Mock()
        self.client = ICourseClient(self.vpn)
        self.response = self.vpn.get.return_value

    def test_deferred_or_unverified_lecture_never_starts_a_download(self):
        for response in (
            {"code": 7001, "data": {"title": "Pending lecture"}},
            {"code": 403, "data": {"title": "Restricted lecture"}},
            {"data": {"title": "Unknown state"}},
            {"code": 0, "data": {}},
            None,
        ):
            with self.subTest(response=response):
                self.vpn.reset_mock()
                self.response.json.return_value = response
                self.assertIsNone(self.client.get_video_url("course", "lecture"))
                self.vpn.get.assert_called_once()

    def test_failed_status_check_does_not_request_other_resources(self):
        self.response.raise_for_status.side_effect = OSError("Offline")
        with self.assertRaises(RuntimeError):
            self.client.get_sub_detail("course", "lecture")
        self.vpn.get.assert_called_once()
        self.response.json.assert_not_called()

    def test_released_stream_uses_the_published_url(self):
        media = "https://example.invalid/lecture.mp4"
        self.response.json.return_value = {
            "code": 0, "data": {"video_list": {"main": {"preview_url": media}}, "now": 123}
        }
        with patch.object(self.client, "sign_video_url", return_value="ready") as sign:
            self.assertEqual(self.client.get_video_url("course", "lecture"), "ready")
        sign.assert_called_once_with(media, now=123)
        self.vpn.get.assert_called_once()

    def test_future_run_can_process_a_previously_deferred_lecture(self):
        self.response.json.side_effect = [
            {"code": 7001, "data": {"title": "Pending lecture"}},
            {"code": 0, "data": {"title": "Released lecture"}},
        ]
        self.assertIsNone(self.client.get_sub_info("course", "lecture"))
        self.assertEqual(self.client.get_sub_info("course", "lecture")["title"], "Released lecture")


class SubscriptionAccessTest(unittest.TestCase):
    def test_new_and_retry_lectures_are_checked_before_processing(self):
        client, db, reporter = Mock(), Mock(), Mock()
        client.get_course_detail.return_value = {
            "title": "Course", "teacher": "Teacher", "lectures": [
                {"sub_id": "new", "sub_title": "New", "has_playback": True},
                {"sub_id": "ready", "sub_title": "Ready", "has_playback": True},
            ]
        }
        db.get_processed_sub_ids.return_value = set()
        db.get_unprocessed_lectures.return_value = [
            {"sub_id": "old", "sub_title": "Retry", "date": "2026-11-01"}
        ]
        client.get_sub_info.side_effect = lambda course, sub: {"title": "Ready"} if sub == "ready" else None
        with patch.object(main.config, "COURSE_IDS", ["course"]):
            queue = main._enumerate_lectures(client, db, reporter)
        self.assertEqual([lecture["sub_id"] for _, _, lecture in queue], ["ready"])
        self.assertEqual(client.get_sub_info.call_count, 3)
        db.insert_lecture.assert_called_once_with("ready", "course", "Ready", "")

    def test_cached_unsent_summary_requires_current_access(self):
        client, db, reporter, mail = Mock(), Mock(), Mock(), Mock()
        db.get_unsent_lectures.return_value = [{
            "sub_id": "past", "course_id": "course", "course_title": "Course",
            "sub_title": "Past", "date": "2026-11-01", "summary": "Notes",
        }]
        client.get_sub_info.return_value = None
        main._send_email(client, mail, db, reporter, [])
        mail.send.assert_not_called()
        db.mark_emailed_batch.assert_not_called()
        client.get_sub_info.return_value = {"title": "Released"}
        main._send_email(client, mail, db, reporter, [])
        mail.send.assert_called_once()
        db.mark_emailed_batch.assert_called_once_with(["past"])


if __name__ == "__main__":
    unittest.main()
