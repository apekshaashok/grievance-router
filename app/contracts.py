"""Data contracts (D1, F1, F2). Published at GET /v1/contract and in docs/data_contract.md."""
import re
from datetime import date
from typing import Any, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
LONG_DIGITS_RE = re.compile(r"\b\d{10,}\b")


class ComplaintIn(BaseModel):
    """A synthetic investor complaint. Real personal identifiers are rejected at the boundary."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    investor_ref: str = Field(pattern=r"^INV[0-9]{6}$", description="Synthetic investor reference, INV + 6 digits")
    complaint_text: str = Field(min_length=20, max_length=4000)
    channel: Literal["web", "email", "phone", "letter"]
    complaint_date: date
    transaction_date: Optional[date] = None
    amount_inr: Optional[float] = Field(default=None, gt=0, le=100_000_000)
    entity_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    prior_complaint_to_entity: bool = False
    prior_complaint_date: Optional[date] = None
    pending_in_court: bool = False

    @field_validator("complaint_date")
    @classmethod
    def _not_future(cls, v):
        if v > date.today():
            raise ValueError("complaint_date must not be in the future")
        return v

    @field_validator("complaint_text", "entity_name")
    @classmethod
    def _no_real_identifiers(cls, v):
        if v is None:
            return v
        if PAN_RE.search(v) or LONG_DIGITS_RE.search(v):
            raise ValueError("must not contain PAN-, Aadhaar-, phone- or account-number-like values (synthetic data only)")
        return v

    @model_validator(mode="after")
    def _cross_field(self):
        if self.transaction_date and self.transaction_date > self.complaint_date:
            raise ValueError("cross-field: transaction_date must not be after complaint_date")
        if self.transaction_date and (self.complaint_date - self.transaction_date).days > 1095:
            raise ValueError("cross-field: transaction_date is more than 3 years before complaint_date")
        if self.prior_complaint_to_entity and self.prior_complaint_date is None:
            raise ValueError("cross-field: prior_complaint_date is required when prior_complaint_to_entity is true")
        if not self.prior_complaint_to_entity and self.prior_complaint_date is not None:
            raise ValueError("cross-field: prior_complaint_date given but prior_complaint_to_entity is false")
        if self.prior_complaint_date:
            if self.prior_complaint_date > self.complaint_date:
                raise ValueError("cross-field: prior_complaint_date must not be after complaint_date")
            if self.transaction_date and self.prior_complaint_date < self.transaction_date:
                raise ValueError("cross-field: prior_complaint_date must not be before transaction_date")
        return self


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    satisfactory: bool
    reason: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _reason_when_unsatisfied(self):
        if not self.satisfactory and not self.reason:
            raise ValueError("cross-field: reason is required when satisfactory is false")
        return self


ROUTES = ("assign-to-entity", "refer-to-other-regulator", "not-maintainable", "escalate-to-human")
DOMAINS = {"broker", "mutual_fund", "registrar", "listed_company", "depository",
           "out_of_jurisdiction", "out_of_corpus", "unknown"}
ESCALATION_REASONS = ("split_regulators", "entity_out_of_corpus", "insufficient_evidence",
                      "verification_failed", "limits_exceeded", "provider_unavailable",
                      "output_filter_blocked")


class Claim(BaseModel):
    claim: str = Field(min_length=3, max_length=700)
    cite: str = Field(min_length=3, max_length=80, description='Clause id such as "RBI-IOS#10(2)(a)", or "complaint"')


class Escalation(BaseModel):
    reason: Literal[ESCALATION_REASONS]  # type: ignore[valid-type]
    detail: str = Field(max_length=500)


class Determination(BaseModel):
    """A routing determination. It never disposes of a complaint; a grievance officer decides."""
    route: Literal[ROUTES]  # type: ignore[valid-type]
    regulator: Optional[str] = Field(default=None, max_length=20)
    domain: str
    entity_type: Optional[str] = Field(default=None, max_length=120)
    timeline_days: Optional[int] = Field(default=None, ge=1, le=365)
    timeline_text: Optional[str] = Field(default=None, max_length=500)
    summary: str = Field(max_length=600)
    next_step: Optional[str] = Field(default=None, max_length=400)
    basis: List[Claim] = Field(default_factory=list)
    escalation: Optional[Escalation] = None

    @field_validator("domain")
    @classmethod
    def _domain_known(cls, v):
        if v not in DOMAINS:
            raise ValueError(f"domain must be one of {sorted(DOMAINS)}")
        return v

    @model_validator(mode="after")
    def _shape(self):
        if self.route == "escalate-to-human":
            if self.escalation is None:
                raise ValueError("escalation is required for route escalate-to-human")
        else:
            if self.escalation is not None:
                raise ValueError("escalation only allowed for route escalate-to-human")
            if not self.regulator:
                raise ValueError("regulator is required for this route")
            if not self.basis:
                raise ValueError("basis is required for this route")
        return self


def format_errors(exc: ValidationError) -> List[dict]:
    out = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()))
        msg = e.get("msg", "invalid")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        out.append({"field": loc or "(cross-field)", "message": msg, "type": e.get("type", "")})
    return out


def validate_complaint(payload: Any) -> Tuple[Optional[ComplaintIn], List[dict]]:
    if not isinstance(payload, dict):
        return None, [{"field": "(body)", "message": "request body must be a JSON object", "type": "type_error"}]
    try:
        return ComplaintIn.model_validate(payload), []
    except ValidationError as exc:
        return None, format_errors(exc)
