"""Exercise MIME delivery limits without contacting SMTP or the formula service."""

import unittest
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock, patch

import main
from src.api import emailer


def lecture(sub_id, summary):
    return {
        "course_title": "Course", "sub_id": sub_id, "sub_title": sub_id,
        "date": "2026-11-21", "summary": summary,
    }


class FormulaDeliveryTest(unittest.TestCase):
    def test_batches_count_distinct_formulas_and_preserve_lecture_order(self):
        items = [
            lecture("one", r"$a$ and \(a\)"), lecture("two", "$b$"),
            lecture("three", r"\[c\]"), lecture("four", "$$c$$"),
        ]
        with patch.object(emailer, "_MAX_INLINE_IMAGES", 2):
            batches = emailer.email_batches(items)
        self.assertEqual(batches, [items[:2], items[2:]])

    def test_one_dense_lecture_stays_readable_and_below_the_mime_limit(self):
        images = {}
        summary = "$x_0$ " + " ".join(f"$x_{n}$" for n in range(501))

        def fake_render(url):
            return 20, 12, url.encode()

        with patch.object(emailer, "_prefetch_latex_images"), \
                patch.object(emailer, "_fetch_latex_image", side_effect=fake_render):
            html = emailer._md_to_html(summary, cid_images=images)
        message = emailer.build_html_message(
            "Test", "source@example.invalid", "reader@example.invalid",
            summary, html, images,
        )
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        parts = [part for part in parsed.walk() if part.get_content_type() == "image/png"]
        self.assertEqual(len(parts), 400)
        self.assertEqual(len({part["Content-ID"] for part in parts}), len(parts))
        self.assertEqual(html.count("cid:"), 401)  # repeated x_0 reuses its image
        self.assertIn("<code>x_500</code>", html)
        self.assertEqual(parsed.get_body(preferencelist=("plain",)).get_content(), summary)
        self.assertIsNotNone(parsed["Date"])
        self.assertIsNotNone(parsed["Message-ID"])

    def test_checkpoint_only_the_batch_accepted_by_smtp(self):
        mail, db, reporter = Mock(), Mock(), Mock()
        db.get_unsent_lectures.return_value = []
        mail.send.side_effect = [True, False]
        items = [lecture("one", "$a$"), lecture("two", "$b$"), lecture("three", "$c$")]
        with patch.object(emailer, "_MAX_INLINE_IMAGES", 1):
            main._send_email(Mock(), mail, db, reporter, items)
        self.assertEqual(mail.send.call_count, 2)
        db.mark_emailed_batch.assert_called_once_with(["one"])
        reporter.email_failed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
