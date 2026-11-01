import asyncio

from mint.backend.enrichment.company_resolver import CompanyResolver
from mint.backend.models import ApolloPersonResult


def run(coro):
    return asyncio.run(coro)


class FakeExa:
    def __init__(self) -> None:
        self.company_queries: list[str] = []
        self.people_queries: list[tuple[str, str]] = []
        self.profile_queries: list[tuple[str, str, str]] = []

    async def company_search(self, query: str) -> list[dict]:
        self.company_queries.append(query)
        if query == "Wobble":
            return [{"name": "Wobble Genomics", "domain": "wobblegenomics.com"}]
        if query == "Quadex AI":
            return [{"name": "Quadex AI", "domain": "quadex.ai"}]
        if query == "Skalpel AI":
            return [
                {
                    "name": "Scalpel AI | Surgical Tray Validation & Intelligence",
                    "domain": "scalpel.ai",
                }
            ]
        if query == "Contor.ai":
            return [{"name": "Contor Group SA", "domain": "contorgroup.com"}]
        if query == "Caprae Capital Partners":
            return [
                {
                    "name": "Caprae Capital Services - End-to-End Private Equity Services",
                    "domain": "capraecapital.com",
                }
            ]
        if query == "Sift Science Inc":
            return [{"name": "Sift Science", "domain": "sift.com"}]
        if query == "LangChain":
            return [{"name": "LangChain", "domain": "langchain.com"}]
        if query == "OpenRelay":
            return [{"name": "OpenRelay", "domain": "openrelay.xyz"}]
        if query == "KIPO":
            return [{"name": "KIPO", "domain": "kipo.go.kr"}]
        return []
    async def people_search(self, name: str, company: str) -> list[dict]:
        self.people_queries.append((name, company))
        if company == "Unknown AI":
            return [
                {
                    "title": f"{name} - CTO at Unknown AI",
                    "url": "https://linkedin.com/in/example",
                    "company": {"name": "Unknown AI", "domain": "unknown.ai"},
                    "highlights": ["CTO at Unknown AI"],
                }
            ]
        if company == "Sift Science Inc":
            return [
                {
                    "title": f"{name} - Software Engineer at Sift",
                    "url": "https://linkedin.com/in/varun",
                    "company": {"name": "Sift", "domain": "sift.com"},
                    "highlights": ["Software Engineer at Sift"],
                }
            ]
        if company == "LangChain":
            return [
                {
                    "title": "Andrew Corcoran",
                    "url": "https://linkedin.com/in/andrewkcorcoran",
                    "highlights": ["Low latency C++ Software Developer at Auros"],
                },
                {
                    "title": "AJ Corcoran",
                    "url": "https://linkedin.com/in/aj-corcoran",
                    "highlights": ["GTM at LangChain"],
                },
            ]
        return [{"title": f"{name} - Software Engineer at PagerDuty", "url": "https://linkedin.com/in/lucas"}]

    async def profile_affiliation_search(self, name: str, linkedin_url: str, company: str) -> list[dict]:
        self.profile_queries.append((name, linkedin_url, company))
        if linkedin_url == "https://linkedin.com/in/andrewkcorcoran" and company == "LangChain":
            return [
                {
                    "title": "Andrew Corcoran",
                    "url": "https://linkedin.com/in/andrewkcorcoran",
                    "highlights": ["Software Engineer at LangChain"],
                }
            ]
        return []


class FakeAliasStore:
    async def canonical_domain(self, domain: str) -> str:
        return {
            "legacy.example": "current.example",
            "current.example": "current.example",
        }.get(domain, domain)


def resolver(tmp_path, fake_exa: FakeExa | None = None) -> CompanyResolver:
    blocklist = tmp_path / "garbage_company_terms.txt"
    blocklist.write_text("stealth\nn/a\n", encoding="utf-8")
    return CompanyResolver(exa=fake_exa or FakeExa(), blocklist_path=blocklist)


def evidence_chain(resolution):
    return resolution.evidence["chain"]


def evidence_state(resolution, key: str) -> str:
    return resolution.evidence[key]


