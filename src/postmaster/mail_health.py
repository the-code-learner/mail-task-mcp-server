from __future__ import annotations

import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any

import dns.exception
import dns.resolver
import httpx


def _txt_value(answer: Any) -> str:
    strings = getattr(answer, "strings", None)
    if strings:
        return b"".join(strings).decode("utf-8", "replace")
    text = answer.to_text() if hasattr(answer, "to_text") else str(answer)
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return text.replace('" "', "")


class DNSHealthChecker:
    """Read-only, provider-independent mail DNS diagnostics."""

    def __init__(self, resolver: dns.resolver.Resolver | None = None, timeout: float = 5.0):
        self.resolver = resolver or dns.resolver.Resolver()
        self.timeout = max(0.5, float(timeout))
        self.resolver.timeout = min(self.resolver.timeout, self.timeout)
        self.resolver.lifetime = self.timeout

    def _resolve(self, name: str, record_type: str) -> tuple[list[Any], str | None]:
        try:
            answer = self.resolver.resolve(name, record_type, lifetime=self.timeout, raise_on_no_answer=False)
            return list(answer or []), None
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return [], None
        except dns.exception.DNSException as exc:
            return [], f"{type(exc).__name__}: {exc}"

    def _txt(self, name: str) -> tuple[list[str], str | None]:
        rows, error = self._resolve(name, "TXT")
        return [_txt_value(row) for row in rows], error

    def _spf_lookup_count(self, domain: str, record: str, seen: set[str] | None = None) -> dict[str, Any]:
        seen = set(seen or ())
        domain_lc = domain.casefold().rstrip(".")
        if domain_lc in seen:
            return {"count": 0, "cycle": True, "details": [f"cycle:{domain_lc}"]}
        seen.add(domain_lc)
        count = 0
        details: list[str] = []
        cycle = False
        for token in record.split()[1:]:
            raw = token.lstrip("+-~?")
            if raw.startswith(("a", "mx", "ptr", "exists:")):
                mechanism = raw.split(":", 1)[0].split("/", 1)[0]
                if mechanism in {"a", "mx", "ptr", "exists"}:
                    count += 1
                    details.append(mechanism)
            if raw.startswith("include:"):
                target = raw.split(":", 1)[1].strip()
                if target:
                    count += 1
                    details.append(f"include:{target}")
                    records, _ = self._txt(target)
                    nested = [x for x in records if x.strip().lower().startswith("v=spf1")]
                    if nested:
                        child = self._spf_lookup_count(target, nested[0], seen)
                        count += int(child["count"])
                        cycle = cycle or bool(child["cycle"])
                        details.extend(child["details"])
            if raw.startswith("redirect="):
                target = raw.split("=", 1)[1].strip()
                if target:
                    count += 1
                    details.append(f"redirect:{target}")
                    records, _ = self._txt(target)
                    nested = [x for x in records if x.strip().lower().startswith("v=spf1")]
                    if nested:
                        child = self._spf_lookup_count(target, nested[0], seen)
                        count += int(child["count"])
                        cycle = cycle or bool(child["cycle"])
                        details.extend(child["details"])
        return {"count": count, "cycle": cycle, "details": details}

    @staticmethod
    def _tags(record: str) -> dict[str, str]:
        tags: dict[str, str] = {}
        for chunk in record.split(";"):
            if "=" in chunk:
                key, value = chunk.split("=", 1)
                tags[key.strip().lower()] = value.strip()
        return tags

    def check(self, domain: str, dkim_selector: str | None = None) -> dict[str, Any]:
        domain = (domain or "").strip().lower().rstrip(".")
        if not domain or "." not in domain:
            raise ValueError("A valid sender domain is required")
        mx_rows, mx_error = self._resolve(domain, "MX")
        mx = []
        for row in mx_rows:
            exchange = str(getattr(row, "exchange", "")).rstrip(".")
            preference = int(getattr(row, "preference", 0))
            if exchange:
                mx.append({"preference": preference, "exchange": exchange})
        mx.sort(key=lambda item: (item["preference"], item["exchange"]))
        txt, txt_error = self._txt(domain)
        spf_records = [row for row in txt if row.strip().lower().startswith("v=spf1")]
        spf = {
            "records": spf_records,
            "valid_record_count": len(spf_records),
            "multiple_records": len(spf_records) > 1,
            "syntax_warning": None,
            "lookup_count": None,
            "lookup_limit_exceeded": False,
            "lookup_details": [],
            "error": txt_error,
        }
        if len(spf_records) == 1:
            if not spf_records[0].strip().lower().startswith("v=spf1 "):
                spf["syntax_warning"] = "SPF record has no mechanisms after v=spf1"
            lookup = self._spf_lookup_count(domain, spf_records[0])
            spf["lookup_count"] = lookup["count"]
            spf["lookup_limit_exceeded"] = int(lookup["count"]) > 10
            spf["lookup_details"] = lookup["details"]
            if lookup["cycle"]:
                spf["syntax_warning"] = "SPF include/redirect cycle detected"
        dmarc_txt, dmarc_error = self._txt(f"_dmarc.{domain}")
        dmarc_records = [row for row in dmarc_txt if row.strip().lower().startswith("v=dmarc1")]
        dmarc_tags = self._tags(dmarc_records[0]) if dmarc_records else {}
        dmarc = {
            "record": dmarc_records[0] if dmarc_records else "",
            "multiple_records": len(dmarc_records) > 1,
            "policy": dmarc_tags.get("p"),
            "subdomain_policy": dmarc_tags.get("sp"),
            "dkim_alignment": dmarc_tags.get("adkim", "r") if dmarc_records else None,
            "spf_alignment": dmarc_tags.get("aspf", "r") if dmarc_records else None,
            "alignment_note": "DMARC alignment policy is reported from DNS; actual per-message alignment requires authenticated received-message results.",
            "error": dmarc_error,
        }
        selector = (dkim_selector or "").strip()
        if selector:
            dkim_txt, dkim_error = self._txt(f"{selector}._domainkey.{domain}")
            dkim_records = [row for row in dkim_txt if "v=DKIM1" in row.upper() or "p=" in row]
            dkim = {"selector": selector, "validated": bool(dkim_records), "record": dkim_records[0] if dkim_records else "", "error": dkim_error}
        else:
            dkim = {"selector": None, "validated": False, "record": "", "error": None, "note": "DKIM cannot be validated without a configured or explicitly supplied selector."}
        mta_sts_txt, mta_error = self._txt(f"_mta-sts.{domain}")
        mta_records = [row for row in mta_sts_txt if row.strip().lower().startswith("v=stsv1")]
        mta_sts: dict[str, Any] = {"dns_record": mta_records[0] if mta_records else "", "supported": bool(mta_records), "policy": None, "error": mta_error}
        if mta_records:
            try:
                response = httpx.get(f"https://mta-sts.{domain}/.well-known/mta-sts.txt", timeout=self.timeout, follow_redirects=True)
                if response.status_code == 200:
                    fields: dict[str, list[str]] = {}
                    for line in response.text.splitlines():
                        if ":" in line:
                            key, value = line.split(":", 1)
                            fields.setdefault(key.strip().lower(), []).append(value.strip())
                    mta_sts["policy"] = {"version": (fields.get("version") or [""])[0], "mode": (fields.get("mode") or [""])[0], "mx": fields.get("mx") or [], "max_age": (fields.get("max_age") or [""])[0]}
                else:
                    mta_sts["policy_error"] = f"HTTP {response.status_code}"
            except Exception as exc:
                mta_sts["policy_error"] = f"{type(exc).__name__}: {exc}"
        tls_rpt_txt, tls_rpt_error = self._txt(f"_smtp._tls.{domain}")
        tls_rpt_records = [row for row in tls_rpt_txt if row.strip().lower().startswith("v=tlsrptv1")]
        dane = []
        for item in mx[:5]:
            host = item["exchange"]
            rows, error = self._resolve(f"_25._tcp.{host}", "TLSA")
            dane.append({"mx": host, "supported": bool(rows), "records": [row.to_text() if hasattr(row, "to_text") else str(row) for row in rows], "error": error})
        bimi_txt, bimi_error = self._txt(f"default._bimi.{domain}")
        bimi_records = [row for row in bimi_txt if row.strip().lower().startswith("v=bimi1")]
        return {
            "domain": domain,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "mx": {"records": mx, "error": mx_error},
            "spf": spf,
            "dkim": dkim,
            "dmarc": dmarc,
            "mta_sts": mta_sts,
            "tls_rpt": {"supported": bool(tls_rpt_records), "records": tls_rpt_records, "error": tls_rpt_error},
            "dane_tlsa": dane,
            "bimi": {"supported": bool(bimi_records), "records": bimi_records, "error": bimi_error, "optional": True},
        }


