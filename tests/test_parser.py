"""Tests for the platform-agnostic email parser.

Every case is written from generic English phrasing, so nothing here encodes a
sender domain. If a test needs a platform name it is only to prove that a
platform is *not* mistaken for an employer.
"""

import unittest
from datetime import date

from services.parser import (
    STATUS_APPLIED,
    STATUS_REJECTED,
    STATUS_SELECTED,
    ParsedEmail,
    extract_company,
    extract_date,
    extract_role,
    infer_status,
    is_meaningful,
    parse_email,
    sender_display_name,
)


class SenderNameTest(unittest.TestCase):
    def test_strips_mailbox_role_words(self) -> None:
        cases = {
            "Acme Careers <jobs@acme.com>": "Acme",
            '"Globex Recruiting" <talent@globex.com>': "Globex",
            "Umbrella Labs Talent <talent@hire.lever.co>": "Umbrella Labs",
            "Widgets Inc via Greenhouse <noreply@greenhouse.io>": "Widgets Inc",
        }
        for header, expected in cases.items():
            with self.subTest(header=header):
                self.assertEqual(sender_display_name(header), expected)

    def test_a_platform_is_never_an_employer(self) -> None:
        for header in (
            "LinkedIn <notifications-noreply@linkedin.com>",
            "Indeed <noreply@indeed.com>",
            "no-reply@hire.lever.co",
            "Greenhouse <noreply@greenhouse.io>",
        ):
            with self.subTest(header=header):
                self.assertIsNone(sender_display_name(header))

    def test_a_named_employer_survives_a_platform_tag(self) -> None:
        self.assertEqual(
            sender_display_name("Acme Careers via Lever <noreply@hire.lever.co>"), "Acme"
        )

    def test_generic_labels_yield_nothing(self) -> None:
        for header in ("", "   ", "Talent Acquisition <careers@widgets.example>"):
            with self.subTest(header=header):
                self.assertIsNone(sender_display_name(header))


class CompanyExtractionTest(unittest.TestCase):
    def test_reads_the_subject_line(self) -> None:
        cases = {
            "Application received at Acme Corp for Software Engineer": "Acme Corp",
            "Your application to Globex was submitted": "Globex",
            "Thanks for applying to Initech!": "Initech",
            "Your application to Umbrella Labs": "Umbrella Labs",
        }
        for subject, expected in cases.items():
            with self.subTest(subject=subject):
                self.assertEqual(extract_company(subject=subject), expected)

    def test_reads_the_body(self) -> None:
        cases = {
            "We have received your application at Widgets Inc.": "Widgets Inc",
            "Your application for Product Manager has been received.": None,
            "Company: Initech\nRole: QA Engineer": "Initech",
        }
        for body, expected in cases.items():
            with self.subTest(body=body):
                self.assertEqual(extract_company(body=body), expected)

    def test_falls_back_to_the_sender_when_the_body_names_nobody(self) -> None:
        company = extract_company(
            subject="Your application",
            sender="Globex Recruiting <talent@globex.com>",
            body="Thanks for applying. We will be in touch.",
        )

        self.assertEqual(company, "Globex")

    def test_falls_back_to_the_mail_domain_as_a_last_resort(self) -> None:
        self.assertEqual(
            extract_company(sender="no-reply@widgets.example.org"), "Widgets"
        )

    def test_never_returns_a_domain_or_an_address(self) -> None:
        for subject, sender in (
            ("Your application", "no-reply@ats.example.com"),
            ("Contact us", "someone@mail.example.com"),
        ):
            with self.subTest(subject=subject):
                company = extract_company(subject=subject, sender=sender)
                self.assertFalse(
                    company and ("@" in company or "." in company),
                    f"leaked a domain: {company!r}",
                )


class RoleExtractionTest(unittest.TestCase):
    def test_reads_labelled_and_phrased_roles(self) -> None:
        cases = {
            ("Role: Backend Engineer", ""): "Backend Engineer",
            ("Position of Product Manager", ""): "Product Manager",
            ("", "Your application for the Senior Data Analyst role is under review."): "Senior Data Analyst",
            ("", "Interview for Software Engineer scheduled"): "Software Engineer",
        }
        for (subject, body), expected in cases.items():
            with self.subTest(subject=subject, body=body):
                self.assertEqual(extract_role(subject, "", body), expected)

    def test_does_not_invent_a_role(self) -> None:
        self.assertIsNone(extract_role("Hello", "", "Thanks for your message."))


class DateExtractionTest(unittest.TestCase):
    def test_reads_common_formats(self) -> None:
        cases = {
            "Applied on 12 March 2026": date(2026, 3, 12),
            "Applied on March 12, 2026": date(2026, 3, 12),
            "Applied on 2026-01-09": date(2026, 1, 9),
            "Date: 03/15/2026": date(2026, 3, 15),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_date(text), expected)

    def test_rejects_impossible_or_future_dates(self) -> None:
        for text in ("31/02/2026", "01/01/2099", "Reference 12345678", ""):
            with self.subTest(text=text):
                self.assertIsNone(extract_date(text))


