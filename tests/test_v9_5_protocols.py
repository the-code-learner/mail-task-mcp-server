from __future__ import annotations

import unittest
from email import policy
from email.parser import BytesParser

from postmaster.mail_health import DNSHealthChecker
from postmaster.mail_protocols import (
    build_dsn_options,
    detect_auto_reply,
    message_diagnostics,
    parse_dsn_message,
    parse_imap_capabilities,
    parse_imap_quota,
    parse_smtp_capabilities,
    xtext,
)


class ProtocolCapabilityTests(unittest.TestCase):
    def test_smtp_capabilities_full(self):
        result = parse_smtp_capabilities({"size":"52428800","dsn":"","smtputf8":"","8bitmime":"","pipelining":"","starttls":"","auth":"PLAIN LOGIN","x-example":"yes"})
        self.assertTrue(result["dsn"])
        self.assertTrue(result["smtputf8"])
        self.assertTrue(result["8bitmime"])
        self.assertTrue(result["pipelining"])
        self.assertTrue(result["starttls"])
        self.assertEqual(result["size"]["limit"], 52428800)
        self.assertEqual(result["auth"]["mechanisms"], ["LOGIN", "PLAIN"])
        self.assertEqual(result["unknown_extensions"], ["X-EXAMPLE"])

    def test_smtp_capabilities_minimal(self):
        result = parse_smtp_capabilities({})
        self.assertFalse(result["dsn"])
        self.assertFalse(result["smtputf8"])
        self.assertEqual(result["extensions"], [])

    def test_imap_capabilities_full(self):
        result = parse_imap_capabilities([b"IMAP4rev1 IDLE MOVE UIDPLUS QUOTA CONDSTORE", b"QRESYNC SPECIAL-USE NAMESPACE SORT THREAD=REFERENCES X-EXT"])
        for key in ("idle","move","uidplus","quota","condstore","qresync","special_use","namespace","sort","thread"):
            self.assertTrue(result[key])
        self.assertEqual(result["unknown_capabilities"], ["IMAP4REV1", "X-EXT"])

    def test_imap_capabilities_minimal(self):
        result = parse_imap_capabilities([b"IMAP4rev1"])
        self.assertFalse(result["idle"])
        self.assertFalse(result["quota"])

    def test_quota_multiple_resources(self):
        result = parse_imap_quota([b'"" (STORAGE 512 1024 MESSAGE 4 100)'])
        self.assertTrue(result["supported"])
        self.assertEqual(result["resources"][0]["percent"], 50.0)
        self.assertEqual(result["resources"][1]["resource"], "MESSAGE")

    def test_quota_unsupported(self):
        self.assertEqual(parse_imap_quota([]), {"supported": False, "resources": []})

    def test_quota_zero_limit(self):
        result = parse_imap_quota([b'"" (STORAGE 0 0)'])
        self.assertIsNone(result["resources"][0]["percent"])
        self.assertTrue(result["resources"][0]["unlimited_or_unknown_limit"])


class DSNTests(unittest.TestCase):
    def test_xtext(self):
        self.assertEqual(xtext("a+b=c"), "a+2Bb+3Dc")

    def test_dsn_options_default_has_no_success(self):
        mail, rcpt = build_dsn_options(envelope_id="dlv_123", recipient="person@example.com")
        self.assertEqual(mail, ["ENVID=dlv_123"])
        self.assertIn("NOTIFY=FAILURE,DELAY", rcpt)
        self.assertFalse(any("SUCCESS" in item for item in rcpt))
        self.assertTrue(any(item.startswith("ORCPT=rfc822;") for item in rcpt))

    def test_dsn_success_must_be_explicit(self):
        _, rcpt = build_dsn_options(envelope_id="dlv_123", recipient="person@example.com", notify_success=True)
        self.assertIn("NOTIFY=FAILURE,DELAY,SUCCESS", rcpt)

    def test_structured_dsn(self):
        raw = (b"From: mailer-daemon@example.net\r\nTo: sender@example.com\r\nSubject: Delivery Status Notification\r\nContent-Type: multipart/report; report-type=delivery-status; boundary=x\r\n\r\n--x\r\nContent-Type: text/plain\r\n\r\nDelivery failed.\r\n--x\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; mx.example.net\r\nOriginal-Envelope-ID: dlv_abc\r\n\r\nFinal-Recipient: rfc822; missing@example.net\r\nAction: failed\r\nStatus: 5.1.1\r\nRemote-MTA: dns; remote.example.net\r\nDiagnostic-Code: smtp; 550 5.1.1 user unknown\r\n\r\n--x--\r\n")
        parsed = parse_dsn_message(raw)
        self.assertTrue(parsed["is_dsn"])
        self.assertTrue(parsed["structured"])
        self.assertEqual(parsed["observed"]["original_envelope_id"], "dlv_abc")
        self.assertEqual(parsed["derived"]["recipient"], "missing@example.net")
        self.assertEqual(parsed["derived"]["classification"], "user_unknown")
        self.assertEqual(parsed["confidence"], "high")

    def test_textual_bounce_fallback(self):
        raw = b"From: daemon@example.net\r\nTo: sender@example.com\r\nSubject: delivery problem\r\n\r\nRemote server said 450 4.2.0 mailbox full; please retry later.\r\n"
        parsed = parse_dsn_message(raw)
        self.assertTrue(parsed["is_dsn"])
        self.assertEqual(parsed["derived"]["classification"], "mailbox_full")
        self.assertEqual(parsed["confidence"], "medium")

    def test_malformed_message_is_not_falsely_certain(self):
        parsed = parse_dsn_message(b"Subject: hello\r\n\r\nordinary message")
        self.assertFalse(parsed["is_dsn"])
        self.assertEqual(parsed["confidence"], "low")


