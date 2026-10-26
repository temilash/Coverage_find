"""Outreach: the recipient table and the composed email (no LLM, Zetaris or Meterless)."""

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

from coverage_detective import outreach as O
from coverage_detective.config import ROOT

RUN = json.loads((ROOT / "examples" / "compare" / "great_langdale_3.full.json").read_text())
RESULT = RUN["result"]
LETTER = {
    "subject": "Poor mobile signal at Great Langdale (LA22 9JY)",
    "body_markdown": (
        "# Mobile signal in Great Langdale\n\n"
        "I live at [Your address] and the signal is poor at my home [los_ee].\n\n"
        "**What the public data shows**\n\n"
        "- No Ookla tests in the cell, so speeds there are unknown [speed_cell][speed_neighbours]\n"
        "- Every operator's nearest site is blocked by the ground in between [los_o2][los_three]\n\n"
        "Please review 4G coverage around Great Langdale and tell me whether an upgrade is planned.\n\n"
        "---\n**Evidence and sources**\n\n"
        "- [los_ee] EE's nearest approximate site is 3.8 km away and the ground rises above the line to it "
        "(Copernicus DEM; data 2021, as of 2026-10-25)\n"
    ),
}
AT = datetime(2026, 10, 26, 21, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ the table


def test_council_rows_match_the_names_postcodes_io_returns():
    contact = O.council_contact("Westmorland and Furness")
    assert contact.name == "Westmorland and Furness Council"
    assert contact.email == "complaints@westmorlandandfurness.gov.uk"
    assert [r.kind for r in contact.routes] == ["email"]


def test_a_council_name_with_an_ampersand_or_council_suffix_finds_the_same_row():
    assert O.council_contact("Westmorland & Furness Council").email == "complaints@westmorlandandfurness.gov.uk"
    assert O.council_contact("Powys County Council").email == "complaints@powys.gov.uk"


def test_an_unlisted_council_gets_the_gov_uk_finder_never_a_guessed_address():
    contact = O.council_contact("Gwynedd")
    assert contact.email is None
    assert contact.routes[0].kind == "unverified"
    assert contact.routes[0].value == "https://www.gov.uk/find-local-council"


def test_every_row_has_a_route_and_a_publication_page_and_council_addresses_are_on_the_councils_domain():
    for path in (O.COUNCIL_FILE, O.OPERATOR_FILE):
        for row in O._rows(path):
            kind = row.get("name") or row.get("operator")
            assert row.get("email") or row.get("form_url") or row.get("post_address"), kind
            assert row["source_url"].startswith("https://"), kind
            datetime.strptime(row["verified"], "%Y-%m-%d")  # a real yyyy-mm-dd date, not ten characters
    for row in O._rows(O.COUNCIL_FILE):
        email_domain = row["email"].split("@")[1]
        site = urlparse(row["source_url"]).netloc.removeprefix("www.")
        assert site == email_domain or site.endswith(f".{email_domain}"), row["name"]


def test_the_page_gets_the_council_then_the_four_networks():
    names = [c.name for c in O.contacts_for("Powys")]
    assert names == ["Powys Council", "EE", "O2", "Three", "Vodafone"]


# ------------------------------------------------------------------ the message


def test_compose_fills_in_the_recipient_and_produces_a_sendable_message():
    email = O.compose(RESULT, LETTER, O.council_contact("Westmorland and Furness"), at=AT)
    assert email.to == "complaints@westmorlandandfurness.gov.uk"
    assert email.subject == "Poor mobile signal at Great Langdale (LA22 9JY)"
    assert email.channel == "email" and email.truncated is False
    assert "I live at LA22 9JY" in email.body_text                    # [Your address] filled from the run
    assert "[los_ee]" not in email.body_text                          # evidence ids stay in the app
    assert "EE's nearest approximate site is 3.8 km away" in email.body_text
    assert "Diagnosis reference run_20261026213702_fgoz" in email.body_text
    assert "26 October 2026" in email.body_text
    assert "not a complaint about the council's own service" in email.body_text
    assert "<p>I live at LA22 9JY" in email.body_html and "<li>No Ookla tests" in email.body_html


def test_compose_signs_the_message_with_the_residents_name_when_given():
    email = O.compose(RESULT, LETTER, O.council_contact("Powys"), sent_by="D. Abraham", at=AT)
    assert "Reported by D. Abraham of LA22 9JY" in email.body_text


def test_mailto_carries_the_subject_and_body_and_decodes_back_to_them():
    email = O.compose(RESULT, LETTER, O.council_contact("Powys"), at=AT)
    assert email.mailto.startswith("mailto:complaints@powys.gov.uk?subject=")
    query = parse_qs(urlparse(email.mailto).query)
    assert unquote(query["subject"][0]) == email.subject
    assert unquote(query["body"][0]) == email.body_text


def test_networks_without_a_published_email_get_their_form_and_post_address_instead():
    for network in ("Three", "Vodafone"):
        contact = O.contact_for("Westmorland and Furness", network)
        email = O.compose(RESULT, LETTER, contact, at=AT)
        assert email.to == "" and email.mailto == ""
        assert email.channel == "form"
        assert [r["kind"] for r in email.routes] == ["form", "post"]
        assert email.routes[0]["value"].startswith("https://www.")
        assert email.body_text.strip()                                     # the resident can still copy it


def test_ee_and_o2_have_a_published_complaints_address():
    for network, address in (("EE", "customer.complaints@ee.co.uk"), ("O2", "complaintreviewservice@o2.com")):
        email = O.compose(RESULT, LETTER, O.contact_for(None, network), at=AT)
        assert email.to == address and email.mailto.startswith(f"mailto:{address}")


def test_an_unknown_council_still_gets_the_text_and_the_finder_as_its_route():
    email = O.compose(RESULT, LETTER, O.council_contact("Gwynedd"), at=AT)
    assert email.channel == "unverified" and email.to == ""
    assert email.routes[0]["value"] == "https://www.gov.uk/find-local-council"
    assert "Great Langdale" in email.body_text
    assert email.filename == "coverage-email-la22-9jy-gwynedd.md"


def test_a_body_too_long_for_a_mailto_link_is_cut_at_a_line_and_keeps_the_signature():
    long_letter = {"subject": "Signal", "body_markdown": "Paragraph with detail.\n\n" * 400}
    email = O.compose(RESULT, long_letter, O.council_contact("Powys"), at=AT)
    body = unquote(email.mailto)
    assert email.truncated is True and len(email.mailto) <= O.MAX_MAILTO
    assert "Paragraph with detail." in body                      # not an empty draft
    assert "use Copy to send the full text" in body
    assert "Reported by a local resident of LA22 9JY" in body    # the signature is never cut
    assert "Diagnosis reference run_20261026213702_fgoz" in body
    assert email.body_text.count("Paragraph with detail.") == 400  # Copy stays complete


def test_a_single_line_too_long_for_a_mailto_link_is_still_carried():
    email = O.compose(RESULT, {"subject": "Signal", "body_markdown": "One very long line. " * 500},
                      O.council_contact("Powys"), at=AT)
    body = unquote(email.mailto)
    assert "One very long line." in body and "Reported by a local resident" in body


def test_a_route_must_be_https_and_a_row_without_one_is_not_offered():
    assert O._routes({"form_url": "javascript:alert(1)"}) == ()
    assert O._routes({"form_url": "http://example.com/x"}) == ()
    assert [r.kind for r in O._routes({"email": "a@b.gov.uk", "form_url": "https://example.com/x"})] == ["email", "form"]


def test_html_for_the_email_escapes_what_a_letter_might_contain():
    html = O.to_html('Hi <script>alert("x")</script> there.\n\n- a & b')
    assert "<script>" not in html and "&lt;script&gt;" in html and "a &amp; b" in html


def test_markdown_becomes_plain_text_and_html_without_losing_the_list():
    text = O.to_text("## Heading\n\nOne **bold** line.\n\n- first\n- second\n\n---\nA [link](https://example.com).")
    assert text == "Heading\n\nOne bold line.\n\n- first\n- second\n\n--\n\nA link (https://example.com)."
    html = O.to_html("## Heading\n\nOne **bold** line.\n\n- first\n- second")
    assert html == "<h3>Heading</h3>\n<p>One <strong>bold</strong> line.</p>\n<ul>\n<li>first</li>\n<li>second</li>\n</ul>"


def test_a_wrapped_paragraph_stays_one_paragraph():
    assert O.to_text("The signal is poor enough that\ncalls drop indoors.\n\nThe next point.") == (
        "The signal is poor enough that calls drop indoors.\n\nThe next point.")
