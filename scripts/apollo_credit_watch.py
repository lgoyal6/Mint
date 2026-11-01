#!/usr/bin/env python3
"""Read-only Apollo credit watch for mint.

Confirms whether Apollo spend has flatlined after the freshness-leak fix.
Reads `enrichment_logs` / `people` from the DB pointed at by DATABASE_URL.
SELECT-only: never writes.

Usage:
    set -a; . ./.env; set +a
    python scripts/apollo_credit_watch.py            # last 24h, hourly
    python scripts/apollo_credit_watch.py --hours 6  # last 6h

What to look for AFTER the fix is live:
- "apollo_person success" per hour should collapse from ~130/sync toward a trickle.
- "skipped_fresh" should dominate (the leak tail now short-circuits).
- re-enrichments/person (last 24h) should drop toward ~1.0x.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _async_url(url: str) -> str:
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return url.replace(prefix, "postgresql+asyncpg://", 1)
    return url


async def main(hours: int) -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set (source your .env first)")
    eng = create_async_engine(_async_url(url))
    window = f"now() - interval '{hours} hours'"
    try:
        async with eng.connect() as c:
            async def q(sql: str):
                return (await c.execute(text(sql))).fetchall()

            print(f"=== Apollo spend per hour, last {hours}h (UTC) ===")
            rows = await q(f"""
                select date_trunc('hour', created_at) hr,
                       count(*) filter (where source='apollo_person' and status='success') person,
                       count(*) filter (where source='apollo_org'    and status='success') org,
                       count(*) filter (where source='apollo_person' and status='failed')  person_fail,
                       count(*) filter (where source='skipped_fresh')                      skipped
                from enrichment_logs where created_at >= {window}
                group by hr order by hr""")
            print(f"  {'hour':16} {'person':>7} {'org':>6} {'fail':>6} {'skipped':>8}")
            for r in rows:
                print(f"  {str(r.hr)[:16]:16} {r.person:>7} {r.org:>6} {r.person_fail:>6} {r.skipped:>8}")

            print(f"\n=== totals, last {hours}h ===")
            r = (await q(f"""
                select
                  count(*) filter (where source='apollo_person' and status='success') person_ok,
                  count(*) filter (where source='apollo_org'    and status='success') org_ok,
                  count(*) filter (where source='apollo_person' and status='failed')  person_fail,
                  count(*) filter (where source='skipped_fresh')                      skipped,
                  count(distinct entity_id) filter (where source='apollo_person' and status='success') ppl
                from enrichment_logs where created_at >= {window}"""))[0]
            spend = r.person_ok + r.org_ok
            ratio = (r.person_ok / r.ppl) if r.ppl else 0.0
            print(f"  apollo credits spent (person+org) : {spend}")
            print(f"    person success / distinct people: {r.person_ok} / {r.ppl}  ({ratio:.1f}x re-enrichment)")
            print(f"    org success                     : {r.org_ok}")
            print(f"    person FAILED (errors/rate/credit): {r.person_fail}")
            print(f"  skipped_fresh (free, short-circuit): {r.skipped}")
            if spend + r.skipped:
                pct = 100 * r.skipped / (spend + r.skipped)
                print(f"  short-circuit rate                : {pct:.1f}%  (higher = healthier)")

            print("\n=== leak population snapshot (people table) ===")
            r = (await q("""
                select count(*) total,
                  count(*) filter (where apollo_enriched_at is not null and exa_enriched_at is null) apollo_no_exa
                from people"""))[0]
            print(f"  apollo-set but exa NULL: {r.apollo_no_exa} / {r.total} people")
            print("  (these were re-charged every sync pre-fix; should now be skipped_fresh)")
    finally:
        await eng.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=int, default=24, help="lookback window in hours (default 24)")
    asyncio.run(main(ap.parse_args().hours))