def test_verified_when_work_email_and_apollo_employer_agree_short_circuits_exa(tmp_path) -> None:
    fake_exa = FakeExa()
    person = ApolloPersonResult(
        matched=True,
        email="alex@wobblegenomics.com",
        first_name="Alex",
        last_name="Rivera",
        title="Founder",
        organization_name="Wobble Genomics",
        organization_domain="wobblegenomics.com",
    )

    resolution = run(resolver(tmp_path, fake_exa).resolve("alex@wobblegenomics.com", "Wobble", person))

    assert evidence_state(resolution, "person_state") == "verified"
    assert evidence_state(resolution, "company_state") == "verified"
    assert resolution.domain == "wobblegenomics.com"
    assert resolution.affiliation_state == "verified"
    assert resolution.confidence == "high"
    assert fake_exa.company_queries == []
    assert fake_exa.people_queries == []
    assert any("Apollo matched person by email" in line for line in evidence_chain(resolution))
    assert any("Email domain wobblegenomics.com matches Apollo employer" in line for line in evidence_chain(resolution))


def test_verified_when_work_email_domain_is_available_without_apollo(tmp_path) -> None:
    person = ApolloPersonResult(
        matched=False,
        email="alex@wobblegenomics.com",
        first_name="Alex",
        last_name="Rivera",
    )

    resolution = run(resolver(tmp_path).resolve("alex@wobblegenomics.com", None, person))

    assert evidence_state(resolution, "person_state") == "self_reported"
    assert evidence_state(resolution, "company_state") == "verified"
    assert resolution.company_source == "email_domain"
    assert resolution.domain == "wobblegenomics.com"
    assert resolution.affiliation_state == "verified"
    assert resolution.confidence == "high"


def test_inferred_when_apollo_person_and_work_email_are_consistent_without_employer(tmp_path) -> None:
    fake_exa = FakeExa()
    person = ApolloPersonResult(
        matched=True,
        email="alex@quadex.ai",
        first_name="Alex",
        last_name="Rivera",
        title="Founder",
    )

    resolution = run(resolver(tmp_path, fake_exa).resolve("alex@quadex.ai", "Quadex AI", person))

    assert evidence_state(resolution, "person_state") == "verified"
    assert evidence_state(resolution, "company_state") == "verified"
    assert resolution.company_source == "email_domain"
    assert resolution.domain == "quadex.ai"
    assert resolution.affiliation_state == "inferred"
    assert resolution.confidence == "medium"
    assert fake_exa.company_queries == []


def test_unverified_for_personal_email_apollo_miss_self_reported_company(tmp_path) -> None:
    fake_exa = FakeExa()
    person = ApolloPersonResult(
        matched=False,
        email="lucas@gmail.com",
        first_name="Lucas",
        last_name="Chen",
    )

    resolution = run(resolver(tmp_path, fake_exa).resolve("lucas@gmail.com", "Wobble", person))

    assert evidence_state(resolution, "person_state") == "self_reported"
    assert evidence_state(resolution, "company_state") == "inferred"
    assert resolution.company_source == "exa_company_search"
    assert resolution.domain == "wobblegenomics.com"
    assert resolution.affiliation_state == "unverified"
    assert resolution.confidence == "low"
    assert fake_exa.company_queries == ["Wobble"]


def test_company_search_rejects_uncorroborated_spelling_correction(tmp_path) -> None:
    fake_exa = FakeExa()
    person = ApolloPersonResult(
        matched=False,
        email="jenny@gmail.com",
        first_name="Jenny",
        last_name="Xie",
    )

    resolution = run(resolver(tmp_path, fake_exa).resolve("jenny@gmail.com", "Skalpel AI", person))

    assert evidence_state(resolution, "company_state") == "unresolved"
    assert resolution.company_source == "none"
    assert resolution.domain is None
    assert resolution.confidence == "low"
    assert fake_exa.company_queries == ["Skalpel AI"]


def test_company_search_rejects_different_domain_when_raw_company_is_domain_like(tmp_path) -> None:
    fake_exa = FakeExa()
    person = ApolloPersonResult(
        matched=False,
        email="john@gmail.com",
        first_name="John",
        last_name="Meissner",
    )

    resolution = run(resolver(tmp_path, fake_exa).resolve("john@gmail.com", "Contor.ai", person))

    assert evidence_state(resolution, "company_state") == "unresolved"
    assert resolution.company_source == "none"
    assert resolution.domain is None
    assert resolution.confidence == "low"
    assert fake_exa.company_queries == ["Contor.ai"]