class UnstopSubjectTest(unittest.TestCase):
    """Unstop's acknowledgement subject is the one format no generic rule fits.

    The sender is noreply@emails.unstop.com, which is a platform mailbox and so
    never yields a company on its own. Everything has to come out of the subject.
    """

    SUBJECT = "Your application for Software Engineer at Acme Corp successfully submitted"

    def test_reads_company_and_role_from_the_subject(self) -> None:
        self.assertEqual(extract_company(subject=self.SUBJECT), "Acme Corp")
        self.assertEqual(extract_role(self.SUBJECT), "Software Engineer")

    def test_the_platform_sender_alone_would_yield_nothing(self) -> None:
        sender = "Unstop <noreply@emails.unstop.com>"

        self.assertIsNone(sender_display_name(sender))
        self.assertIsNone(extract_company(subject="Thanks for your application"))

    def test_parses_a_full_unstop_email(self) -> None:
        parsed = parse_email(
            self.SUBJECT,
            "Unstop <noreply@emails.unstop.com>",
            "Hi,\nYour application for Software Engineer at Acme Corp has been "
            "submitted successfully.\nWe will keep you posted.",
        )

        self.assertEqual(parsed.company, "Acme Corp")
        self.assertEqual(parsed.role, "Software Engineer")
        self.assertEqual(parsed.status, STATUS_APPLIED)

    def test_handles_a_multi_word_role(self) -> None:
        subject = (
            "Your application for Senior Software Engineer at Globex Limited "
            "successfully submitted"
        )

        self.assertEqual(extract_company(subject=subject), "Globex Limited")
        self.assertEqual(extract_role(subject), "Senior Software Engineer")

    def test_matches_regardless_of_case(self) -> None:
        subject = (
            "your application for Data Analyst at Initech successfully Submitted"
        )

        self.assertEqual(extract_company(subject=subject), "Initech")
        self.assertEqual(extract_role(subject), "Data Analyst")


class StatusTest(unittest.TestCase):
    def test_an_acknowledgement_is_applied(self) -> None:
        cases = (
            ("Application received at Acme Corp for Software Engineer", ""),
            ("Your application for Software Engineer at Acme Corp successfully submitted", ""),
            ("", "We have received your application at Widgets Inc."),
            ("", "Thanks for applying. We will be in touch."),
        )
        for subject, body in cases:
            with self.subTest(subject=subject, body=body):
                self.assertEqual(infer_status(subject, body), STATUS_APPLIED)

    def test_an_explicit_refusal_wins(self) -> None:
        self.assertEqual(
            infer_status("Update on your application", "Unfortunately you were not selected."),
            STATUS_REJECTED,
        )

    def test_an_offer_wins(self) -> None:
        self.assertEqual(
            infer_status("Update on your application", "Congratulations, we made you an offer."),
            STATUS_SELECTED,
        )

    def test_the_default_survives_junk(self) -> None:
        for args in (("", ""), (None, None)):
            with self.subTest(args=args):
                self.assertEqual(infer_status(*args), STATUS_APPLIED)


class CrossPlatformTest(unittest.TestCase):
    """One realistic email per ATS; none of them should need a special case."""

    def test_every_platform_yields_a_company(self) -> None:
        samples = (
            ("Application received at Acme Corp for Software Engineer",
             "Unstop <no-reply@unstop.com>",
             "We have received your application at Acme Corp for the position of "
             "Software Engineer."),
            ("Your application to Globex was submitted",
             "LinkedIn <notifications-noreply@linkedin.com>",
             "Thanks for applying to Globex. Your application for the Senior Data "
             "Analyst role is under review."),
            ("Acme Careers - Application Confirmation",
             "Acme Careers <no-reply@myworkdayjobs.com>",
             "Your application for Software Engineer has been received.\n"
             "Company: Acme Corporation\nApplied on 12 March 2026."),
            ("Thanks for applying to Initech!",
             "no-reply@greenhouse.io",
             "We have received your application at Initech.\nRole: Backend Engineer\n"
             "Applied on 2026-01-09."),
            ("We have received your application at Widgets Inc.",
             "Talent Acquisition <careers@widgets.example>",
             "Position of Product Manager\nDate: 03/15/2026"),
        )
        for subject, sender, body in samples:
            with self.subTest(subject=subject):
                parsed = parse_email(subject, sender, body)
                self.assertTrue(
                    is_meaningful(parsed.company),
                    f"no company extracted from {subject!r}",
                )

    def test_job_alert_noise_yields_nothing(self) -> None:
        parsed = parse_email(
            "Job alert: 5 new Software Engineer jobs",
            "Indeed <noreply@indeed.com>",
            "Recommended opportunities you may like.",
        )

        self.assertIsNone(parsed.company)
        self.assertIsNone(parsed.role)

    def test_parse_email_never_raises_on_junk(self) -> None:
        for args in (("", "", ""), (None, None, None), ("x" * 5000, "a@b.co", "y" * 5000)):
            with self.subTest(args=args):
                self.assertIsInstance(parse_email(*args), ParsedEmail)


if __name__ == "__main__":
    unittest.main()