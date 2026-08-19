from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any


PROVIDER_CLASSIFICATIONS = {
    "likely_human",
    "uncertain",
    "likely_email_provider",
    "known_email_proxy",
}


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    return None


def _known_proxy(event: dict[str, Any]) -> tuple[str | None, str | None]:
    ua = str(event.get("user_agent") or "").lower()
    source = str(event.get("client_source") or "").lower()
    browser = str(event.get("browser") or "").lower()
    if "googleimageproxy" in ua or "gmail_image_proxy" in source or "google image proxy" in browser:
        return "google", "known GoogleImageProxy/Gmail image-proxy signature"
    return None, None


def _same_nonempty(left: Any, right: Any) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    return bool(a and b and a == b)


def _changed_nonempty(left: Any, right: Any) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    return bool(a and b and a != b)


def classify_click_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return click events enriched with a reversible, query-time provider likelihood.

    The raw event rows are never mutated or discarded. The heuristic intentionally combines
    multiple weak signals instead of treating timing, geography or User-Agent as proof on its own.
    """
    source_rows = [dict(event) for event in events]

    fingerprint_links: dict[tuple[str, str], set[str]] = defaultdict(set)
    for event in source_rows:
        delivery_id = str(event.get("delivery_id") or "")
        fingerprint = str(event.get("client_fingerprint") or "")
        link_id = str(event.get("link_id") or "")
        if delivery_id and fingerprint and link_id:
            fingerprint_links[(delivery_id, fingerprint)].add(link_id)

    grouped: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, event in enumerate(source_rows):
        grouped[(str(event.get("delivery_id") or ""), str(event.get("link_id") or ""))].append((index, event))

    classifications: dict[int, dict[str, Any]] = {}
    for rows in grouped.values():
        ordered = sorted(
            rows,
            key=lambda item: (
                _parse_time(item[1].get("observed_at")) or datetime.min,
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
                        delta = (current_time - candidate_time).total_seconds()
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
                            reasons.append(f"second request on same delivery/link after {delta_seconds:.2f}s")
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

                delivery_id = str(event.get("delivery_id") or "")
                fingerprint = str(event.get("client_fingerprint") or "")
                consistent_links = len(fingerprint_links.get((delivery_id, fingerprint), set())) if fingerprint else 0
                if consistent_links >= 2:
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
                    if provider_guess is None:
                        provider_guess = None
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
