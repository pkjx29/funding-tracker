"""The documents must keep up with the code."""
from pathlib import Path

from funding_tracker.checks import CHECKS
from funding_tracker.reconcile import ROUND_COLUMNS, SOURCE_RULES
from funding_tracker.report import RULES_INTRO, write_rules_doc

ROOT = Path(__file__).resolve().parents[1]


def test_readme_lists_every_check():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(CHECKS) == 18
    for check in CHECKS:
        assert f"| {check.id} | {check.name} |" in readme, check.id


def test_data_dictionary_covers_every_round_column():
    dictionary = (ROOT / "docs" / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    for column in ROUND_COLUMNS + ["flags"]:
        assert f"`{column}`" in dictionary, column


def test_rules_document_is_generated_from_the_rules(tmp_path):
    target = tmp_path / "SOURCE_OF_TRUTH.md"
    write_rules_doc(target)
    text = target.read_text(encoding="utf-8")
    assert text.startswith(RULES_INTRO)
    for field, rule, _ in SOURCE_RULES:
        assert field in text and rule in text
