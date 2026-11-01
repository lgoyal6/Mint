from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from mint.backend.db.tables import CompanyDomainAlias
from mint.backend.utils.domain import clean_domain


class CompanyAliasStore:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def canonical_domain(self, value: str | None) -> str | None:
        domain = clean_domain(value)
        if not domain:
            return None
        async with self.session_factory() as session:
            canonical = await session.scalar(
                select(CompanyDomainAlias.canonical_domain).where(CompanyDomainAlias.domain == domain)
            )
        return clean_domain(canonical) or domain

    async def remember(self, canonical: str, aliases: list[str]) -> int:
        canonical_domain = clean_domain(canonical)
        domains = {clean_domain(value) for value in aliases}
        domains.discard(None)
        if not canonical_domain:
            return 0
        domains.add(canonical_domain)
        rows = [
            {"domain": domain, "canonical_domain": canonical_domain, "source": "human_review"}
            for domain in sorted(domains)
        ]
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    insert(CompanyDomainAlias)
                    .values(rows)
                    .on_conflict_do_update(
                        index_elements=[CompanyDomainAlias.domain],
                        set_={"canonical_domain": canonical_domain, "source": "human_review"},
                    )
                )
        return len(rows)
