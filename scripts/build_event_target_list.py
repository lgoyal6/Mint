#!/usr/bin/env python3
"""GMI Cloud event — target attendee list builder (Apollo only).

Reads an approved ``firms.csv``, discovers 2-4 decision-makers per firm via the
Apollo people-search endpoint, enriches only the people we keep for verified work
email + LinkedIn, normalizes/tiers/dedupes, and writes a formatted two-tab xlsx
(Contacts + Firms).

This is a file-in / file-out tool. It NEVER sends email, posts, writes to a CRM,
or schedules anything. Apollo is the only provider.

Dry-run by default (free people-search only, no credits spent). Pass ``--live``
to additionally call the credit-consuming enrichment endpoint.

Usage:
    APOLLO_API_KEY=... python scripts/build_event_target_list.py --input firms.csv
    APOLLO_API_KEY=... python scripts/build_event_target_list.py --input firms.csv --live
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

log = logging.getLogger("event_targets")

APOLLO_BASE = "https://api.apollo.io/api/v1"

# Persona buckets in priority order (highest priority first). Each has the seed
# title terms and seniority filters fed to Apollo people-search (spec section 4).
PERSONAS: list[dict[str, Any]] = [
    {
        "bucket": "founder/CEO",
        "titles": ["Founder", "Co-Founder", "CEO", "Chief Executive"],
        "seniorities": ["owner", "c_suite"],
    },
    {
        "bucket": "CTO",
        "titles": ["CTO", "Chief Technology Officer"],
        "seniorities": ["c_suite"],
    },
    {
        "bucket": "head of AI/ML",
        "titles": ["Head of AI", "Head of ML", "VP AI", "VP Machine Learning", "Director of AI"],
        "seniorities": ["vp", "director", "head"],
    },
    {
        "bucket": "head of infra",
        "titles": ["Head of Infrastructure", "Head of Platform", "VP Engineering", "Head of MLOps"],
        "seniorities": ["vp", "director", "head"],
    },
    {
        "bucket": "ML eng (senior)",
        "titles": ["Machine Learning Engineer", "ML Engineer", "Inference Engineer"],
        "seniorities": ["senior"],
    },
]

# bucket -> priority index (0 = highest). Used for selection ranking + sort order.
PERSONA_ORDER = {p["bucket"]: i for i, p in enumerate(PERSONAS)}

# Per-tier cap on people kept (and therefore enriched) per firm.
TIER_CAPS = {"A": 4, "B": 3, "C": 3}
DEFAULT_CAP = 3

TIER_RANK = {"A": 0, "B": 1, "C": 2}

CONTACT_COLUMNS = [
    "tier", "company", "modality", "warm_or_cold", "name", "title", "persona",
    "email", "email_status", "linkedin", "location", "source", "notes",
]
FIRM_COLUMNS = [
    "tier", "company", "domain", "modality", "warm_or_cold",
    "contacts_found", "emails_verified", "notes",
]
COLUMN_WIDTHS = {
    "tier": 6, "company": 24, "domain": 22, "modality": 14, "warm_or_cold": 12,
    "name": 22, "title": 32, "persona": 16, "email": 30, "email_status": 13,
    "linkedin": 38, "location": 26, "source": 16, "notes": 36,
    "contacts_found": 14, "emails_verified": 15,
}


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #
def clean_domain(value: str | None) -> str:
    """Normalize a domain: drop scheme, www., and any path."""
    if not value:
        return ""
    text = value.strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = text.split("/")[0]
    text = re.sub(r"^www\.", "", text)
    return text.strip()


def read_firms(path: Path) -> tuple[list[dict[str, str]], int]:
    """Read firms.csv. Returns (valid firms, skipped count). Rows missing a
    domain are logged and skipped."""
    firms: list[dict[str, str]] = []
    skipped = 0
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for line_no, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            company = row.get("company", "")
            domain = clean_domain(row.get("domain"))
            if not domain:
                log.warning("SKIP row %d (%s): missing domain", line_no, company or "?")
                skipped += 1
                continue
            firms.append(
                {
                    "company": company,
                    "domain": domain,
                    "tier": (row.get("tier", "") or "").upper(),
                    "modality": row.get("modality", ""),
                    "warm_or_cold": row.get("warm_or_cold", ""),
                    "notes": row.get("notes", ""),
                }
            )
    return firms, skipped


# --------------------------------------------------------------------------- #
# Apollo
# --------------------------------------------------------------------------- #
class ApolloClient:
    """Thin synchronous Apollo wrapper. Only people-search (free) and single
    people/match (paid) are exposed. bulk_match is deliberately NOT used."""

    def __init__(self, api_key: str, *, enrich_sleep: float = 1.0, max_retries: int = 4) -> None:
        self.enrich_sleep = enrich_sleep
        self.max_retries = max_retries
        self.client = httpx.Client(
            base_url=APOLLO_BASE,
            timeout=45,
            headers={
                "X-Api-Key": api_key,
                "Cache-Control": "no-cache",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self.client.close()

    def search_people(
        self, domain: str, titles: list[str], per_page: int = 100
    ) -> list[dict[str, Any]]:
        """POST /mixed_people/api_search. Free, returns no emails. Scoped to one
        org domain, filtered by persona titles only. person_seniorities is
        deliberately NOT sent: on lower plans the seniority field is null, so
        AND-ing it drops nearly every match. Bucketing/ranking is done on titles.
        Location is NOT filtered here (we down-rank in selection)."""
        params: list[tuple[str, str]] = [
            ("q_organization_domains_list[]", domain),
            ("per_page", str(per_page)),
            ("page", "1"),
        ]
        params += [("person_titles[]", t) for t in titles]
        data = self._request("POST", "/mixed_people/api_search", params=params)
        people = data.get("people")
        return people if isinstance(people, list) else []

    def match_person(self, person_id: str) -> dict[str, Any]:
        """POST /people/match by Apollo person id. CONSUMES a credit. Returns the
        resolved person object (may be empty / have no email)."""
        data = self._request("POST", "/people/match", json={"id": person_id})
        person = data.get("person")
        return person if isinstance(person, dict) else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise
                wait = 2 ** (attempt - 1)
                log.warning("transport error on %s (%s); retry in %.0fs", path, exc, wait)
                time.sleep(wait)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.max_retries:
                    resp.raise_for_status()
                wait = float(resp.headers.get("Retry-After") or 2 ** (attempt - 1))
                log.warning("HTTP %d on %s; retry in %.0fs", resp.status_code, path, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError("unreachable")  # pragma: no cover


# --------------------------------------------------------------------------- #
# Persona + selection
# --------------------------------------------------------------------------- #
def _has_word(text: str, words: list[str]) -> bool:
    return any(re.search(r"\b" + re.escape(w) + r"\b", text) for w in words)


def bucket_persona(title: str | None) -> str | None:
    """Map a raw Apollo title to a normalized persona bucket, or None if it does
    not match any target persona. Checked in priority order."""
    t = (title or "").lower()
    if not t:
        return None
    # Exclude support roles whose titles name a senior person ("Executive
    # Assistant to the CEO", "Technical Associate to the CTO").
    if "assistant" in t or "associate to" in t:
        return None
    # \bfounder\b matches "founder" and "co-founder" (hyphen is a boundary) but
    # NOT "founding engineer/recruiter/gtm/creative director"; add "cofounder".
    if _has_word(t, ["founder", "cofounder", "ceo"]) or "chief executive" in t:
        return "founder/CEO"
    if _has_word(t, ["cto"]) or "chief technology" in t:
        return "CTO"
    is_lead = _has_word(t, ["head", "vp", "vice president", "director", "chief"]) or "lead" in t
    ai_ml = _has_word(t, ["ai", "ml", "machine learning", "artificial intelligence"])
    infra = _has_word(t, ["infrastructure", "infra", "platform", "mlops", "devops", "sre", "engineering"])
    if is_lead and ai_ml:
        return "head of AI/ML"
    if is_lead and infra:
        return "head of infra"
    if _has_word(t, ["engineer"]) and _has_word(t, ["ai", "ml", "machine learning", "inference"]):
        return "ML eng (senior)"
    return None


def _person_location(person: dict[str, Any]) -> str:
    raw = person.get("location")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    parts = [person.get("city"), person.get("state"), person.get("country")]
    return ", ".join(str(p).strip() for p in parts if p)


def _is_us(person: dict[str, Any]) -> bool:
    country = (person.get("country") or "").strip().lower()
    if country in {"united states", "united states of america", "usa", "us"}:
        return True
    loc = _person_location(person).lower()
    hints = ["united states", "california", "san francisco", "los angeles", "bay area",
             "new york", "seattle", "austin", "boston", " ca", " ny"]
    return any(h in loc for h in hints)


def _person_name(person: dict[str, Any]) -> str:
    name = (person.get("name") or "").strip()
    if name:
        return name
    parts = [person.get("first_name"), person.get("last_name")]
    return " ".join(str(p).strip() for p in parts if p).strip()


def search_personas(tier: str) -> list[dict[str, Any]]:
    """Tier A searches tighter (leadership only); B/C wider (incl. senior ML eng)."""
    return PERSONAS[:4] if tier == "A" else PERSONAS


def search_seeds(tier: str) -> list[str]:
    """Unique persona title seeds for a tier's search breadth."""
    titles: list[str] = []
    for persona in search_personas(tier):
        for t in persona["titles"]:
            if t not in titles:
                titles.append(t)
    return titles


