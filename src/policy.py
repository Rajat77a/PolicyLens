"""
PolicyLens - Policy Schema
Hardcoded merchant policy rules for the current MVP.

Design principle: FAIL CLOSED. Any missing field, wrong type, out-of-range
value, or unrecognised decision_type is treated as a violation, never as a
silent pass. A merchant-facing risk tool must never let malformed or
unexpected input slip through just because no explicit rule matched it.

Known limitation: this engine validates
STRUCTURED fields only. It does not read stated_reasoning for semantic
contradictions (e.g. text admitting no evidence exists while a field claims
evidence is present). That is flagged as future work, not claimed as solved.
"""

import math

# Rule 1: Discount cap - agent should never approve a discount above this %
MAX_DISCOUNT_PERCENT = 15

# Rule 2: Dispute evidence - agent must cite at least this many RELEVANT
# evidence items when responding to a payment dispute
MIN_DISPUTE_EVIDENCE_ITEMS = 2

# Rule 3: Refund cap - agent should never auto-approve a refund above this
# amount without escalation, and must have supporting evidence either way
MAX_AUTO_REFUND_AMOUNT = 5000  # in INR

VALID_DECISION_TYPES = {"discount_offer", "dispute_response", "refund_approval"}


def _violation(rule, reason):
    return {"rule": rule, "violated": True, "reason": reason}


def _ok(rule, reason):
    return {"rule": rule, "violated": False, "reason": reason}


def _schema_violation(field_name, value):
    """Fail-closed helper for missing/malformed fields."""
    return _violation(
        "schema_validation",
        f"Field '{field_name}' is missing or malformed (got: {value!r}) - failing closed"
    )


def validate_envelope(decision):
    """
    Top-level structural validation, run BEFORE any rule-specific logic.
    Catches: non-dict payloads, missing decision_type, unknown decision_type,
    missing/non-dict details. Returns a violation dict if anything is wrong,
    otherwise None.
    """
    if not isinstance(decision, dict):
        return _schema_violation("payload", decision)

    decision_type = decision.get("decision_type")
    if decision_type not in VALID_DECISION_TYPES:
        return _violation(
            "unknown_decision_type",
            f"decision_type '{decision_type!r}' is not a recognised/allowlisted type - failing closed"
        )

    details = decision.get("details")
    if not isinstance(details, dict):
        return _schema_violation("details", details)

    return None


def _is_clean_number(value):
    """True only for a real, finite int/float (not bool, not NaN/inf).
    Python ints have arbitrary precision and never overflow, so they're
    always 'finite' - only floats need the isfinite check (math.isfinite
    on a huge int would raise OverflowError trying to convert to float)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def check_discount_rule(decision):
    """Rule 1: discount_percent must be a finite number in [0, 100] and not
    exceed MAX_DISCOUNT_PERCENT."""
    if decision["decision_type"] != "discount_offer":
        return None
    discount = decision["details"].get("discount_percent")
    if not _is_clean_number(discount):
        return _schema_violation("discount_percent", discount)
    if not (0 <= discount <= 100):
        return _violation(
            "invalid_range",
            f"discount_percent {discount} is outside the valid 0-100 range - failing closed"
        )
    if discount > MAX_DISCOUNT_PERCENT:
        return _violation(
            "max_discount",
            f"Offered {discount}% discount, exceeds cap of {MAX_DISCOUNT_PERCENT}%"
        )
    return _ok("max_discount", "Within discount cap")


def check_evidence_rule(decision):
    """Rule 2: dispute responses must cite enough RELEVANT evidence items.
    evidence_relevance is REQUIRED (not optional) - a caller cannot bypass
    relevance checking by simply omitting the field. It must be a list of
    strict booleans whose length matches evidence_count exactly, with at
    least MIN_DISPUTE_EVIDENCE_ITEMS entries True."""
    if decision["decision_type"] != "dispute_response":
        return None

    evidence_count = decision["details"].get("evidence_count")
    if not _is_clean_number(evidence_count) or evidence_count != int(evidence_count) or evidence_count < 0:
        return _schema_violation("evidence_count", evidence_count)
    evidence_count = int(evidence_count)

    relevance = decision["details"].get("evidence_relevance")
    if relevance is None or not isinstance(relevance, list) or not all(isinstance(r, bool) for r in relevance):
        return _schema_violation("evidence_relevance", relevance)
    if len(relevance) != evidence_count:
        return _violation(
            "evidence_count_mismatch",
            f"evidence_count={evidence_count} does not match len(evidence_relevance)={len(relevance)} - failing closed"
        )
    relevant_count = sum(1 for r in relevance if r is True)
    if relevant_count < MIN_DISPUTE_EVIDENCE_ITEMS:
        return _violation(
            "min_evidence",
            f"Only {relevant_count} RELEVANT evidence item(s) out of {evidence_count} cited "
            f"(minimum {MIN_DISPUTE_EVIDENCE_ITEMS} relevant items required)"
        )
    return _ok("min_evidence", "Sufficient relevant evidence cited")


def check_refund_rule(decision):
    """Rule 3: auto-approved refunds must not exceed the cap AND must have
    supporting evidence EXPLICITLY confirmed True. Missing, None, or False
    all fail closed - only an explicit True passes."""
    if decision["decision_type"] != "refund_approval":
        return None

    amount = decision["details"].get("refund_amount")
    if not _is_clean_number(amount):
        return _schema_violation("refund_amount", amount)
    if amount < 0:
        return _violation("invalid_range", f"refund_amount {amount} is negative - failing closed")

    if amount > MAX_AUTO_REFUND_AMOUNT:
        return _violation(
            "max_auto_refund",
            f"Auto-approved refund of Rs.{amount}, exceeds cap of Rs.{MAX_AUTO_REFUND_AMOUNT}"
        )

    has_evidence = decision["details"].get("has_supporting_evidence")
    if has_evidence is not True:
        return _schema_violation("has_supporting_evidence", has_evidence)

    return _ok("max_auto_refund", "Within auto-refund cap, evidence explicitly confirmed")


RULES = [check_discount_rule, check_evidence_rule, check_refund_rule]


def run_policy_checks(decision):
    """
    Run full validation against a single decision. Returns a list of result
    dicts. Envelope validation runs first and short-circuits rule checks if
    the payload itself is malformed - this is what prevents crashes on
    missing decision_type / missing details / non-dict payloads.
    """
    envelope_issue = validate_envelope(decision)
    if envelope_issue is not None:
        return [envelope_issue]

    results = []
    for rule_fn in RULES:
        result = rule_fn(decision)
        if result is not None:
            results.append(result)
    return results
