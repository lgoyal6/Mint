from collections.abc import Mapping
from typing import Any

from mint.backend.models import EnrichmentInput
from mint.backend.utils.domain import is_personal_email_domain


KNOWN_ALIASES: dict[str, set[str]] = {
    "email": {"email", "email address", "e-mail", "work email"},
    "first_name": {"first name", "firstname", "given name"},
    "last_name": {"last name", "lastname", "surname", "family name"},
    "company_name": {"company", "company name", "organization", "org", "account name"},
    "website_url": {"website", "website url", "company website", "organization website"},
    "title": {"title", "job title", "role"},
    "linkedin_url": {"linkedin", "linkedin url", "linkedin profile"},
    "what_they_are_building": {
        "what are you building",
        "what are you building?",
        "what are you working on",
        "what are you working on?",
        "project",
        "project description",
    },
    "source": {"source", "lead source"},
    "event_id": {"event id", "luma event id"},
}

MODEL_FIELDS = {
    "email",
    "first_name",
    "last_name",
    "company_name",
    "website_url",
    "title",
    "linkedin_url",
    "what_they_are_building",
    "source",
    "event_id",
}

EVENT_STATUS_COLUMNS = {"status", "approval status", "rsvp status"}
EVENT_REGISTRATION_STATUSES = {
    "going",
    "approved",
    "interested",
    "maybe",
    "pending",
    "declined",
    "cancelled",
    "canceled",
    "waitlisted",
    "waitlist",
}
GOING_STATUSES = {"going", "approved"}

LUMA_EXPORT_METADATA_COLUMNS = {
    "guest id",
    "name",
    "status",
    "rsvp date",
    "phone number",
    "created at",
    "approval status",
    "checked in at",
    "utm source",
    "qr code url",
    "amount",
    "amount tax",
    "amount discount",
    "currency",
    "coupon code",
    "eth address",
    "solana address",
    "ticket type id",
    "ticket name",
    "survey response rating",
    "survey response feedback",
}


def normalize_luma_guests(
    guests: list[dict],
    event_id: str,
    *,
    include_all_statuses: bool = False,
) -> list[EnrichmentInput]:
    leads: list[EnrichmentInput] = []
    seen: set[str] = set()
    for raw_guest in guests:
        guest = _unwrap_luma_guest(raw_guest)
        if not include_all_statuses and _status(guest) not in {"going", "approved"}:
            continue
        email = _text(guest.get("email") or guest.get("guest_email"))
        if not email:
            continue
        normalized_email = email.lower()
        if normalized_email in seen:
            continue
        seen.add(normalized_email)

        first_name = _text(guest.get("first_name") or guest.get("user_first_name"))
        last_name = _text(guest.get("last_name") or guest.get("user_last_name"))
        if not first_name:
            first_name, last_name = _split_name(_text(guest.get("name") or guest.get("user_name")))

        mapped, survey_responses = _extract_custom_answers(guest)
        leads.append(
            EnrichmentInput(
                email=email,
                first_name=first_name,
                last_name=last_name,
                company_name=mapped.get("company_name") or _text(guest.get("company_name")),
                title=mapped.get("title") or _text(guest.get("title") or guest.get("user_title")),
                linkedin_url=_normalize_linkedin_url(
                    mapped.get("linkedin_url") or _text(guest.get("linkedin_url") or guest.get("linkedin"))
                ),
                what_they_are_building=mapped.get("what_they_are_building"),
                source="luma",
                event_id=event_id,
                survey_responses=survey_responses or None,
            )
        )
    return leads