def test_academic_email_domain_does_not_contradict_self_reported_company(tmp_path) -> None:
    fake_exa = FakeExa()
    person = ApolloPersonResult(
        matched=False,
        email="chinmaypatil@berkeley.edu",
        first_name="Chinmay",
        last_name="Patil",
    )

    resolution = run(
        resolver(tmp_path, fake_exa).resolve(
            "chinmaypatil@berkeley.edu",
            "Caprae Capital Partners",
            person,
        )
    )

    assert evidence_state(resolution, "company_state") == "inferred"
    assert resolution.company_source == "exa_company_search"
    assert resolution.domain == "capraecapital.com"
    assert resolution.affiliation_state == "unverified"
    assert resolution.confidence == "low"
    assert resolution.candidate_companies == []


def test_people_search_can_confirm_affiliation_after_company_search(tmp_path) -> None:
    fake_exa = FakeExa()
    person = ApolloPersonResult(
        matched=False,
        email="varun@gmail.com",
        first_name="Varun",
        last_name="Simha",
    )

    resolution = run(resolver(tmp_path, fake_exa).resolve("varun@gmail.com", "Sift Science Inc", person))

    assert evidence_state(resolution, "person_state") == "inferred"
    assert resolution.person_source == "exa_people"
    assert evidence_state(resolution, "company_state") == "inferred"
    assert resolution.company_source == "exa_company_search"
    assert resolution.domain == "sift.com"
    assert resolution.affiliation_state == "inferred"
    assert resolution.confidence == "medium"
    assert fake_exa.people_queries == [("Varun Simha", "Sift Science Inc")]


def test_exact_linkedin_profile_follow_up_can_confirm_affiliation(tmp_path) -> None:
    fake_exa = FakeExa()
    person = ApolloPersonResult(
        matched=False,
        email="andrew@gmail.com",
        first_name="Andrew",
        last_name="Corcoran",
    )

    resolution = run(resolver(tmp_path, fake_exa).resolve("andrew@gmail.com", "LangChain", person))

    assert evidence_state(resolution, "person_state") == "inferred"
    assert resolution.person_source == "exa_people"
    assert evidence_state(resolution, "company_state") == "inferred"
    assert resolution.company_source == "exa_company_search"
    assert resolution.domain == "langchain.com"
    assert resolution.affiliation_state == "inferred"
    assert resolution.confidence == "medium"
    assert fake_exa.people_queries == [("Andrew Corcoran", "LangChain")]
    assert fake_exa.profile_queries == [
        ("Andrew Corcoran", "https://linkedin.com/in/andrewkcorcoran", "LangChain")
    ]


def test_contradicted_when_apollo_employer_conflicts_with_self_reported_company(tmp_path) -> None:
    person = ApolloPersonResult(
        matched=True,
        email="alex@gmail.com",
        first_name="Alex",
        last_name="Rivera",
        organization_name="Acme",
        organization_domain="acme.com",
    )

    resolution = run(resolver(tmp_path).resolve("alex@gmail.com", "Wobble", person))

    assert resolution.affiliation_state == "contradicted"
    assert resolution.confidence == "flagged"
    assert resolution.domain is None
    assert {candidate.domain for candidate in resolution.candidate_companies} == {
        "acme.com",
        "wobblegenomics.com",
    }
    assert any("affiliation: contradicted" in line for line in evidence_chain(resolution))


def test_same_company_name_on_alternate_domain_does_not_flag(tmp_path) -> None:
    person = ApolloPersonResult(
        matched=True,
        email="founder@gmail.com",
        first_name="Jaden",
        last_name="Wang",
        organization_name="OpenRelay",
        organization_domain="openrelay.inc",
    )

    resolution = run(resolver(tmp_path).resolve("founder@gmail.com", "OpenRelay", person))

    assert resolution.domain == "openrelay.inc"
    assert resolution.affiliation_state == "verified"
    assert resolution.confidence == "high"


def test_similar_but_not_identical_company_name_stays_flagged(tmp_path) -> None:
    person = ApolloPersonResult(
        matched=True,
        email="founder@gmail.com",
        first_name="Rajat",
        last_name="Bansal",
        organization_name="Kipo AI",
        organization_domain="kipo.ai",
    )

    resolution = run(resolver(tmp_path).resolve("founder@gmail.com", "KIPO", person))

    assert resolution.affiliation_state == "contradicted"
    assert resolution.confidence == "flagged"