def socket_tls_info(sock: Any, *, hostname: str, implicit_tls: bool, starttls: bool) -> dict[str, Any]:
    if sock is None or not hasattr(sock, "cipher"):
        return {"protected": False, "implicit_tls": implicit_tls, "starttls": starttls, "version": None, "cipher": None, "certificate": None}
    cert = sock.getpeercert() or {}
    not_after = cert.get("notAfter")
    expires_at = None
    expires_in_days = None
    if not_after:
        try:
            seconds = ssl.cert_time_to_seconds(not_after)
            expiry = datetime.fromtimestamp(seconds, tz=timezone.utc)
            expires_at = expiry.isoformat()
            expires_in_days = round((expiry - datetime.now(timezone.utc)).total_seconds() / 86400, 2)
        except Exception:
            pass
    cipher = sock.cipher()
    return {
        "protected": True,
        "implicit_tls": implicit_tls,
        "starttls": starttls,
        "version": sock.version() if hasattr(sock, "version") else None,
        "cipher": cipher[0] if cipher else None,
        "cipher_bits": cipher[2] if cipher and len(cipher) > 2 else None,
        "certificate": {"subject": cert.get("subject"), "issuer": cert.get("issuer"), "serial_number": cert.get("serialNumber"), "not_before": cert.get("notBefore"), "not_after": not_after, "expires_at": expires_at, "expires_in_days": expires_in_days},
        "hostname": hostname,
        "hostname_verification": True,
        "chain_verification": True,
    }


def timed_tcp_connect(host: str, port: int, timeout: float = 10.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            latency = round((time.perf_counter() - started) * 1000.0, 2)
        return {"ok": True, "connection_latency_ms": latency}
    except Exception as exc:
        return {"ok": False, "connection_latency_ms": round((time.perf_counter() - started) * 1000.0, 2), "error": f"{type(exc).__name__}: {exc}"}