class AutoReplyAndMIMETests(unittest.TestCase):
    def _msg(self, raw: bytes):
        return BytesParser(policy=policy.default).parsebytes(raw)

    def test_auto_submitted(self):
        result = detect_auto_reply(self._msg(b"Subject: away\r\nAuto-Submitted: auto-replied\r\n\r\nmessage"))
        self.assertTrue(result["is_auto_reply"])
        self.assertEqual(result["confidence"], "high")

    def test_precedence_auto_reply(self):
        self.assertTrue(detect_auto_reply(self._msg(b"Subject: notice\r\nPrecedence: auto_reply\r\n\r\nmessage"))["is_auto_reply"])

    def test_human_reply_not_misclassified(self):
        self.assertFalse(detect_auto_reply(self._msg(b"Subject: Re: hello\r\nAuto-Submitted: no\r\n\r\nThanks"))["is_auto_reply"])

    def test_mime_diagnostics(self):
        raw = (b"Message-ID: <m1@example.com>\r\nIn-Reply-To: <m0@example.com>\r\nAuthentication-Results: mx.example.net; spf=pass; dkim=pass; dmarc=pass\r\nReceived: from relay.example.net by mx.example.net\r\nList-Unsubscribe: <https://example.com/unsub>\r\nContent-Type: multipart/mixed; boundary=x\r\n\r\n--x\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nhello\r\n--x\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment; filename=test.bin\r\n\r\nabc\r\n--x--\r\n")
        result = message_diagnostics(raw)
        self.assertEqual(result["message_id"], "<m1@example.com>")
        self.assertEqual(result["authentication_summary"]["spf"], ["pass"])
        self.assertEqual(len(result["received_chain"]), 1)
        self.assertEqual(result["attachments"][0]["filename"], "test.bin")
        self.assertIn("list-unsubscribe", result["list_headers"])


class FakeTXT:
    def __init__(self, value: str): self.strings = [value.encode()]
    def to_text(self): return f'"{self.strings[0].decode()}"'

class FakeMX:
    def __init__(self, preference: int, exchange: str): self.preference, self.exchange = preference, exchange
    def to_text(self): return f"{self.preference} {self.exchange}"

class FakeResolver:
    def __init__(self, mapping): self.mapping, self.timeout, self.lifetime = mapping, 2.0, 2.0
    def resolve(self, name, record_type, **kwargs): return list(self.mapping.get((name, record_type), []))


class DNSHealthTests(unittest.TestCase):
    def test_dns_health_standard_records(self):
        resolver = FakeResolver({
            ("example.com","MX"): [FakeMX(10,"mx.example.com.")],
            ("example.com","TXT"): [FakeTXT("v=spf1 include:_spf.example.net -all")],
            ("_spf.example.net","TXT"): [FakeTXT("v=spf1 a mx -all")],
            ("_dmarc.example.com","TXT"): [FakeTXT("v=DMARC1; p=reject; adkim=s; aspf=r")],
            ("selector._domainkey.example.com","TXT"): [FakeTXT("v=DKIM1; p=abc")],
            ("_smtp._tls.example.com","TXT"): [FakeTXT("v=TLSRPTv1; rua=mailto:tls@example.com")],
            ("default._bimi.example.com","TXT"): [FakeTXT("v=BIMI1; l=https://example.com/logo.svg")],
        })
        result = DNSHealthChecker(resolver=resolver).check("example.com", dkim_selector="selector")
        self.assertEqual(result["mx"]["records"][0]["exchange"], "mx.example.com")
        self.assertEqual(result["spf"]["lookup_count"], 3)
        self.assertFalse(result["spf"]["lookup_limit_exceeded"])
        self.assertEqual(result["dmarc"]["policy"], "reject")
        self.assertEqual(result["dmarc"]["dkim_alignment"], "s")
        self.assertTrue(result["dkim"]["validated"])
        self.assertTrue(result["tls_rpt"]["supported"])
        self.assertTrue(result["bimi"]["supported"])

    def test_multiple_spf_is_flagged(self):
        result = DNSHealthChecker(resolver=FakeResolver({("example.com","TXT"): [FakeTXT("v=spf1 -all"), FakeTXT("v=spf1 include:other.example -all")]})).check("example.com")
        self.assertTrue(result["spf"]["multiple_records"])
        self.assertIsNone(result["spf"]["lookup_count"])

    def test_dkim_selector_is_never_invented(self):
        result = DNSHealthChecker(resolver=FakeResolver({})).check("example.com")
        self.assertIsNone(result["dkim"]["selector"])
        self.assertFalse(result["dkim"]["validated"])
        self.assertIn("without", result["dkim"]["note"])


if __name__ == "__main__":
    unittest.main()