def test_human_confirmed_domain_alias_prevents_future_flag(tmp_path) -> None:
    blocklist = tmp_path / "garbage_company_terms.txt"
    blocklist.write_text("stealth\n", encoding="utf-8")
    person = ApolloPersonResult(
        matched=True,
        email="founder@legacy.example",
        first_name="Ada",
        last_name="Lovelace",
        organization_name="Current Company",
        organization_domain="current.example",
    )
    company_resolver = CompanyResolver(
        exa=FakeExa(),
        blocklist_path=blocklist,
        alias_store=FakeAliasStore(),
    )

    resolution = run(company_resolver.resolve("founder@legacy.example", None, person))

    assert resolution.domain == "current.example"
    assert resolution.affiliation_state == "verified"
    assert resolution.confidence == "high"


def test_name_owned_email_domain_does_not_contradict_verified_apollo_employer(tmp_path) -> None:
    person = ApolloPersonResult(
        matched=True,
        email="nico@nicolasrobayo.com",
        first_name="Nicolas",
        last_name="Robayo",
        title="Director of Analytics",
        organization_name="UnoGroup",
        organization_domain="unogroup.us",
    )

    resolution = run(
        resolver(tmp_path).resolve(
            "nico@nicolasrobayo.com",
            "UnoGroup",
            person,
        )
    )

    assert resolution.domain == "unogroup.us"
    assert resolution.affiliation_state in {"verified", "inferred"}
    assert resolution.confidence in {"high", "medium"}
    assert resolution.candidate_companies == []
    assert any("personal vanity domain" in line for line in evidence_chain(resolution))


def test_supplied_company_website_can_resolve_personal_email_without_apollo(tmp_path) -> None:
    person = ApolloPersonResult(
        matched=False,
        email="founder@gmail.com",
        first_name="Ada",
        last_name="Lovelace",
    )

    resolution = run(
        resolver(tmp_path).resolve(
            "founder@gmail.com",
            "Quadex AI",
            person,
            website_url="https://www.quadex.ai/about",
        )
    )

    assert resolution.domain == "quadex.ai"
    assert resolution.company_source == "self_reported_website"
    assert resolution.affiliation_state == "unverified"
    assert any("first-party company evidence" in line for line in evidence_chain(resolution))


def test_unresolved_for_garbage_company_without_strong_signals(tmp_path) -> None:
    fake_exa = FakeExa()
    person = ApolloPersonResult(
        matched=False,
        email="alex@gmail.com",
        first_name="Alex",
        last_name="Rivera",
    )

    resolution = run(resolver(tmp_path, fake_exa).resolve("alex@gmail.com", "stealth", person))

    assert evidence_state(resolution, "company_state") == "garbage"
    assert resolution.company_source == "none"
    assert resolution.affiliation_state == "unverified"
    assert resolution.confidence == "low"
    assert fake_exa.company_queries == []


def test_people_search_name_only_does_not_confirm_affiliation(tmp_path) -> None:
    fake_exa = FakeExa()
    person = ApolloPersonResult(
        matched=False,
        email="lucas@gmail.com",
        first_name="Lucas",
        last_name="Chen",
    )

    resolution = run(resolver(tmp_path, fake_exa).resolve("lucas@gmail.com", "No Match", person))

    assert evidence_state(resolution, "person_state") == "self_reported"
    assert evidence_state(resolution, "company_state") == "unresolved"
    assert resolution.affiliation_state == "unverified"
    assert resolution.confidence == "low"
    assert fake_exa.people_queries == [("Lucas Chen", "No Match")]


def test_people_search_requires_corroborating_fields_before_inference(tmp_path) -> None:
    fake_exa = FakeExa()
    person = ApolloPersonResult(
        matched=True,
        email="alex@gmail.com",
        first_name="Alex",
        last_name="Rivera",
        title="CTO",
    )

    resolution = run(resolver(tmp_path, fake_exa).resolve("alex@gmail.com", "Unknown AI", person))

    assert evidence_state(resolution, "person_state") == "verified"
    assert evidence_state(resolution, "company_state") == "inferred"
    assert resolution.company_source == "exa_people_search"
    assert resolution.domain == "unknown.ai"
    assert resolution.affiliation_state == "inferred"
    assert resolution.confidence == "medium"
    assert fake_exa.people_queries == [("Alex Rivera", "Unknown AI")]