def select_contacts(firm: dict[str, str], people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bucket, dedupe by Apollo id, rank, and cap. Returns list of
    {"person", "persona"} dicts. Runs BEFORE enrichment to control credit spend."""
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for person in people:
        pid = str(person.get("id") or "")
        if pid and pid in seen:
            continue
        bucket = bucket_persona(person.get("title"))
        if not bucket:
            continue
        if pid:
            seen.add(pid)
        candidates.append(
            {
                "person": person,
                "persona": bucket,
                "_rank": (PERSONA_ORDER[bucket], 0 if _is_us(person) else 1),
            }
        )
    candidates.sort(key=lambda c: c["_rank"])
    cap = TIER_CAPS.get(firm["tier"], DEFAULT_CAP)
    return candidates[:cap]


# --------------------------------------------------------------------------- #
# Row building + dedupe
# --------------------------------------------------------------------------- #
def build_row(
    firm: dict[str, str],
    selected: dict[str, Any],
    enriched: dict[str, Any] | None,
) -> dict[str, Any]:
    person = selected["person"]
    base = {
        "tier": firm["tier"],
        "company": firm["company"],
        "modality": firm["modality"],
        "warm_or_cold": firm["warm_or_cold"],
        "persona": selected["persona"],
        "notes": firm["notes"],
        "_id": str(person.get("id") or ""),
    }
    if enriched is None:
        # Dry-run: search data only, no email.
        base.update(
            name=_person_name(person),
            title=(person.get("title") or "").strip(),
            email="",
            email_status="",
            linkedin=(person.get("linkedin_url") or "").strip(),
            location=_person_location(person),
            source="search_only",
        )
        return base
    email = (enriched.get("email") or "").strip()
    base.update(
        name=_person_name(enriched) or _person_name(person),
        title=(enriched.get("title") or person.get("title") or "").strip(),
        email=email,
        email_status=(enriched.get("email_status") or "").strip(),
        linkedin=(enriched.get("linkedin_url") or person.get("linkedin_url") or "").strip(),
        location=_person_location(enriched) or _person_location(person),
        source="apollo" if email else "apollo_no_email",
    )
    return base


def _quality_rank(row: dict[str, Any]) -> tuple[int, int]:
    warm = 0 if (row["warm_or_cold"] or "").lower().startswith("warm") else 1
    tier = TIER_RANK.get((row["tier"] or "").upper(), 3)
    return (warm, tier)


def dedupe_global(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same person across firms -> keep one, preferring warm over cold then
    higher tier. Keyed by Apollo id, falling back to name+company."""
    best: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    for row in rows:
        key = row["_id"] or (row["name"].lower(), row["company"].lower())
        if key not in best:
            best[key] = row
            order.append(key)
        elif _quality_rank(row) < _quality_rank(best[key]):
            best[key] = row
    return [best[k] for k in order]


def summarize_firms(
    firms: list[dict[str, str]], contacts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_company: dict[str, list[dict[str, Any]]] = {}
    for c in contacts:
        by_company.setdefault(c["company"].lower(), []).append(c)
    summary = []
    for firm in firms:
        firm_contacts = by_company.get(firm["company"].lower(), [])
        verified = sum(1 for c in firm_contacts if c["email_status"].lower() == "verified")
        summary.append(
            {
                "tier": firm["tier"],
                "company": firm["company"],
                "domain": firm["domain"],
                "modality": firm["modality"],
                "warm_or_cold": firm["warm_or_cold"],
                "contacts_found": len(firm_contacts),
                "emails_verified": verified,
                "notes": firm["notes"],
            }
        )
    return summary


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _write_sheet(ws: Any, columns: list[str], rows: list[list[Any]]) -> None:
    ws.append(columns)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    last_col = get_column_letter(len(columns))
    ws.auto_filter.ref = f"A1:{last_col}{len(rows) + 1}"
    for idx, col in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = COLUMN_WIDTHS.get(col, 18)


def write_xlsx(contacts: list[dict[str, Any]], firms_summary: list[dict[str, Any]], path: Path) -> None:
    wb = Workbook()
    ws_contacts = wb.active
    ws_contacts.title = "Contacts"
    _write_sheet(
        ws_contacts,
        CONTACT_COLUMNS,
        [[c.get(col, "") for col in CONTACT_COLUMNS] for c in contacts],
    )
    ws_firms = wb.create_sheet("Firms")
    _write_sheet(
        ws_firms,
        FIRM_COLUMNS,
        [[f.get(col, "") for col in FIRM_COLUMNS] for f in firms_summary],
    )
    wb.save(path)


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=Path("firms.csv"), help="path to firms.csv")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output xlsx path (default: gmi-event-targets-<YYYYMMDD>.xlsx)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="call the paid Apollo enrichment endpoint (default: dry-run, search only)",
    )
    parser.add_argument(
        "--enrich-sleep",
        type=float,
        default=1.0,
        help="seconds to sleep between enrichment calls (live mode only)",
    )
    parser.add_argument("--per-page", type=int, default=100, help="Apollo search page size (max 100)")
    return parser.parse_args(argv)


