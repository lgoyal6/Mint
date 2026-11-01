#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   MINT_SESSION_COOKIE='mint_session=...' ./apply_mint_domain_fixes.sh person_ids.csv
#
# person_ids.csv must have headers: email,person_id
# Get it from Mint/raw query, then run this from /Users/lakshgoyal/mint.

PERSON_IDS_CSV="${1:-person_ids.csv}"
API_BASE="${API_BASE:-https://mint-hoiv.onrender.com}"
COOKIE="${MINT_SESSION_COOKIE:-}"
if [[ -z "$COOKIE" ]]; then
  echo "Set MINT_SESSION_COOKIE first, e.g. export MINT_SESSION_COOKIE='mint_session=...'" >&2
  exit 1
fi
if [[ ! -f "$PERSON_IDS_CSV" ]]; then
  echo "Missing $PERSON_IDS_CSV with headers: email,person_id" >&2
  exit 1
fi

python3 - "$PERSON_IDS_CSV" <<'PY' | while IFS=$'\t' read -r person_id company domain email; do
import csv, sys
ids={}
with open(sys.argv[1], newline='', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        ids[(r.get('email') or r.get('Email') or '').strip().lower()] = (r.get('person_id') or r.get('Person ID') or '').strip()
with open('/Users/lakshgoyal/mint/mint-domain-fixes-apply-ready.csv', newline='', encoding='utf-8') as f:
    for r in csv.DictReader(f):
        email=r['Email'].strip().lower()
        pid=ids.get(email,'')
        if not pid:
            print(f"#MISSING\t{r['Company']}\t{r['Correct Domain']}\t{email}")
            continue
        print(f"{pid}\t{r['Company']}\t{r['Correct Domain']}\t{email}")
PY
  if [[ "$person_id" == "#MISSING" ]]; then
    echo "Skipping missing person_id for $email ($company -> $domain)" >&2
    continue
  fi
  echo "Applying $email -> $company / $domain"
  curl -fsS -X POST "$API_BASE/review/$person_id/supply-context" \
    -H 'content-type: application/json' \
    -H "cookie: $COOKIE" \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"company_name":sys.argv[1],"company_domain":sys.argv[2]}))' "$company" "$domain")" >/dev/null
done
