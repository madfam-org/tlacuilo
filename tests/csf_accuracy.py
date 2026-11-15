"""Accuracy of the csf lane on the synthetic corpus, per field and degradation level.

`python -m tests.csf_accuracy` prints the table (markdown) the PR reports; the tests in
`test_csf_lane.py` assert on the same scoring. A field is scored as one of:

- correct: the value read is the truth (a blank field read as blank, a field of the
  other legal form read as not applicable);
- doubtful: not the truth, but flagged — low confidence, invalid, blank or not found
  where the truth has a value. The person is asked to capture or check it;
- WRONG: not the truth and NOT flagged (confidence at or above the low-confidence
  line). The lane's promise is that this count is zero."""

from __future__ import annotations

import sys
import time
from collections import defaultdict

from tlacuilo.contract import DocumentType, Sensitivity
from tlacuilo.csf_contract import FIELD_NAMES
from tlacuilo.pipeline import ExtractionError, run_extraction

from .synth_csf import KINDS, LEVELS, make_csf, page_image, scanned_pdf


def score_field(name: str, got, truth: dict[str, str]) -> str:
    if name not in truth:
        return "correct" if got.status == "not_applicable" else ("doubtful" if got.lowConfidence else "WRONG")
    expected = truth[name]
    if expected == "":
        if got.status == "blank":
            return "correct"
        if got.status == "not_found":
            return "doubtful"  # not wrong, but not read either: the person is asked
        return "doubtful" if got.lowConfidence else "WRONG"
    if got.value == expected and got.status == "read":
        return "correct"
    if got.lowConfidence or got.status in ("not_found", "invalid"):
        return "doubtful"
    return "WRONG"  # a confident wrong value, or a confident «blank» over a printed value


ROWS = (*FIELD_NAMES, "regimenes", "economicActivities", "obligations", "seal", "qr")


def score(result, truth) -> dict[str, str]:
    fields = result.csf.fields
    return {name: score_field(name, getattr(fields, name), truth.fields) for name in FIELD_NAMES}


def score_extras(result, truth) -> dict[str, str]:
    """The tables, the seal and the QR code: correct, doubtful (flagged or unread) or WRONG."""
    block = result.csf

    def rows(got, want, status, items) -> str:
        if got == want:
            return "correct"
        return "doubtful" if status != "read" or any(r.lowConfidence for r in items) else "WRONG"

    out = {}
    for key, got, want, status, items in (
        ("regimenes", [(r.name, _d(r.startDate), _d(r.endDate)) for r in block.regimenes],
         [tuple(r) for r in truth.regimenes], block.regimenesStatus, block.regimenes),
        ("economicActivities", [(a.order, a.name, a.percentage, _d(a.startDate), _d(a.endDate))
                                for a in block.economicActivities],
         [tuple(a) for a in truth.activities], block.economicActivitiesStatus, block.economicActivities),
        ("obligations", [(o.description, o.dueRule or "", _d(o.startDate), _d(o.endDate)) for o in block.obligations],
         [tuple(o) for o in truth.obligations], block.obligationsStatus, block.obligations),
    ):  # fmt: skip
        out[key] = rows(got, want, status, items)
    seal_ok = block.seal.timestamp == truth.seal_timestamp and block.seal.rfc == truth.fields["rfc"]
    seal_flagged = block.seal.status != "read" or block.seal.lowConfidence
    out["seal"] = "correct" if seal_ok else ("doubtful" if seal_flagged else "WRONG")
    out["qr"] = "correct" if block.qr.url == truth.qr_url else ("doubtful" if block.qr.status != "decoded" else "WRONG")
    return out


def _d(value) -> str:
    return value.isoformat() if value else ""


def run(kind: str, level: str, as_image: bool = False):
    pdf, truth = make_csf(kind)
    if level == "text_layer":
        data, mime = pdf, "application/pdf"
    elif as_image:
        data, mime = page_image(pdf, 1, level), "image/jpeg"
    else:
        data, mime = scanned_pdf(pdf, level), "application/pdf"
    result = run_extraction(data, mime, Sensitivity.restricted, doc_type_hint=DocumentType.csf)
    return result, truth


def main(levels=("text_layer", *LEVELS)) -> int:
    table: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    extras: dict[str, list[str]] = defaultdict(list)
    for level in levels:
        for kind in KINDS:
            started = time.perf_counter()
            try:
                result, truth = run(kind, level)
            except ExtractionError as exc:
                extras[level].append(f"{kind}: error {exc.code}")
                for name in ROWS:
                    table[name][level].append("doubtful")
                continue
            for name, verdict in {**score(result, truth), **score_extras(result, truth)}.items():
                table[name][level].append(verdict)
            seconds = time.perf_counter() - started
            extras[level].append(
                f"{kind}: {seconds:.1f}s verdict={result.csf.quality.verdict} qr={result.csf.qr.status}"
                f" seal={result.csf.seal.status} regimenes={result.csf.regimenesStatus}/{len(result.csf.regimenes)}"
            )
    head = "| field | " + " | ".join(levels) + " |"
    print(head)
    print("|" + "---|" * (len(levels) + 1))
    totals = {lv: defaultdict(int) for lv in levels}
    for name in ROWS:
        cells = []
        for lv in levels:
            verdicts = table[name][lv]
            for v in verdicts:
                totals[lv][v] += 1
            ok, wrong = verdicts.count("correct"), verdicts.count("WRONG")
            cells.append(f"{ok}/{len(verdicts)}" + (f" **{wrong} WRONG**" if wrong else ""))
        print(f"| {name} | " + " | ".join(cells) + " |")
    print(
        "| **all fields** | "
        + " | ".join(
            f"{totals[lv]['correct']}/{sum(totals[lv].values())} ({totals[lv]['WRONG']} wrong)" for lv in levels
        )
        + " |"
    )
    for lv in levels:
        print(f"- {lv}: " + "; ".join(extras[lv]))
    return 0


if __name__ == "__main__":
    sys.exit(main(tuple(sys.argv[1:]) or ("text_layer", *LEVELS)))
