"""Public permit output states facts and unknowns, never a personal approval."""
import json
import re
from test_permit_assessment_v2 import result


def test_assessment_and_timeline_never_make_an_eligibility_promise():
    for occupation in ("critical", "ineligible", "neither", "unknown"):
        output = result(occupation=occupation)
        assert not re.search(r"\byou (?:are eligible|qualify)\b|\bguarantee\w*\b", json.dumps(output), re.I)
        assert output["disclaimer"] == output["timeline"]["disclaimer"] == "Not immigration advice"
