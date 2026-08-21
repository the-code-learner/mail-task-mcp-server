from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any


PROVIDER_CLASSIFICATIONS = {
    "likely_human",
    "uncertain",
    "likely_email_provider",
    "known_email_proxy",
}

_MULTI_LINK_BURST_SECONDS = 2.0


def _parse_time(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _provider_hint(event: dict[str, Any]) -> str | None:
    ua = str(event.get("user_agent") or "").lower()
    source = str(event.get("client_source") or "").lower()
    browser = str(event.get("browser") or "").lower()
    combined = " ".join((ua, source, browser))
    if "googleimageproxy" in combined or "gmail_image_proxy" in combined:
        return "google"
    if any(token in combined for token in ("microsoft", "outlook", "office 365", "office/")):
        return "microsoft"
    if "yahoo" in combined:
        return "yahoo"

    recipient = str(event.get("recipient") or "").strip().lower()
    domain = recipient.rsplit("@", 1)[-1] if "@" in recipient else ""
    if domain in {"gmail.com", "googlemail.com"}:
        return "google"
    if domain in {"outlook.com", "hotmail.com", "live.com", "msn.com"}:
        return "microsoft"
    if domain == "yahoo.com" or domain.startswith("yahoo."):
        return "yahoo"
    return None


def _known_proxy(event: dict[str, Any]) -> tuple[str | None, str | None]:
    ua = str(event.get("user_agent") or "").lower()
    source = str(event.get("client_source") or "").lower()
    browser = str(event.get("browser") or "").lower()
    if "googleimageproxy" in ua or "gmail_image_proxy" in source or "google image proxy" in browser:
        return "google", "known GoogleImageProxy/Gmail image-proxy signature"
    return None, None


def _changed_nonempty(left: Any, right: Any) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    return bool(a and b and a != b)


def _multi_link_burst_points(delta_seconds: float) -> int:
    """Score same-fingerprint fetches of distinct links that are too fast for manual clicks."""
    if delta_seconds <= 0.100:
        return 95
    if delta_seconds <= 0.250:
        return 90
    if delta_seconds <= 1.000:
        return 80
    if delta_seconds <= _MULTI_LINK_BURST_SECONDS:
        return 70
    return 0


def _multi_link_burst_evidence(
    events: list[dict[str, Any]],
) -> dict[int, tuple[float, str]]:
    """Return symmetric cross-link burst evidence indexed by source row.

    A burst requires the same delivery and fingerprint but a different logical link.
    Evidence is query-time only and does not mutate stored rows or the unique-click key.
    """
    grouped: dict[tuple[str, str], list[tuple[int, dict[str, Any], float]]] = defaultdict(list)
    for index, event in enumerate(events):
        delivery_id = str(event.get("delivery_id") or "")
        fingerprint = str(event.get("client_fingerprint") or "")
        link_id = str(event.get("link_id") or "")
        observed = _parse_time(event.get("observed_at"))
        if delivery_id and fingerprint and link_id and observed is not None:
            grouped[(delivery_id, fingerprint)].append((index, event, observed))

    evidence: dict[int, tuple[float, str]] = {}
    for rows in grouped.values():
        ordered = sorted(rows, key=lambda item: (item[2], int(item[1].get("id") or 0)))
        for pos, (index, event, observed) in enumerate(ordered):
            link_id = str(event.get("link_id") or "")
            best: tuple[float, str] | None = None
            left = pos - 1
            while left >= 0:
                _other_index, other, other_time = ordered[left]
                delta = observed - other_time
                if delta > _MULTI_LINK_BURST_SECONDS:
                    break
                other_link = str(other.get("link_id") or "")
                if other_link and other_link != link_id:
                    best = (delta, other_link)
                    break
                left -= 1

            right = pos + 1
            while right < len(ordered):
                _other_index, other, other_time = ordered[right]
                delta = other_time - observed
                if delta > _MULTI_LINK_BURST_SECONDS:
                    break
                other_link = str(other.get("link_id") or "")
                if other_link and other_link != link_id:
                    if best is None or delta < best[0]:
                        best = (delta, other_link)
                    break
                right += 1

            if best is not None:
                evidence[index] = best
    return evidence


def classify_click_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return click events enriched with a reversible, query-time provider likelihood.

    The raw event rows are never mutated or discarded. The heuristic intentionally combines
    multiple weak signals instead of treating timing, geography or User-Agent as proof on its own.
    Cross-link same-fingerprint bursts within two seconds are treated as strong scanner/provider
    evidence because distinct manual clicks cannot plausibly occur milliseconds apart.
    """
    source_rows = [dict(event) for event in events]

    fingerprint_links: dict[tuple[str, str], set[str]] = defaultdict(set)
    for event in source_rows:
        delivery_id = str(event.get("delivery_id") or "")
        fingerprint = str(event.get("client_fingerprint") or "")
        link_id = str(event.get("link_id") or "")
        if delivery_id and fingerprint and link_id:
            fingerprint_links[(delivery_id, fingerprint)].add(link_id)

    burst_evidence = _multi_link_burst_evidence(source_rows)

    grouped: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, event in enumerate(source_rows):
        grouped[(str(event.get("delivery_id") or ""), str(event.get("link_id") or ""))].append((index, event))

    classifications: dict[int, dict[str, Any]] = {}
    for rows in grouped.values():
        ordered = sorted(
            rows,
            key=lambda item: (
                _parse_time(item[1].get("observed_at"))
                if _parse_time(item[1].get("observed_at")) is not None
                else float("-inf"),
                int(item[1].get("id") or 0),
            ),
        )
        prior: list[dict[str, Any]] = []
        for index, event in ordered:
            score = 5
            reasons: list[str] = []
            provider_guess = _provider_hint(event)
            known_provider, known_reason = _known_proxy(event)

            if known_provider:
                score = 100
                provider_guess = known_provider
                reasons.append(str(known_reason))
                classification = "known_email_proxy"
            else:
                current_time = _parse_time(event.get("observed_at"))
                nearest: dict[str, Any] | None = None
                delta_seconds: float | None = None
                if current_time is not None:
                    for candidate in reversed(prior):
                        candidate_time = _parse_time(candidate.get("observed_at"))
                        if candidate_time is None:
                            continue
                        delta = current_time - candidate_time
                        if delta < 0:
                            continue
                        if delta <= 15:
                            nearest = candidate
                            delta_seconds = delta
                        break

                if nearest is not None and delta_seconds is not None:
                    if _changed_nonempty(event.get("client_fingerprint"), nearest.get("client_fingerprint")):
                        if delta_seconds <= 5:
                            score += 55
                        else:
                            score += 35
                        reasons.append(f"second request on same delivery/link after {delta_seconds:.2f}s")
                        reasons.append("fingerprint changed")
                    if _changed_nonempty(event.get("country_code"), nearest.get("country_code")):
                        score += 15
                        reasons.append(
                            f"country changed {str(nearest.get('country_code') or '')} → {str(event.get('country_code') or '')}"
                        )
                    if _changed_nonempty(event.get("browser"), nearest.get("browser")):
                        score += 10
                        reasons.append(
                            f"browser changed {str(nearest.get('browser') or '')} → {str(event.get('browser') or '')}"
                        )
                    if _changed_nonempty(event.get("user_agent"), nearest.get("user_agent")):
                        score += 10
                        reasons.append("User-Agent changed")

                burst = burst_evidence.get(index)
                if burst is not None:
                    burst_delta, other_link = burst
                    score += _multi_link_burst_points(burst_delta)
                    reasons.append(
                        "multi-link burst: same fingerprint fetched distinct links "
                        f"{burst_delta:.3f}s apart (other link {other_link})"
                    )

                delivery_id = str(event.get("delivery_id") or "")
                fingerprint = str(event.get("client_fingerprint") or "")
                consistent_links = len(fingerprint_links.get((delivery_id, fingerprint), set())) if fingerprint else 0
                if consistent_links >= 2 and burst is None:
                    score -= 25
                    reasons.append(f"fingerprint observed consistently across {consistent_links} links in same delivery")

                source = str(event.get("client_source") or "").lower()
                if "proxy" in source and source != "direct_or_unknown":
                    score += 25
                    reasons.append(f"client source indicates proxy: {event.get('client_source')}")

                score = max(0, min(100, int(score)))
                if score >= 70:
                    classification = "likely_email_provider"
                    provider_guess = provider_guess or "other"
                elif score >= 35:
                    classification = "uncertain"
                else:
                    classification = "likely_human"
                    provider_guess = None

            classifications[index] = {
                "provider_likelihood": int(score),
                "provider_classification": classification,
                "provider_guess": provider_guess,
                "classification_reasons": reasons,
            }
            prior.append(event)

    return [{**event, **classifications[index]} for index, event in enumerate(source_rows)]


def summarize_click_classification(events: list[dict[str, Any]]) -> dict[str, Any]:
    classified = classify_click_events(events)
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for event in classified:
        key = (
            str(event.get("delivery_id") or ""),
            str(event.get("link_id") or ""),
            str(event.get("client_fingerprint") or ""),
        )
        previous = unique.get(key)
        if previous is None or int(event.get("provider_likelihood") or 0) > int(previous.get("provider_likelihood") or 0):
            unique[key] = event

    unique_rows = list(unique.values())
    classes = Counter(str(row.get("provider_classification") or "") for row in unique_rows)
    likely_provider_rows = [
        row for row in unique_rows
        if row.get("provider_classification") in {"likely_email_provider", "known_email_proxy"}
    ]
    provider_suspects = Counter(
        str(row.get("provider_guess") or "other") for row in likely_provider_rows
    )
    total_unique = len(unique_rows)
    likely_provider_unique = len(likely_provider_rows)
    provider_scores = [int(row.get("provider_likelihood") or 0) for row in likely_provider_rows]
    if not provider_scores:
        confidence = "low"
    elif min(provider_scores) >= 85:
        confidence = "high"
    else:
        confidence = "medium"

    return {
        "classification_model": "heuristic-v1-query-time",
        "observed_click_events": len(classified),
        "unique_clicks_classified": total_unique,
        "likely_human_unique_clicks": int(classes.get("likely_human", 0)),
        "uncertain_unique_clicks": int(classes.get("uncertain", 0)),
        "likely_provider_unique_clicks": likely_provider_unique,
        "known_email_proxy_unique_clicks": int(classes.get("known_email_proxy", 0)),
        "likely_human_or_unclassified_unique_clicks": max(0, total_unique - likely_provider_unique),
        "potential_provider_share": {
            "numerator": likely_provider_unique,
            "denominator": total_unique,
            "percent": round((likely_provider_unique / total_unique) * 100.0, 1) if total_unique else 0.0,
        },
        "provider_suspects": dict(sorted(provider_suspects.items())),
        "confidence": confidence,
        "note": (
            "Qualitative estimate only. Raw events and the stable unique-click definition are unchanged; "
            "classification is recalculated from stored evidence on every query."
        ),
    }
