"""Candidate-facing copy follows the active evidence and page contract."""

from backend.services.fit import verify
from backend.services.intake.interview import BUILD_NOW, SetupChat
from backend.services.intake.job import IntakeJob


def test_fit_blocker_without_ai_reason_uses_neutral_evidence_wording():
    posting = "Applicants must hold an active security clearance."
    result = verify(
        {"hard_blockers": [{"excerpt": posting, "reason": ""}]},
        posting,
        {"entries": [], "never": []},
    )
    assert result["hard_blockers"] == [{
        "excerpt": posting,
        "reason": "stated requirement not supported by registered evidence",
    }]


def test_setup_copy_uses_saved_page_contract_and_does_not_guess_before_one_exists(tmp_path):
    chat = SetupChat(IntakeJob(tmp_path), "Example Person", [], lambda *_: None, lambda *_: {}, lambda: {})
    assert "a base resume sized for your chosen market" in chat._greeting()
    assert "one-page" not in chat._greeting()

    config = tmp_path / "data/config/profile.yml"
    config.parent.mkdir(parents=True)
    config.write_text("resume_contract:\n  required_pages: 2\n", encoding="utf-8")
    assert "a two-page base resume" in chat._greeting()

    chat._spawn = lambda *_: None
    chat.view = lambda: {}
    thread = {"messages": [], "pending": None, "asked": [], "ai_asked": 0, "flags": {}}
    question = {"id": "build", "key": "build", "options": [{"value": BUILD_NOW, "label": BUILD_NOW}]}
    chat._answer(thread, question, [BUILD_NOW], "")
    assert "then a two-page base resume" in thread["messages"][-1]["text"]
