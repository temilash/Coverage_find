"""Outreach check: the recipient table, and the email composed from a sample diagnosis.

    python scripts/check_outreach.py                          # table + the email for Great Langdale (offline)
    python scripts/check_outreach.py --lat 52.30 --lon -3.51 --to all

Runs offline in sample mode: the evidence bundle and diagnosis come from the committed extracts, and the
letter is the fixed template (outreach.letter). Use it after editing data/reference/*_contacts.csv and
when a council's address changes. The point must lie inside the sample extracts.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SAMPLE_MODE", "1")  # before the package reads the environment

from coverage_detective import diagnosis as D  # noqa: E402
from coverage_detective import evidence as ev  # noqa: E402
from coverage_detective import outreach as O  # noqa: E402
from coverage_detective.config import ROOT  # noqa: E402


def check_table() -> int:
    """Every row is usable, and every council address is on the council's own site's domain."""
    problems = 0
    for path in (O.COUNCIL_FILE, O.OPERATOR_FILE):
        rows = O._rows(path)
        print(f"\n{path.relative_to(ROOT)}: {len(rows)} rows")
        for row in rows:
            who = row.get("name") or row.get("operator") or "(no name)"
            if not (row.get("email") or row.get("form_url") or row.get("post_address")):
                print(f"  FAIL {who}: no email, form or post address")
                problems += 1
            if not (row.get("source_url") or "").startswith("https://"):
                print(f"  FAIL {who}: no https source_url")
                problems += 1
            if len(row.get("verified") or "") != 10:
                print(f"  FAIL {who}: verified is not a yyyy-mm-dd date")
                problems += 1
            if path == O.COUNCIL_FILE and row.get("email"):
                domain = row["email"].split("@")[-1]
                site = urlparse(row["source_url"]).netloc.removeprefix("www.")
                if not (site == domain or site.endswith(f".{domain}")):
                    print(f"  FAIL {who}: {row['email']} is not on {site}")
                    problems += 1
            channel = "email" if row.get("email") else "form" if row.get("form_url") else "post"
            print(f"  ok   {who:<28} {channel:<6} {row.get('email') or row.get('form_url') or row.get('post_address')}")
    return problems


def check_email(bundle: dict, diagnosis: dict, to: str, sent_by: str) -> int:
    point = bundle["point"]
    district = point.get("admin_district")
    contact = O.contact_for(district, to)
    result = {"location": point, "reference": f"{bundle['cell_id']} {bundle['generated_at'][:10]}"}
    place = O.place_of(result)
    print(f"\n{place['postcode'] or place['label']} ({district}) -> {contact.name}")
    print(f"  routes: {', '.join(f'{r.kind}: {r.value}' for r in contact.routes)}")
    email = O.compose(result, O.letter(bundle, diagnosis, contact), contact, sent_by=sent_by,
                      at=datetime.now(timezone.utc))
    print(f"  to: {email.to or '(none published)'}   channel: {email.channel}   "
          f"mailto: {len(email.mailto)} chars{' (cut at a line: Copy carries the rest)' if email.truncated else ''}")
    if email.mailto:
        carried = unquote(email.mailto)
        missing = [part for part in ("Reported by", "Diagnosis reference") if part not in carried]
        if missing:
            print(f"  FAIL the mail link drops {', '.join(missing)}: the signature must always survive the cut")
            return 1
    print(f"  subject: {email.subject}")
    print("  body:")
    for line in email.body_text.splitlines():
        print(f"    {line}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lat", type=float, default=54.445, help="a point inside the sample extracts")
    parser.add_argument("--lon", type=float, default=-3.105)
    parser.add_argument("--to", default="council", help="council, EE, O2, Three, Vodafone or all")
    parser.add_argument("--sent-by", default="", help="the resident's name for the signature")
    args = parser.parse_args()

    problems = check_table()
    bundle = ev.build_bundle(lat=args.lat, lon=args.lon)
    diagnosis = D.diagnose(bundle)
    for to in ("council", "EE", "O2", "Three", "Vodafone") if args.to == "all" else (args.to,):
        try:
            problems += check_email(bundle, diagnosis, to, args.sent_by)
        except LookupError as e:
            print(f"\nFAIL {to}: {e}")
            problems += 1
    print(f"\n{'FAIL' if problems else 'OK'}: {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
