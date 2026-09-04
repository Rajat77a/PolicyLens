"""
PolicyLens - Scoring Engine (MVP)
Runs rule-based policy checks against a dataset of agent decisions,
compares against ground truth labels, and reports precision/recall
plus rupee value caught vs missed.

Usage:
    python score.py
"""

import json
import math
import os
from policy import run_policy_checks

# Robust path resolution: try the expected src/../data/ layout first, but
# fall back to looking in the same directory as this script (in case the
# repo gets flattened during a zip/transfer, e.g. src/ and data/ collapsed
# into one folder). This is defensive - it does not change any scoring
# logic, only where the input file is found.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_PATHS = [
    os.path.join(_SCRIPT_DIR, "..", "data", "decisions.json"),  # normal repo layout
    os.path.join(_SCRIPT_DIR, "decisions.json"),                 # flattened layout
    os.path.join(_SCRIPT_DIR, "data", "decisions.json"),         # data/ nested under src/
]
DATA_PATH = next((p for p in _CANDIDATE_PATHS if os.path.isfile(p)), _CANDIDATE_PATHS[0])


def load_decisions():
    if not os.path.isfile(DATA_PATH):
        raise FileNotFoundError(
            f"Could not find decisions.json. Looked in: {[os.path.abspath(p) for p in _CANDIDATE_PATHS]}. "
            f"Make sure decisions.json is either in a sibling 'data/' folder next to 'src/', "
            f"or in the same folder as score.py."
        )
    with open(DATA_PATH) as f:
        return json.load(f)


def _is_clean_number(value):
    """Same logic as policy.py's helper - avoids math.isfinite() on a
    Python int, which raises OverflowError for very large values."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def validate_test_case(d):
    """
    Validate the test-case fields that score.py itself depends on
    (separate from policy.py's validation of the decision's own fields).
    A malformed value_at_risk or ground_truth_compliant must not be allowed
    to silently distort the confusion matrix or the rupee totals.
    Returns an error string, or None if the case is well-formed.
    """
    gt = d.get("ground_truth_compliant")
    if not isinstance(gt, bool):
        return f"ground_truth_compliant must be a boolean, got {gt!r}"

    value = d.get("value_at_risk")
    if not _is_clean_number(value) or value < 0:
        return f"value_at_risk must be a non-negative finite number, got {value!r}"

    return None


def evaluate(decisions):
    results = []
    skipped = []
    tp = fp = tn = fn = 0
    value_caught = 0
    value_missed = 0
    value_false_alarm = 0

    for d in decisions:
        test_case_error = validate_test_case(d)
        if test_case_error is not None:
            skipped.append({"id": d.get("id", "UNKNOWN"), "error": test_case_error})
            continue

        checks = run_policy_checks(d)
        violated = any(c["violated"] for c in checks)
        actual_violation = not d["ground_truth_compliant"]
        value = d["value_at_risk"]

        if violated and actual_violation:
            tp += 1
            value_caught += value
            outcome = "CAUGHT (true positive)"
        elif violated and not actual_violation:
            fp += 1
            value_false_alarm += value
            outcome = "FALSE ALARM (false positive)"
        elif not violated and actual_violation:
            fn += 1
            value_missed += value
            outcome = "MISSED (false negative)"
        else:
            tn += 1
            outcome = "OK (true negative)"

        results.append({
            "id": d["id"],
            "decision_type": d.get("decision_type"),
            "outcome": outcome,
            "reasons": [c["reason"] for c in checks if c["violated"]],
            "value_at_risk": value
        })

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    summary = {
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1_score": round(f1, 3),
        "rupees_caught": value_caught,
        "rupees_missed": value_missed,
        "rupees_false_alarm": value_false_alarm,
        "skipped_malformed_test_cases": len(skipped)
    }

    return results, summary, skipped


def print_report(results, summary, skipped):
    print("=" * 70)
    print("POLICYLENS - AGENT DECISION COMPLIANCE REPORT")
    print("=" * 70)
    for r in results:
        print(f"\n[{r['id']}] {r['decision_type']} -> {r['outcome']}")
        if r["reasons"]:
            for reason in r["reasons"]:
                print(f"    - {reason}")
        if r["value_at_risk"]:
            print(f"    Value at risk: Rs.{r['value_at_risk']}")

    if skipped:
        print("\n" + "=" * 70)
        print(f"SKIPPED {len(skipped)} MALFORMED TEST CASE(S) IN THE TEST SUITE ITSELF")
        print("=" * 70)
        for s in skipped:
            print(f"[{s['id']}] {s['error']}")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Precision: {summary['precision']}")
    print(f"Recall:    {summary['recall']}")
    print(f"F1 Score:  {summary['f1_score']}")
    print(f"\nTrue Positives (violations caught):  {summary['true_positives']}")
    print(f"False Negatives (violations missed):  {summary['false_negatives']}")
    print(f"False Positives (false alarms):        {summary['false_positives']}")
    print(f"True Negatives (correctly passed):     {summary['true_negatives']}")
    print(f"\nRs.{summary['rupees_caught']} caught in policy violations")
    print(f"Rs.{summary['rupees_missed']} missed (would have leaked through)")
    print(f"Rs.{summary['rupees_false_alarm']} flagged as false alarms")
    print("=" * 70)


if __name__ == "__main__":
    import sys

    decisions = load_decisions()
    results, summary, skipped = evaluate(decisions)
    print_report(results, summary, skipped)

    # Save results for use in README / pitch video - write next to wherever
    # decisions.json was actually found, so this works in a flattened layout too
    out_path = os.path.join(os.path.dirname(DATA_PATH), "results.json")
    with open(out_path, "w") as f:
        json.dump({"results": results, "summary": summary, "skipped": skipped}, f, indent=2)
    print(f"\nDetailed results saved to {out_path}")

    if skipped:
        print(f"\n*** WARNING: {len(skipped)} test case(s) were malformed and EXCLUDED from the "
              f"metrics above. The precision/recall numbers do NOT reflect these cases. ***")
        sys.exit(1)