def normalize_csv_rows(
    rows: list[dict[str, Any]],
    column_mapping: Mapping[str, str] | None,
) -> list[EnrichmentInput]:
    if not rows:
        return []
    mapping = _normalize_mapping(column_mapping) if column_mapping else _detect_mapping(_all_columns(rows))
    name_column = _name_column(rows)
    leads: list[EnrichmentInput] = []
    seen: set[str] = set()
    for row in rows:
        values: dict[str, Any] = {}
        used_columns: set[str] = set()
        for target, source in mapping.items():
            if target in MODEL_FIELDS and source in row:
                values[target] = row.get(source)
                used_columns.add(source)

        if name_column and not values.get("first_name") and not values.get("last_name"):
            values["first_name"], values["last_name"] = _split_name(_text(row.get(name_column)))
            used_columns.add(name_column)

        survey_responses: dict[str, Any] = {}
        for column, value in row.items():
            if column in used_columns or value in (None, ""):
                continue
            question_kind = _question_kind(column)
            if question_kind == "company_name" and not values.get("company_name"):
                values["company_name"] = value
            elif question_kind == "what_they_are_building" and not values.get("what_they_are_building"):
                values["what_they_are_building"] = value
            elif question_kind == "what_they_are_building":
                existing = str(values["what_they_are_building"]).strip()
                addition = str(value).strip()
                if addition and addition not in existing:
                    values["what_they_are_building"] = f"{existing}\n{column}: {addition}"
            elif _is_luma_export_metadata(column):
                continue
            else:
                survey_responses[column] = value

        email = _text(values.get("email"))
        if not email or not _looks_like_email(email):
            continue
        normalized_email = email.lower()
        if normalized_email in seen:
            continue
        seen.add(normalized_email)
        values["source"] = values.get("source") or "csv"
        values["survey_responses"] = survey_responses or None
        leads.append(EnrichmentInput(**values))
    return leads


