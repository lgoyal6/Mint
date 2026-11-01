from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ProviderUsage:
    provider: str
    operation: str
    requests: int = 1
    credits: Decimal = Decimal("0")
    estimated_cost_usd: Decimal = Decimal("0")
    pricing_basis: str | None = None


_usage: ContextVar[list[ProviderUsage] | None] = ContextVar("mint_provider_usage", default=None)


def begin_usage() -> Token:
    return _usage.set([])


def current_usage() -> list[ProviderUsage]:
    return list(_usage.get() or [])


def end_usage(token: Token) -> None:
    _usage.reset(token)


def record_usage(
    provider: str,
    operation: str,
    *,
    requests: int = 1,
    credits: Decimal | int | str = 0,
    estimated_cost_usd: Decimal | int | str = 0,
    pricing_basis: str | None = None,
) -> None:
    events = _usage.get()
    if events is None:
        return
    events.append(
        ProviderUsage(
            provider=provider,
            operation=operation,
            requests=requests,
            credits=Decimal(str(credits)),
            estimated_cost_usd=Decimal(str(estimated_cost_usd)),
            pricing_basis=pricing_basis,
        )
    )
