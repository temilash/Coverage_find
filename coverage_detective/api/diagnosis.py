"""Diagnosis endpoints: the rule-based diagnosis for a point, and the email that sends it.

    POST /api/diagnose {query | lat, lon}                 -> headline, scored causes, evidence, limitations
    GET  /api/outreach/recipients?district=               -> the council and the four networks, with their routes
    POST /api/email {query | lat, lon, to, sent_by}       -> the email for that recipient (subject, text, html, mailto)

Everything is computed in code from the evidence bundle (evidence.py, diagnosis.py); nothing calls a model.
In sample mode a point must lie inside the committed extracts (data/db.py: sample_covers).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from coverage_detective import diagnosis as D
from coverage_detective import evidence as ev
from coverage_detective import outreach as O
from coverage_detective.config import UK_BBOX, sample_mode
from coverage_detective.data import db

router = APIRouter(prefix="/api", tags=["diagnosis"])
SAMPLE_SEARCH_HINT = (" (sample mode: search works for the example places only, e.g. CO9 1TP, Great Langdale, WC2N 5DU, "
                      "Rhayader, Sutton St Edmund)")


class PointRequest(BaseModel):
    query: str | None = Field(None, min_length=2, max_length=100, description="UK postcode or place name")
    lat: float | None = Field(None, ge=UK_BBOX["min_lat"], le=UK_BBOX["max_lat"])
    lon: float | None = Field(None, ge=UK_BBOX["min_lon"], le=UK_BBOX["max_lon"])

    @model_validator(mode="after")
    def one_location(self):
        if not self.query and (self.lat is None or self.lon is None):
            raise ValueError('give "query", or both "lat" and "lon"')
        return self


class EmailRequest(PointRequest):
    to: str = Field("council", min_length=2, max_length=80, description='"council", EE, O2, Three or Vodafone')
    sent_by: str = Field("", max_length=120, description="the resident's name for the signature (optional)")
    subject: str | None = Field(None, max_length=200, description="an edited subject to send instead of the template's")
    body_markdown: str | None = Field(None, max_length=20_000, description="an edited letter to send instead of the template")


def bundle_for(req: PointRequest) -> dict:
    """The evidence bundle with its diagnosis attached; 404 for an unknown place or a point outside the samples."""
    if sample_mode() and req.lat is not None and not db.sample_covers(req.lat, req.lon):
        raise HTTPException(404, db.sample_coverage_message())
    try:
        bundle = ev.build_bundle(req.query, req.lat, req.lon)
    except LookupError as e:
        raise HTTPException(404, str(e) + (SAMPLE_SEARCH_HINT if sample_mode() else "")) from e
    bundle["diagnosis"] = D.diagnose(bundle)
    return bundle


def reference(bundle: dict) -> str:
    """A reference a recipient can quote: the H3 cell and the date the evidence was gathered."""
    return f"{bundle['cell_id']} {str(bundle.get('generated_at') or '')[:10]}".strip()


@router.post("/diagnose")
def diagnose(req: PointRequest) -> dict:
    """Why the signal is likely poor at a point: scored causes with the evidence each one cites."""
    b = bundle_for(req)
    d = b["diagnosis"]
    return {"reference": reference(b), "location": b["point"], "cell_id": b["cell_id"], "sample_mode": b["sample_mode"],
            **d, "evidence": b["items"], "queries": b["queries"], "errors": b["errors"]}


@router.get("/outreach/recipients")
def outreach_recipients(district: str | None = Query(None, max_length=120,
                                                     description="local authority name, as postcodes.io returns it")) -> dict:
    """Who a diagnosis can be sent to, and how each of them can be reached."""
    return O.recipients(district)


@router.post("/email")
def email(req: EmailRequest) -> dict:
    """The email for one recipient: the template letter (or the edited text sent back), the address, the
    signature and the mailto link."""
    b = bundle_for(req)
    district = b["point"].get("admin_district")
    try:
        contact = O.contact_for(district, req.to)
    except LookupError as e:
        raise HTTPException(422, str(e)) from e
    drafted = O.letter(b, b["diagnosis"], contact)
    if req.body_markdown:
        drafted = {"subject": req.subject or drafted["subject"], "body_markdown": req.body_markdown}
    result = {"location": b["point"], "reference": reference(b)}
    out = O.compose(result, drafted, contact, sent_by=req.sent_by)
    return {**out.to_dict(), "contact": contact.to_dict(), "district": district, "reference": result["reference"],
            "markdown": drafted["body_markdown"]}