def csv_approval_status_by_email(
    rows: list[dict[str, Any]],
    column_mapping: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return every valid CSV guest's event status without filtering the lead list."""
    if not rows:
        return {}
    mapping = _normalize_mapping(column_mapping) if column_mapping else _detect_mapping(_all_columns(rows))
    email_column = mapping.get("email")
    status_column = _event_status_column(rows)
    if not email_column:
        return {}
    output: dict[str, str] = {}
    for row in rows:
        email = _text(row.get(email_column))
        if not email or not _looks_like_email(email):
            continue
        status = _canonical_registration_status(row.get(status_column)) if status_column else "going"
        output.setdefault(email.lower(), status)
    return output


def csv_rows_with_organization_email(
    rows: list[dict[str, Any]],
    column_mapping: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Keep valid rows whose email domain is not a known personal provider."""
    if not rows:
        return []
    mapping = _normalize_mapping(column_mapping) if column_mapping else _detect_mapping(_all_columns(rows))
    email_column = mapping.get("email")
    if not email_column:
        return []
    return [
        row
        for row in rows
        if (email := _text(row.get(email_column)))
        and _looks_like_email(email)
        and not is_personal_email_domain(email)
    ]


def _unwrap_luma_guest(raw_guest: dict) -> dict:
    if isinstance(raw_guest.get("guest"), dict):
        merged = dict(raw_guest["guest"])
        for key, value in raw_guest.items():
            if key != "guest" and key not in merged:
                merged[key] = value
        return merged
    return raw_guest


def _status(guest: dict) -> str:
    return _text(guest.get("approval_status") or guest.get("status") or "").lower()


def _extract_custom_answers(guest: dict) -> tuple[dict[str, str], dict[str, Any]]:
    answers = (
        guest.get("registration_answers")
        or guest.get("answers")
        or guest.get("custom_fields")
        or guest.get("survey_responses")
        or []
    )
    mapped: dict[str, str] = {}
    survey_responses: dict[str, Any] = {}
    for question, answer in _iter_answers(answers):
        if answer in (None, ""):
            continue
        kind = _question_kind(question)
        if kind in {"company_name", "website_url", "title", "linkedin_url", "what_they_are_building"} and kind not in mapped:
            mapped[kind] = str(answer).strip()
        else:
            survey_responses[question] = answer
    return mapped, survey_responses


def _iter_answers(answers: Any):
    if isinstance(answers, dict):
        for question, answer in answers.items():
            yield str(question), answer
        return
    if isinstance(answers, list):
        for item in answers:
            if not isinstance(item, dict):
                continue
            question = (
                item.get("question")
                or item.get("label")
                or item.get("question_text")
                or item.get("name")
            )
            if question:
                yield str(question), item.get("answer") or item.get("value") or item.get("response")


def _detect_mapping(columns: list[str]) -> dict[str, str]:
    detected: dict[str, str] = {}
    for column in columns:
        normalized = _normalize_name(column)
        for target, aliases in KNOWN_ALIASES.items():
            if target not in detected and normalized in aliases:
                detected[target] = column
        kind = _question_kind(column)
        if kind and kind not in detected:
            detected[kind] = column
    return detected


def _normalize_mapping(column_mapping: Mapping[str, str]) -> dict[str, str]:
    mapping = dict(column_mapping)
    if any(key in MODEL_FIELDS for key in mapping):
        return {target: source for target, source in mapping.items() if target in MODEL_FIELDS}
    return {target: source for source, target in mapping.items() if target in MODEL_FIELDS}


def _all_columns(rows: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(str(key) for row in rows for key in row))


def _question_kind(question: str) -> str | None:
    normalized = _normalize_name(question)
    if "email" in normalized:
        return "email"
    if "linkedin" in normalized:
        return "linkedin_url"
    if "website" in normalized:
        return "website_url"
    if _is_company_name_question(normalized):
        return "company_name"
    if normalized in {"role", "title", "job title"} or "your role" in normalized or "job title" in normalized:
        return "title"
    if (
        "building" in normalized
        or "working on" in normalized
        or "project" in normalized
        or ("founder" in normalized and "startup" in normalized)
        or ("researcher" in normalized and "lab" in normalized)
    ):
        return "what_they_are_building"
    return None


def _is_company_name_question(normalized: str) -> bool:
    """Match employer-name questions without swallowing company survey questions.

    Headers such as "Which best describes your company today?" describe funding
    stage or operating status, not the company's name.  The previous substring
    check treated every question containing "company" as an employer field.
    """
    question = normalized.rstrip(" ?")
    if question in {
        "company",
        "company name",
        "organization",
        "org",
        "account name",
        "what's your company",
        "what is your company",
        "name of your company",
        "what's your company name",
        "what is your company name",
        "organization name",
        "what's your organization",
        "what is your organization",
    }:
        return True
    return any(
        phrase in question
        for phrase in (
            "company do you work for",
            "company are you working for",
            "company are you with",
            "organization do you work for",
            "organization are you working for",
            "organization are you with",
            "where do you work",
        )
    )


def _event_status_column(rows: list[dict[str, Any]]) -> str | None:
    for column in _all_columns(rows):
        if _normalize_name(column) not in EVENT_STATUS_COLUMNS:
            continue
        values = {
            _normalize_status(row.get(column))
            for row in rows
            if _normalize_status(row.get(column))
        }
        if values.intersection(GOING_STATUSES) and values.intersection(EVENT_REGISTRATION_STATUSES):
            return column
    return None


def _name_column(rows: list[dict[str, Any]]) -> str | None:
    for column in _all_columns(rows):
        if _normalize_name(column) in {"name", "full name"}:
            return column
    return None


def _normalize_status(value: Any) -> str:
    return _normalize_name(str(value or ""))


def _canonical_registration_status(value: Any) -> str:
    status = _normalize_status(value)
    aliases = {
        "pending approval": "pending",
        "pending": "pending",
        "waitlisted": "waitlisted",
        "waitlist": "waitlisted",
        "cancelled": "declined",
        "canceled": "declined",
    }
    return aliases.get(status, status or "going")


def _looks_like_email(value: str) -> bool:
    local, sep, domain = value.strip().partition("@")
    return bool(local and sep and "." in domain)


def _is_luma_export_metadata(column: str) -> bool:
    return _normalize_name(column) in LUMA_EXPORT_METADATA_COLUMNS


def _split_name(name: str | None) -> tuple[str | None, str | None]:
    if not name:
        return None, None
    parts = name.split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _normalize_linkedin_url(value: str | None) -> str | None:
    text = _text(value)
    if not text:
        return None
    if text.startswith("/in/"):
        return f"https://www.linkedin.com{text}"
    if text.startswith("linkedin.com/"):
        return f"https://www.{text}"
    if text.startswith("www.linkedin.com/"):
        return f"https://{text}"
    return text


def _normalize_name(value: str) -> str:
    return " ".join(str(value).strip().lower().replace("_", " ").replace("-", " ").split())


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