def run(args: argparse.Namespace, client: ApolloClient) -> tuple[list[dict[str, Any]], dict[str, int]]:
    firms, skipped = read_firms(args.input)
    stats = {"processed": 0, "skipped": skipped, "found": 0, "credits": 0, "enrich_errors": 0}
    rows: list[dict[str, Any]] = []
    credits_exhausted = False
    for firm in firms:
        titles = search_seeds(firm["tier"])
        people = client.search_people(firm["domain"], titles, per_page=args.per_page)
        stats["processed"] += 1
        stats["found"] += len(people)
        selected = select_contacts(firm, people)
        log.info(
            "%s [%s] %s: %d found, %d selected",
            firm["company"], firm["tier"] or "?", firm["domain"], len(people), len(selected),
        )
        for sel in selected:
            enriched: dict[str, Any] | None = None
            who = _person_name(sel["person"]) or sel["person"].get("id")
            if args.live and not credits_exhausted:
                try:
                    enriched = client.match_person(sel["person"]["id"])
                    stats["credits"] += 1
                except httpx.HTTPStatusError as exc:
                    body = exc.response.text if exc.response is not None else ""
                    if "insufficient credits" in body.lower():
                        log.error(
                            "Apollo reports INSUFFICIENT ENRICHMENT CREDITS — stopping "
                            "enrichment; remaining rows will hold search data only."
                        )
                        credits_exhausted = True
                    else:
                        stats["enrich_errors"] += 1
                        log.warning("enrichment failed for %s @ %s: %s", who, firm["company"], exc)
                    enriched = None
                else:
                    if not (enriched.get("email") or "").strip():
                        log.warning("no email for %s @ %s (apollo_no_email)", who, firm["company"])
                    if args.enrich_sleep > 0:
                        time.sleep(args.enrich_sleep)
            rows.append(build_row(firm, sel, enriched))
    contacts = dedupe_global(rows)
    contacts.sort(
        key=lambda r: (TIER_RANK.get((r["tier"] or "").upper(), 3), PERSONA_ORDER.get(r["persona"], 99), r["company"].lower())
    )
    firms_summary = summarize_firms(firms, contacts)
    write_xlsx(contacts, firms_summary, args.output)
    stats["credits_exhausted"] = int(credits_exhausted)
    return contacts, stats


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    if args.output is None:
        args.output = Path(f"gmi-event-targets-{date.today():%Y%m%d}.xlsx")

    api_key = os.environ.get("APOLLO_API_KEY")
    if not api_key:
        log.error("APOLLO_API_KEY is not set (env var required; must be a master key)")
        return 2
    if not args.input.exists():
        log.error("input file not found: %s", args.input)
        return 2

    if not args.live:
        log.info("DRY-RUN: people-search only, no enrichment, no credits spent. Use --live to enrich.")

    client = ApolloClient(api_key, enrich_sleep=args.enrich_sleep)
    try:
        contacts, stats = run(args, client)
    finally:
        client.close()

    verified = sum(1 for c in contacts if c["email_status"].lower() == "verified")
    with_email = sum(1 for c in contacts if c["email"])
    print("\n=== run summary ===")
    print(f"firms processed : {stats['processed']}")
    print(f"firms skipped   : {stats['skipped']} (missing domain)")
    print(f"people found    : {stats['found']} (search)")
    print(f"contacts kept   : {len(contacts)} (after cap + global dedupe)")
    if args.live:
        print(f"emails: {with_email} with email ({verified} verified, {with_email - verified} unverified), "
              f"{len(contacts) - with_email} blank")
        print(f"enrich errors  : {stats['enrich_errors']}")
        print(f"credits consumed: ~{stats['credits']} (1 per enrichment call)")
        if stats.get("credits_exhausted"):
            print("NOTE: Apollo enrichment credits exhausted — emails are blank. "
                  "Add credits and re-run with --live to fill them.")
    else:
        print("emails: none (dry-run). Re-run with --live to enrich.")
        print("credits consumed: 0 (dry-run)")
    print(f"written: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
