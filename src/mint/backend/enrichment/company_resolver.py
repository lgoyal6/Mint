import re
from pathlib import Path
from typing import Any

from mint.backend.models import ApolloPersonResult, CandidateCompany, ResolvedIdentity
from mint.backend.utils.domain import clean_domain, email_domain, is_personal_email_domain


DEFAULT_GARBAGE_TERMS = {
    "stealth",
    "stealth startup",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "undisclosed",
    "confidential",
    "freelance",
    "self-employed",
    "self employed",
    "independent",
    "student",
    "unemployed",
    "founder",
    "ceo",
    "cto",
    "engineer",
    "software engineer",
    "consultant",
    "advisor",
}

PEOPLE_SEARCH_CORROBORATION_THRESHOLD = 2
COMMON_COMPANY_TOKENS = {
    "ai",
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "lab",
    "labs",
    "llc",
    "ltd",
    "technology",
    "technologies",
    "the",
}
LEGAL_COMPANY_TOKENS = {"co", "company", "corp", "corporation", "inc", "llc", "ltd", "limited", "plc"}


class CompanyResolver:
    def __init__(
        self,
        exa: Any,
        *,
        blocklist_path: str | Path = "config/garbage_company_terms.txt",
        alias_store: Any = None,
    ) -> None:
        self.exa = exa
        self.blocklist_path = Path(blocklist_path)
        self.garbage_terms = self._load_blocklist(self.blocklist_path)
        self.alias_store = alias_store
        self._people_search_results: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def resolve(
        self,
        email: str,
        raw_company: str | None,
        apollo_person: ApolloPersonResult | None,
        *,
        linkedin_url: str | None = None,
        title: str | None = None,
        website_url: str | None = None,
    ) -> ResolvedIdentity:
        raw_input = raw_company or ""
        evidence: list[str] = []
        person_state, person_source = self._person_identity(apollo_person, evidence)
        person_name = self._person_name(apollo_person)
        person_title = apollo_person.title if apollo_person and apollo_person.title else title
        seniority = apollo_person.seniority if apollo_person else None

        apollo_domain = clean_domain(apollo_person.organization_domain) if apollo_person else None
        if apollo_domain and is_personal_email_domain(apollo_domain):
            apollo_domain = None
        apollo_candidate = (
            CandidateCompany(
                domain=apollo_domain,
                name=_text(apollo_person.organization_name) if apollo_person else None,
                source="apollo_org",
                note="Apollo person match returned this employer",
            )
            if apollo_domain
            else None
        )
        if apollo_candidate:
            evidence.append(f"Apollo returned employer domain {apollo_candidate.domain}")

        work_domain = email_domain(email)
        if work_domain and is_personal_email_domain(work_domain):
            evidence.append(f"Email domain {work_domain} is personal")
            work_domain = None
        elif work_domain and _is_academic_domain(work_domain):
            evidence.append(f"Email domain {work_domain} is academic and not used as a company-employer signal")
            work_domain = None
        elif work_domain:
            evidence.append(f"Email domain {work_domain} is a work-domain signal")
        if work_domain and _looks_like_personal_vanity_domain(work_domain, email, person_name):
            evidence.append(
                f"Email domain {work_domain} resembles the attendee's name and is treated as a personal vanity domain"
            )
            work_domain = None
        email_candidate = (
            CandidateCompany(
                domain=work_domain,
                source="email_domain",
                note="Non-personal email domain",
            )
            if work_domain
            else None
        )

        if apollo_candidate and email_candidate:
            if await self._same_company(apollo_candidate, email_candidate):
                evidence.append(f"Email domain {email_candidate.domain} matches Apollo employer")
                return self._identity(
                    person_state=person_state,
                    person_source=person_source,
                    name=person_name,
                    title=person_title,
                    seniority=seniority,
                    company_state="verified",
                    company_source="apollo_org",
                    domain=apollo_candidate.domain,
                    company_name=apollo_candidate.name,
                    affiliation_state="verified",
                    evidence=evidence,
                )
            evidence.append(
                f"Apollo returned {apollo_candidate.domain} but email domain is {email_candidate.domain} "
                "-> affiliation: contradicted"
            )
            return self._contradicted_identity(
                person_state,
                person_source,
                person_name,
                person_title,
                seniority,
                [apollo_candidate, email_candidate],
                evidence,
            )

        website_domain = clean_domain(website_url)
        if website_domain and not is_personal_email_domain(website_domain):
            evidence.append(f"Supplied company website resolved directly to {website_domain}")
            website_candidate = CandidateCompany(
                domain=website_domain,
                name=_text(raw_company),
                source="self_reported_website",
                note="Company website supplied in the event registration",
            )
            if apollo_candidate:
                if await self._same_company(website_candidate, apollo_candidate):
                    evidence.append("Supplied website corroborates Apollo employer")
                    return self._identity(
                        person_state=person_state,
                        person_source=person_source,
                        name=person_name,
                        title=person_title,
                        seniority=seniority,
                        company_state="verified",
                        company_source="apollo_org",
                        domain=apollo_candidate.domain,
                        company_name=apollo_candidate.name or website_candidate.name,
                        affiliation_state="verified",
                        evidence=evidence,
                    )
                evidence.append(
                    f"Apollo employer {apollo_candidate.domain} conflicts with supplied company website "
                    f"{website_candidate.domain}"
                )
                return self._contradicted_identity(
                    person_state,
                    person_source,
                    person_name,
                    person_title,
                    seniority,
                    [apollo_candidate, website_candidate],
                    evidence,
                )
            evidence.append("Supplied company website is accepted as first-party company evidence")
            return self._identity(
                person_state=person_state,
                person_source=person_source,
                name=person_name,
                title=person_title,
                seniority=seniority,
                company_state="verified",
                company_source="self_reported_website",
                domain=website_candidate.domain,
                company_name=website_candidate.name,
                affiliation_state="inferred" if person_state == "verified" else "unverified",
                evidence=evidence,
            )

        raw_is_garbage = bool(raw_company and self._is_garbage(raw_company))
        if raw_is_garbage:
            evidence.append(f'Self-reported company "{raw_input}" is garbage/unusable')

        if apollo_candidate:
            self_reported_candidate = None
            if raw_company and not raw_is_garbage:
                self_reported_candidate = await self._company_search_candidate(raw_company, evidence)
            if self_reported_candidate and not await self._same_company(self_reported_candidate, apollo_candidate):
                evidence.append(
                    f"Apollo returned {apollo_candidate.domain} but self-reported company resolved to "
                    f"{self_reported_candidate.domain} -> affiliation: contradicted"
                )
                return self._contradicted_identity(
                    person_state,
                    person_source,
                    person_name,
                    person_title,
                    seniority,
                    [apollo_candidate, self_reported_candidate],
                    evidence,
                )
            if self_reported_candidate and await self._same_company(self_reported_candidate, apollo_candidate):
                evidence.append(f"Self-reported company corroborates Apollo employer {apollo_candidate.domain}")
                affiliation = "verified"
            else:
                affiliation = "inferred"
                evidence.append("Apollo employer is a single employment signal -> affiliation: inferred")
            return self._identity(
                person_state=person_state,
                person_source=person_source,
                name=person_name,
                title=person_title,
                seniority=seniority,
                company_state="verified",
                company_source="apollo_org",
                domain=apollo_candidate.domain,
                company_name=apollo_candidate.name,
                affiliation_state=affiliation,
                evidence=evidence,
            )

        if email_candidate:
            if raw_company and not raw_is_garbage and not self._company_text_matches_domain(raw_company, email_candidate.domain):
                self_reported_candidate = await self._company_search_candidate(raw_company, evidence)
                if self_reported_candidate and not await self._same_company(self_reported_candidate, email_candidate):
                    evidence.append(
                        f"Email domain is {email_candidate.domain} but self-reported company resolved to "
                        f"{self_reported_candidate.domain} -> affiliation: contradicted"
                    )
                    return self._contradicted_identity(
                        person_state,
                        person_source,
                        person_name,
                        person_title,
                        seniority,
                        [email_candidate, self_reported_candidate],
                        evidence,
                    )
            affiliation = "inferred" if apollo_person and apollo_person.matched else "verified"
            evidence.append(f"Email domain {email_candidate.domain} sets company identity")
            evidence.append(f"-> affiliation: {affiliation}")
            return self._identity(
                person_state=person_state,
                person_source=person_source,
                name=person_name,
                title=person_title,
                seniority=seniority,
                company_state="verified",
                company_source="email_domain",
                domain=email_candidate.domain,
                company_name=None,
                affiliation_state=affiliation,
                evidence=evidence,
            )

        if raw_is_garbage:
            return self._identity(
                person_state=person_state,
                person_source=person_source,
                name=person_name,
                title=person_title,
                seniority=seniority,
                company_state="garbage",
                company_source="none",
                domain=None,
                company_name=None,
                affiliation_state="unverified",
                evidence=evidence,
            )

        if raw_company:
            company_candidate = await self._company_search_candidate(raw_company, evidence)
            if company_candidate:
                affiliation = "inferred" if apollo_person and apollo_person.matched else "unverified"
                confirmed_person_state = person_state
                confirmed_person_source = person_source
                if affiliation == "unverified":
                    people_candidate = await self._people_search_candidate(
                        email=email,
                        raw_company=raw_company,
                        name=person_name,
                        linkedin_url=linkedin_url,
                        title=person_title,
                        evidence=evidence,
                        expected_company=company_candidate,
                    )
                    if people_candidate:
                        _, linkedin_verified = people_candidate
                        affiliation = "inferred"
                        if person_state != "verified" and linkedin_verified:
                            confirmed_person_state = "verified"
                            confirmed_person_source = "linkedin_url"
                            evidence.append("Supplied LinkedIn URL matched Exa people result (person: verified)")
                        elif person_state != "verified":
                            confirmed_person_state = "inferred"
                            confirmed_person_source = "exa_people"
                        evidence.append(
                            f"Exa people search corroborated affiliation with {company_candidate.domain}"
                        )
                if not apollo_person or not apollo_person.matched:
                    if affiliation == "unverified":
                        evidence.append("No Apollo match and no corporate email corroboration caps affiliation at unverified")
                evidence.append(f"-> affiliation: {affiliation}")
                return self._identity(
                    person_state=confirmed_person_state,
                    person_source=confirmed_person_source,
                    name=person_name,
                    title=person_title,
                    seniority=seniority,
                    company_state="inferred",
                    company_source="exa_company_search",
                    domain=company_candidate.domain,
                    company_name=company_candidate.name,
                    affiliation_state=affiliation,
                    evidence=evidence,
                )

            people_candidate = await self._people_search_candidate(
                email=email,
                raw_company=raw_company,
                name=person_name,
                linkedin_url=linkedin_url,
                title=person_title,
                evidence=evidence,
            )
            if people_candidate:
                people_company, linkedin_verified = people_candidate
                affiliation = "inferred" if apollo_person and apollo_person.matched else "unverified"
                if not apollo_person or not apollo_person.matched:
                    evidence.append("No Apollo match and no corporate email corroboration caps affiliation at unverified")
                evidence.append(f"-> affiliation: {affiliation}")
                resolved_person_state = person_state
                resolved_person_source = person_source
                if person_state != "verified" and linkedin_verified:
                    resolved_person_state = "verified"
                    resolved_person_source = "linkedin_url"
                    evidence.append("Supplied LinkedIn URL matched Exa people result (person: verified)")
                elif person_state != "verified":
                    resolved_person_state = "inferred"
                    resolved_person_source = "exa_people"
                return self._identity(
                    person_state=resolved_person_state,
                    person_source=resolved_person_source,
                    name=person_name,
                    title=person_title,
                    seniority=seniority,
                    company_state="inferred",
                    company_source="exa_people_search",
                    domain=people_company.domain,
                    company_name=people_company.name,
                    affiliation_state=affiliation,
                    evidence=evidence,
                )

        evidence.append("No usable company identity resolved")
        return self._identity(
            person_state=person_state,
            person_source=person_source,
            name=person_name,
            title=person_title,
            seniority=seniority,
            company_state="unresolved",
            company_source="none",
            domain=None,
            company_name=None,
            affiliation_state="unverified",
            evidence=evidence,
        )

    def people_search_results_for(self, email: str, raw_company: str | None) -> list[dict[str, Any]] | None:
        return self._people_search_results.get(self._people_search_key(email, raw_company))

    async def _same_company(self, first: CandidateCompany, second: CandidateCompany) -> bool:
        first_domain = clean_domain(first.domain)
        second_domain = clean_domain(second.domain)
        if not first_domain or not second_domain:
            return False
        if first_domain == second_domain:
            return True
        if self.alias_store is not None:
            first_canonical = await self.alias_store.canonical_domain(first_domain)
            second_canonical = await self.alias_store.canonical_domain(second_domain)
            if first_canonical == second_canonical:
                return True
        # Different domains are accepted automatically only when both providers
        # return the same meaningful company name. Legal suffixes and punctuation
        # are ignored, but product words such as "AI" are deliberately retained.
        first_name = _canonical_company_name(first.name)
        second_name = _canonical_company_name(second.name)
        return bool(first_name and first_name == second_name and len(first_name) >= 4)

    def _identity(
        self,
        *,
        person_state,
        person_source,
        name: str | None,
        title: str | None,
        seniority: str | None,
        company_state,
        company_source,
        domain: str | None,
        company_name: str | None,
        affiliation_state,
        evidence: list[str],
    ) -> ResolvedIdentity:
        confidence = self._confidence(company_state, affiliation_state, person_state)
        evidence.append(f"-> confidence: {confidence}")
        return ResolvedIdentity(
            person_source=person_source,
            name=name,
            title=title,
            seniority=seniority,
            company_source=company_source,
            domain=domain,
            company_name=company_name,
            affiliation_state=affiliation_state,
            confidence=confidence,
            evidence=self._resolution_evidence(person_state, company_state, evidence),
        )

    @staticmethod
    def _contradicted_identity(
        person_state,
        person_source,
        name: str | None,
        title: str | None,
        seniority: str | None,
        candidates: list[CandidateCompany],
        evidence: list[str],
    ) -> ResolvedIdentity:
        evidence.append("-> confidence: flagged")
        return ResolvedIdentity(
            person_source=person_source,
            name=name,
            title=title,
            seniority=seniority,
            company_source="none",
            domain=None,
            company_name=None,
            affiliation_state="contradicted",
            confidence="flagged",
            evidence=CompanyResolver._resolution_evidence(person_state, "unresolved", evidence),
            candidate_companies=_dedupe_candidates(candidates),
        )

    @staticmethod
    def _resolution_evidence(person_state: str, company_state: str, chain: list[str]) -> dict[str, Any]:
        return {
            "person_state": person_state,
            "company_state": company_state,
            "chain": list(chain),
        }

    async def _company_search_candidate(self, raw_company: str, evidence: list[str]) -> CandidateCompany | None:
        company_results = await self.exa.company_search(raw_company.strip())
        for result in company_results:
            result_domain = clean_domain(result.get("domain") or result.get("url"))
            if result_domain and not is_personal_email_domain(result_domain):
                result_name = _text(result.get("name"))
                if not self._company_candidate_matches_raw(raw_company, result_domain, result_name):
                    evidence.append(
                        f'Self-reported company "{raw_company}" did not exactly match Exa result '
                        f"{result_name or result_domain}"
                    )
                    continue
                evidence.append(f'Self-reported company "{raw_company}" resolved to {result_domain} via Exa company search')
                return CandidateCompany(
                    domain=result_domain,
                    name=result_name,
                    source="exa_company_search",
                    note=f'Self-reported company "{raw_company}" resolved through Exa company search',
                )
        evidence.append(f'Exa company search found no usable domain for "{raw_company}"')
        return None

    async def _people_search_candidate(
        self,
        *,
        email: str,
        raw_company: str,
        name: str | None,
        linkedin_url: str | None,
        title: str | None,
        evidence: list[str],
        expected_company: CandidateCompany | None = None,
    ) -> tuple[CandidateCompany, bool] | None:
        if not name:
            evidence.append("Skipped Exa people search because no person name is available")
            return None
        people_results = await self.exa.people_search(name, raw_company.strip())
        self._people_search_results[self._people_search_key(email, raw_company)] = people_results
        for result in people_results:
            score = _people_result_score(result, name, raw_company, linkedin_url, title, expected_company)
            if score < PEOPLE_SEARCH_CORROBORATION_THRESHOLD:
                continue
            result_domain = self._domain_from_people_result(result)
            if result_domain and not is_personal_email_domain(result_domain):
                evidence.append(
                    f"Exa people search corroborated {name} and {raw_company} with score {score}"
                )
                return (
                    CandidateCompany(
                        domain=result_domain,
                        name=self._company_name_from_people_result(result),
                        source="exa_people_search",
                        note=f"People-search result met corroboration threshold {score}",
                    ),
                    _linkedin_url_matches_result(linkedin_url, result),
                )
        profile_candidate = await self._profile_affiliation_candidate(
            people_results=people_results,
            name=name,
            raw_company=raw_company,
            linkedin_url=linkedin_url,
            title=title,
            expected_company=expected_company,
            evidence=evidence,
        )
        if profile_candidate:
            return profile_candidate
        if people_results:
            evidence.append("Exa people search returned candidates, but none met corroboration threshold")
        return None

    async def _profile_affiliation_candidate(
        self,
        *,
        people_results: list[dict[str, Any]],
        name: str,
        raw_company: str,
        linkedin_url: str | None,
        title: str | None,
        expected_company: CandidateCompany | None,
        evidence: list[str],
    ) -> tuple[CandidateCompany, bool] | None:
        if expected_company is None:
            return None
        lookup = getattr(self.exa, "profile_affiliation_search", None)
        if lookup is None:
            return None
        company = expected_company.name or raw_company
        for candidate in people_results:
            candidate_url = _text(candidate.get("url"))
            if not candidate_url or "linkedin.com/in/" not in candidate_url:
                continue
            if not _result_has_exact_name(candidate, name):
                continue
            profile_results = await lookup(name, candidate_url, company)
            for result in profile_results:
                if not _linkedin_url_matches_result(candidate_url, result):
                    continue
                score = _people_result_score(result, name, raw_company, linkedin_url, title, expected_company)
                if score < PEOPLE_SEARCH_CORROBORATION_THRESHOLD:
                    continue
                evidence.append(
                    f"Follow-up LinkedIn profile check corroborated {name} with {expected_company.domain}"
                )
                return (
                    CandidateCompany(
                        domain=expected_company.domain,
                        name=expected_company.name,
                        source="exa_people_search",
                        note=f"Exact LinkedIn profile follow-up met corroboration threshold {score}",
                    ),
                    _linkedin_url_matches_result(linkedin_url, result),
                )
        return None

    def _load_blocklist(self, path: Path) -> set[str]:
        if not path.exists():
            return set(DEFAULT_GARBAGE_TERMS)
        terms = {
            self._normalize_company_term(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        return terms | DEFAULT_GARBAGE_TERMS

    def _is_garbage(self, raw_company: str | None) -> bool:
        normalized = self._normalize_company_term(raw_company)
        if not normalized:
            return True
        return normalized in self.garbage_terms

    @staticmethod
    def _person_identity(
        person: ApolloPersonResult | None,
        evidence: list[str],
    ) -> tuple[str, str]:
        if person and person.matched:
            evidence.append("Apollo matched person by email (person: verified)")
            return "verified", "apollo"
        if CompanyResolver._person_name(person):
            evidence.append("Using self-reported person name (person: self_reported)")
            return "self_reported", "self_reported"
        evidence.append("No usable person identity signal (person: unknown)")
        return "unknown", "self_reported"

    @staticmethod
    def _confidence(company_state: str, affiliation_state: str, person_state: str) -> str:
        if affiliation_state == "contradicted":
            return "flagged"
        if affiliation_state == "verified" and person_state != "unknown":
            return "high"
        if affiliation_state == "inferred" and person_state != "unknown":
            return "medium"
        if affiliation_state == "unverified":
            return "low"
        return "low"

    @staticmethod
    def _normalize_company_term(value: str | None) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())

    @staticmethod
    def _company_text_matches_domain(raw_company: str, domain: str | None) -> bool:
        if not raw_company or not domain:
            return False
        raw_tokens = set(_distinctive_company_tokens(raw_company))
        domain_root = domain.split(".", 1)[0].replace("-", " ")
        return bool(raw_tokens and any(token in domain_root for token in raw_tokens))

    @staticmethod
    def _company_candidate_matches_raw(raw_company: str, domain: str | None, name: str | None) -> bool:
        raw_domain = _raw_company_domain(raw_company)
        if raw_domain:
            return raw_domain == domain
        raw_tokens = _distinctive_company_tokens(raw_company)
        if not raw_tokens:
            return False
        candidate_text = " ".join(part for part in [name, domain] if part)
        candidate_tokens = set(_company_tokens(candidate_text))
        domain_root = (domain or "").split(".", 1)[0]
        candidate_compact = re.sub(r"[^a-z0-9]+", "", candidate_text.lower())
        return any(
            token in candidate_tokens or token in domain_root or token in candidate_compact
            for token in raw_tokens
        )

    @staticmethod
    def _person_name(person: ApolloPersonResult | None) -> str | None:
        if not person:
            return None
        if person.first_name and person.last_name:
            return f"{person.first_name} {person.last_name}".strip()
        return person.name

    @staticmethod
    def _domain_from_people_result(result: dict[str, Any]) -> str | None:
        company = result.get("company") if isinstance(result.get("company"), dict) else {}
        return (
            clean_domain(company.get("domain"))
            or clean_domain(result.get("company_domain"))
            or clean_domain(result.get("domain"))
            or clean_domain(result.get("company_url"))
        )

    @staticmethod
    def _company_name_from_people_result(result: dict[str, Any]) -> str | None:
        company = result.get("company") if isinstance(result.get("company"), dict) else {}
        if company.get("name"):
            return _text(company.get("name"))
        title = _text(result.get("title"))
        if " at " in title:
            return title.rsplit(" at ", 1)[-1].strip() or None
        return None

    @staticmethod
    def _people_search_key(email: str, raw_company: str | None) -> tuple[str, str]:
        return (email.strip().lower(), (raw_company or "").strip().lower())


def _people_result_score(
    result: dict[str, Any],
    name: str,
    raw_company: str,
    linkedin_url: str | None,
    title: str | None,
    expected_company: CandidateCompany | None = None,
) -> int:
    haystack = _people_result_text(result)
    name_tokens = [token for token in name.lower().split() if token]
    if name_tokens and not all(token in haystack for token in name_tokens):
        return 0

    score = 0
    if _linkedin_url_matches_result(linkedin_url, result):
        score += 2
    company_term = CompanyResolver._normalize_company_term(raw_company)
    if company_term and company_term in haystack:
        score += 1
    result_domain = CompanyResolver._domain_from_people_result(result)
    if expected_company and expected_company.domain:
        if result_domain == expected_company.domain:
            score += 2
        else:
            expected_domain_root = expected_company.domain.split(".", 1)[0]
            haystack_tokens = set(_company_tokens(haystack))
            if len(expected_domain_root) >= 4 and expected_domain_root in haystack_tokens:
                score += 2
    if result_domain and CompanyResolver._company_text_matches_domain(raw_company, result_domain):
        score += 1
    if title and CompanyResolver._normalize_company_term(title) in haystack:
        score += 1
    return score


def _distinctive_company_tokens(value: str) -> list[str]:
    return [
        token
        for token in _company_tokens(value)
        if token not in COMMON_COMPANY_TOKENS and len(token) >= 2
    ]


def _raw_company_domain(value: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if re.search(r"\s", text) or "." not in text:
        return None
    domain = clean_domain(text)
    return domain if domain and "." in domain and not is_personal_email_domain(domain) else None


def _is_academic_domain(domain: str) -> bool:
    return domain.endswith(".edu")


def _looks_like_personal_vanity_domain(
    domain: str,
    email: str,
    person_name: str | None,
) -> bool:
    """Detect name-owned domains so they do not contradict a verified employer."""
    root = domain.split(".", 1)[0]
    root_compact = re.sub(r"[^a-z0-9]+", "", root.lower())
    local_compact = re.sub(r"[^a-z0-9]+", "", email.split("@", 1)[0].lower())
    name_tokens = [
        token for token in re.sub(r"[^a-z0-9]+", " ", (person_name or "").lower()).split()
        if len(token) >= 2
    ]
    name_compact = "".join(name_tokens)
    if not root_compact or not name_tokens:
        return False
    if root_compact == name_compact:
        return True
    if len(name_tokens) >= 2 and all(token in root_compact for token in name_tokens):
        return True
    return (
        len(local_compact) >= 6
        and local_compact == root_compact
        and any(token in root_compact for token in name_tokens)
    )


def _company_tokens(value: str) -> list[str]:
    return [token for token in re.sub(r"[^a-z0-9]+", " ", value.lower()).split() if token]


def _canonical_company_name(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(token for token in _company_tokens(value) if token not in LEGAL_COMPANY_TOKENS)


def _people_result_text(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("title", "url", "domain", "company_domain", "company_url"):
        value = result.get(key)
        if value:
            parts.append(str(value))
    company = result.get("company") if isinstance(result.get("company"), dict) else {}
    for key in ("name", "domain", "url"):
        value = company.get(key)
        if value:
            parts.append(str(value))
    highlights = result.get("highlights")
    if isinstance(highlights, list):
        parts.extend(str(item) for item in highlights if item)
    return " ".join(parts).lower()


def _result_has_exact_name(result: dict[str, Any], name: str) -> bool:
    title = _text(result.get("title")) or ""
    title_tokens = set(_company_tokens(title))
    name_tokens = set(_company_tokens(name))
    return bool(name_tokens and name_tokens.issubset(title_tokens))


def _normalized_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().lower()
    text = text.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return text.rstrip("/")


def _linkedin_url_matches_result(linkedin_url: str | None, result: dict[str, Any]) -> bool:
    return bool(linkedin_url and _normalized_url(linkedin_url) == _normalized_url(result.get("url")))


def _dedupe_candidates(candidates: list[CandidateCompany]) -> list[CandidateCompany]:
    seen: set[tuple[str | None, str]] = set()
    deduped: list[CandidateCompany] = []
    for candidate in candidates:
        key = (candidate.domain, candidate.source)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
