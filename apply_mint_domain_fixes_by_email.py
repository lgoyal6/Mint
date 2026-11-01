#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


API_BASE = os.environ.get("API_BASE", "https://mint-hoiv.onrender.com").rstrip("/")
COOKIE = os.environ.get("MINT_SESSION_COOKIE", "")
FIXES_CSV = Path(sys.argv[1] if len(sys.argv) > 1 else "mint-domain-fixes-apply-ready.csv")


def request_json(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        method=method,
        headers={
            "content-type": "application/json",
            "cookie": COOKIE,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        payload = response.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def person_id_for_email(email: str) -> str | None:
    query = urllib.parse.urlencode({"search": email, "limit": 10})
    page = request_json("GET", f"/leads?{query}")
    for item in page.get("items", []):
        if str(item.get("email", "")).strip().lower() == email:
            return item.get("person_id")
    return None


def main() -> int:
    if not COOKIE:
        print("Set MINT_SESSION_COOKIE first, e.g. export MINT_SESSION_COOKIE='mint_session=...'", file=sys.stderr)
        return 1
    if not FIXES_CSV.exists():
        print(f"Missing fixes CSV: {FIXES_CSV}", file=sys.stderr)
        return 1

    applied = 0
    skipped = 0
    failed = 0
    with FIXES_CSV.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            email = (row.get("Email") or "").strip().lower()
            company = (row.get("Company") or "").strip()
            domain = (row.get("Correct Domain") or "").strip().lower()
            if not email or not company or not domain:
                skipped += 1
                print(f"SKIP malformed row: {row}")
                continue

            try:
                person_id = person_id_for_email(email)
                if not person_id:
                    skipped += 1
                    print(f"SKIP no lead found for {email}")
                    continue

                request_json(
                    "POST",
                    f"/review/{person_id}/supply-context",
                    {"company_name": company, "company_domain": domain},
                )
                applied += 1
                print(f"APPLIED {email} -> {company} / {domain}")
                time.sleep(0.2)
            except urllib.error.HTTPError as exc:
                failed += 1
                detail = exc.read().decode("utf-8", errors="replace")
                print(f"FAIL {email}: HTTP {exc.code} {detail}", file=sys.stderr)
            except Exception as exc:
                failed += 1
                print(f"FAIL {email}: {exc}", file=sys.stderr)

    print(f"Done. applied={applied} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
