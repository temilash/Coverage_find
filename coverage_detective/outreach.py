"""Who to tell about a diagnosis, and the email that tells them.

A finished run ends with an evidence letter (`agents.write_letter`). This module turns that
letter into something a resident can send: the recipient (their council, taken from the run's
local authority, or one of the four networks), the ways to reach that recipient, and the
message itself (subject, plain text, HTML, a mailto link).

Recipients are a table, not conditionals: `data/reference/operator_contacts.csv` and
`council_contacts.csv`, each row carrying the page the address was published on and the date
it was last checked. `scripts/check_outreach.py` re-checks the table. A council that is not in
the table gets gov.uk's council finder as its route, never a guessed address.

The wording rules in CLAUDE.md apply to everything here: no claim that the data does not
support, and the operating company is named as the network (VodafoneThree trades as Vodafone
and Three), so a letter says "Vodafone" and lets the network explain its own structure.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from coverage_detective.config import ROOT

OPERATORS = ("EE", "O2", "Three", "Vodafone")
COUNCIL_FINDER = "https://www.gov.uk/find-local-council"
REFERENCE_DIR = ROOT / "data" / "reference"
OPERATOR_FILE = REFERENCE_DIR / "operator_contacts.csv"
COUNCIL_FILE = REFERENCE_DIR / "council_contacts.csv"
MAX_MAILTO = 1800           # characters; Windows mail clients drop a longer mailto, so the body is cut

RouteKind = Literal["email", "form", "post", "unverified"]
ContactKind = Literal["council", "operator"]


# ------------------------------------------------------------------ the shape


@dataclass(frozen=True)
class Route:
    """One way to reach a recipient. `source_url` is where the address was published."""

    kind: RouteKind
    value: str
    label: str
    source_url: str | None = None
    verified: str | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value, "label": self.label,
                "source_url": self.source_url, "verified": self.verified}


@dataclass(frozen=True)
class Contact:
    """A recipient: a council or a network, with at least one route."""

    kind: ContactKind
    key: str
    name: str
    routes: tuple[Route, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.routes:
            raise ValueError(f"{self.name} has no route: every contact needs email, form, post or the finder")

    @property
    def email_route(self) -> Route | None:
        return next((r for r in self.routes if r.kind == "email"), None)

    @property
    def email(self) -> str | None:
        route = self.email_route
        return route.value if route else None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "key": self.key, "name": self.name, "email": self.email,
                "routes": [r.to_dict() for r in self.routes]}


@dataclass(frozen=True)
class Email:
    """A composed message: what to send, to whom, and how."""

    to: str
    subject: str
    body_text: str
    body_html: str
    mailto: str
    filename: str
    channel: RouteKind
    routes: list[dict]
    truncated: bool = False

    def to_dict(self) -> dict:
        return {"to": self.to, "subject": self.subject, "body_text": self.body_text, "body_html": self.body_html,
                "mailto": self.mailto, "filename": self.filename, "channel": self.channel, "routes": self.routes,
                "truncated": self.truncated}


# ------------------------------------------------------------------ the table


def normalise(name: str) -> str:
    """'Westmorland & Furness Council' and 'Westmorland and Furness' are the same council."""
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for prefix in ("the ", "city of ", "london borough of ", "royal borough of "):
        if s.startswith(prefix):
            s = s[len(prefix):]
    for suffix in (" county council", " city council", " borough council", " district council", " council",
                   " unitary authority", " county borough council"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s.strip()


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(f)]


def _https(value: str) -> str | None:
    """Only https URLs from the table are offered as routes: a link is clickable, so a bad row must not be."""
    value = (value or "").strip()
    return value if value.startswith("https://") else None


def _routes(row: dict) -> tuple[Route, ...]:
    source = _https(row.get("source_url") or "")
    verified = (row.get("verified") or "").strip() or None
    out = []
    if (row.get("email") or "").strip():
        out.append(Route("email", row["email"].strip(), "complaints email", source, verified))
    form = _https(row.get("form_url") or "")
    if form:
        out.append(Route("form", form, "complaints form", source, verified))
    if (row.get("post_address") or "").strip():
        out.append(Route("post", row["post_address"].strip(), "postal address", source, verified))
    return tuple(out)


@lru_cache(maxsize=1)
def catalog() -> tuple[dict[str, Contact], dict[str, Contact]]:
    """(councils by normalised name, operators by name). Read once per process."""
    councils = {}
    for row in _rows(COUNCIL_FILE):
        name = row.get("name", "")
        if name and _routes(row):
            councils[normalise(name)] = Contact("council", normalise(name), _council_name(name), _routes(row))
    operators = {}
    for row in _rows(OPERATOR_FILE):
        name = (row.get("operator") or row.get("name") or "").strip()
        if name and _routes(row):
            operators[name] = Contact("operator", name, name, _routes(row))
    return councils, operators


def _council_name(district: str) -> str:
    return district if "council" in district.lower() else f"{district} Council"


def council_contact(district: str | None) -> Contact:
    """The council for a local authority name, or the finder route when we have no row."""
    councils, _ = catalog()
    if district:
        found = councils.get(normalise(district))
        if found:
            return found
        return Contact("council", normalise(district), _council_name(district),
                       (Route("unverified", COUNCIL_FINDER, "Find your council on gov.uk"),))
    return Contact("council", "", "Your council", (Route("unverified", COUNCIL_FINDER, "Find your council on gov.uk"),))


def operator_contact(name: str) -> Contact | None:
    _, operators = catalog()
    for key, contact in operators.items():
        if key.lower() == (name or "").strip().lower():
            return contact
    return None


def contacts_for(district: str | None) -> list[Contact]:
    """The council first (it can act on a local gap), then the four networks."""
    _, operators = catalog()
    return [council_contact(district), *[operators[o] for o in OPERATORS if o in operators]]


def contact_for(district: str | None, to: str) -> Contact:
    """Resolve the recipient the resident picked: "council" or a network name."""
    if (to or "").strip().lower() in ("council", "the council", "local council"):
        return council_contact(district)
    contact = operator_contact(to)
    if contact is None:
        raise LookupError(f"unknown recipient {to!r}: use council or one of {', '.join(OPERATORS)}")
    return contact


# ------------------------------------------------------------------ the letter


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\((https?:[^)\s]+)\)")
_RULE = re.compile(r"^-{3,}$")
_ANNEX_ID = re.compile(r"^- \[([a-z0-9_]+)\] ", re.MULTILINE)


def cited_ids(letter: dict) -> set[str]:
    """Every bracketed evidence id in the letter: those it lists, those in its annex, and those in
    its prose. A letter supplied by the client has not been through the guard, so its bracketed
    tokens are stripped the same way (lowercase ids never collide with [Your name])."""
    body = letter.get("body_markdown") or ""
    ids = {str(i) for i in letter.get("cited_evidence") or []}
    ids |= set(_ANNEX_ID.findall(body))
    ids |= set(re.findall(r"\[([a-z0-9_]+)\]", body))
    return ids


def clean_markdown(md: str, ids: set[str] | None = None) -> str:
    """Drop the given evidence ids from prose (the annex lists every item with its source and date).

    Only known ids are removed: bracketed words such as a Markdown link label stay.
    """
    text = md or ""
    for eid in sorted(ids or (), key=len, reverse=True):
        text = text.replace(f"[{eid}]", "")
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([.,;:!?])", r"\1", text)
    return text


def blocks(md: str, ids: set[str] | None = None) -> list[tuple[str, str]]:
    """(kind, text) blocks: h, p, li, hr. Enough for the letters and summaries we produce.

    Consecutive plain lines are one paragraph (Markdown's lazy continuation), so a wrapped
    sentence does not arrive as two paragraphs.
    """
    out: list[tuple[str, str]] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append(("p", " ".join(line.strip() for line in para)))
            para.clear()

    for raw in clean_markdown(md, ids).splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if _RULE.match(line.strip()):
            flush()
            out.append(("hr", ""))
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)", line)
        if heading:
            flush()
            out.append(("h", heading.group(2).strip()))
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*)", line)
        if bullet:
            flush()
            out.append(("li", bullet.group(1).strip()))
            continue
        para.append(line)
    flush()
    return out


def _plain(s: str) -> str:
    s = _BOLD.sub(r"\1", s)
    s = _ITALIC.sub(r"\1", s)
    return _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", s).strip()


def _inline_html(s: str) -> str:
    from html import escape

    s = escape(s)
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    s = _ITALIC.sub(r"<em>\1</em>", s)
    return _LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)


def to_text(md: str, ids: set[str] | None = None) -> str:
    """Plain text: paragraphs separated by a blank line, bullets grouped, rule as a plain line."""
    out: list[str] = []
    bullets: list[str] = []

    def flush() -> None:
        if bullets:
            out.append("\n".join(bullets))
            bullets.clear()

    for kind, text in blocks(md, ids):
        if kind == "li":
            bullets.append(f"- {_plain(text)}")
            continue
        flush()
        if kind == "hr":
            out.append("--")
        else:
            out.append(_plain(text))
    flush()
    return "\n\n".join(out).strip()


def to_html(md: str, ids: set[str] | None = None) -> str:
    out: list[str] = []
    in_list = False
    for kind, text in blocks(md, ids):
        if kind == "li":
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_html(text)}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if kind == "h":
            out.append(f"<h3>{_inline_html(text)}</h3>")
        elif kind == "hr":
            out.append("<hr>")
        else:
            out.append(f"<p>{_inline_html(text)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def fill_placeholders(md: str, sent_by: str, where: str) -> str:
    text = md.replace("[Your name]", sent_by or "A local resident")
    for placeholder in ("[Your address]", "[Your postcode]"):
        text = text.replace(placeholder, where)
    return text


# ------------------------------------------------------------------ the message


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "location"


def place_of(result: dict) -> dict:
    """Where the run is about: postcode, coordinates, council."""
    location = result.get("location") or {}
    zone = result.get("zone") or {}
    return {"postcode": location.get("postcode") or location.get("nearest_postcode") or "",
            "label": location.get("label") or zone.get("name") or "",
            "lat": location.get("lat"), "lon": location.get("lon"),
            "district": location.get("admin_district") or zone.get("council") or ""}


def signature(result: dict, contact: Contact, sent_by: str, at: datetime) -> str:
    """Who is reporting, where, and which run: the recipient needs these to act on it."""
    place = place_of(result)
    bits = [f"Reported by {sent_by.strip() or 'a local resident'}"
            + (f" of {place['postcode']}" if place["postcode"] else "")
            + (f" ({place['lat']:.5f}, {place['lon']:.5f})" if isinstance(place["lat"], (int, float)) else "")]
    bits.append(f"Diagnosis reference {result.get('run_id') or 'unknown'}, prepared {at:%d %B %Y} with Coverage "
                "Detective from public data; each figure below names its source and the data's date.")
    if contact.kind == "council":
        bits.append("This is a report about the area's coverage, not a complaint about the council's own service.")
    return "\n\n".join(bits)


def _head_to_fit(text: str, room: int) -> str:
    """The longest prefix of text whose quoted length fits in room (a body with no line breaks)."""
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if len(quote(text[:mid], safe="")) <= room:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip()


def mailto_uri(to: str, subject: str, body: str, tail: str = "", max_chars: int = MAX_MAILTO) -> tuple[str, bool]:
    """A mailto link. `tail` (the signature) is never cut: when the body is too long, the body is cut
    at a line boundary, or mid-line if it has none, and a marker says where the rest is."""
    if not to:
        return "", False
    head = f"mailto:{to}?subject={quote(subject, safe='')}&body="
    tail_q = quote(f"\n\n{tail}", safe="") if tail else ""
    room = max_chars - len(head) - len(tail_q)
    if len(quote(body, safe="")) <= room:
        return head + quote(body, safe="") + tail_q, False
    marker = "\n\n[The rest of this message is in the app: use Copy to send the full text.]"
    room -= len(quote(marker, safe=""))
    kept: list[str] = []
    if room > 0:
        for line in body.splitlines():
            candidate = "\n".join([*kept, line])
            if len(quote(candidate, safe="")) > room:
                break
            kept.append(line)
    cut = "\n".join(kept) if kept else (_head_to_fit(body, room) if room > 0 else "")
    return head + quote(cut + marker, safe="") + tail_q, True


def compose(result: dict, letter: dict, contact: Contact, sent_by: str = "",
            at: datetime | None = None) -> Email:
    """The email for one diagnosis, one recipient. No model call: the letter is already checked."""
    at = at or datetime.now(timezone.utc)
    place = place_of(result)
    where = place["postcode"] or place["label"] or "the area"
    ids = cited_ids(letter)
    letter_md = fill_placeholders(letter.get("body_markdown") or "", sent_by, where).rstrip()
    sign_md = signature(result, contact, sent_by, at)
    letter_text, sign_text = to_text(letter_md, ids), to_text(sign_md, ids)
    subject = (letter.get("subject") or "").strip() or f"Poor mobile signal at {where}: evidence from public data"
    to = contact.email or ""
    mailto, truncated = mailto_uri(to, subject, letter_text, tail=sign_text)
    return Email(to=to, subject=subject, body_text=f"{letter_text}\n\n{sign_text}".strip(),
                 body_html=to_html(f"{letter_md}\n\n{sign_md}", ids), mailto=mailto,
                 filename=f"coverage-email-{slug(where)}-{slug(contact.key or contact.name)}.md",
                 channel=contact.routes[0].kind, routes=[r.to_dict() for r in contact.routes], truncated=truncated)


def recipients(district: str | None) -> dict:
    """The choices the page offers, with their addresses, before anything is drafted."""
    contacts = contacts_for(district)
    return {"district": district, "recipients": [c.to_dict() for c in contacts],
            "council_known": contacts[0].email is not None}
