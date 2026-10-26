"""Outreach check: the recipient table, and the email composed from a saved run.

    python scripts/check_outreach.py                 # table + a real saved run (Great Langdale)
    python scripts/check_outreach.py --run <full.json> --to EE

Needs no Zetaris, no LLM and no Meterless: it reads examples/*/*.full.json (or a run export you
pass) and composes from a letter written the way the letter writer writes one. Use it after
editing data/reference/*_contacts.csv, before a demo, and when a council's address changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coverage_detective import outreach as O  # noqa: E402
from coverage_detective.config import ROOT  # noqa: E402

DEFAULT_RUN = ROOT / "examples" / "compare" / "great_langdale_3.full.json"


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


def check_email(result: dict, evidence: list[dict], to: str, sent_by: str) -> int:
    district = (result.get("location") or {}).get("admin_district") or (result.get("zone") or {}).get("council")
    contact = O.contact_for(district, to)
    place = O.place_of(result)
    print(f"\nRun {result.get('run_id')}  ({place['postcode'] or place['label']}, {district}) -> {contact.name}")
    print(f"  routes: {', '.join(f'{r.kind}: {r.value}' for r in contact.routes)}")
    letter = {
        "subject": f"Poor mobile signal at {place['postcode'] or place['label']}",
        "body_markdown": (f"# Mobile signal at {place['postcode'] or place['label']}\n\n"
                          "I live at [Your address] and the signal at my home is poor "
                          f"{''.join(f'[{i['evidence_id']}]' for i in evidence[:2])}.\n\n"
                          "**What the public data shows**\n\n- See the evidence listed below\n\n"
                          "Please review 4G coverage here and tell me whether an upgrade is planned.\n\n"
                          "---\n**Evidence and sources**\n\n" + _annex(evidence)),
    }
    email = O.compose(result, letter, contact, sent_by=sent_by, at=datetime.now(timezone.utc))
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


def _evidence(export: Path) -> list[dict]:
    """The evidence items of the run export (examples/*/great_langdale_3.full.json)."""
    if not export.exists():
        return []
    return json.loads(export.read_text()).get("evidence") or []


def _annex(evidence: list[dict]) -> str:
    lines = []
    for item in evidence:
        lines.append(f"- [{item['evidence_id']}] {item.get('finding')} ({item.get('source')}; "
                     f"data {item.get('data_period')}, as of {str(item.get('as_of') or '')[:10]})")
    return "\n".join(lines) or "- (no evidence export next to this run)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN, help="a run export or its .json")
    parser.add_argument("--to", default="council", help="council, EE, O2, Three or Vodafone")
    parser.add_argument("--sent-by", default="", help="the resident's name for the signature")
    args = parser.parse_args()

    problems = check_table()
    result = json.loads(args.run.read_text()).get("result")
    evidence = _evidence(args.run.with_name(args.run.name.replace(".json", ".full.json"))) if not \
        args.run.name.endswith(".full.json") else _evidence(args.run)
    if result:
        for to in ("council", "EE", "O2", "Three", "Vodafone") if args.to == "all" else (args.to,):
            try:
                problems += check_email(result, evidence, to, args.sent_by)
            except LookupError as e:
                print(f"\nFAIL {to}: {e}")
                problems += 1
    else:
        print(f"\nFAIL {args.run}: no result in this file")
        problems += 1
    print(f"\n{'FAIL' if problems else 'OK'}: {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
