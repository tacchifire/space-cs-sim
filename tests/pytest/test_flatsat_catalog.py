"""The web catalog must match the existing curriculum and stay within its documents."""
import importlib.util
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
from cuberange.flatsat import catalog  # noqa: E402


def test_console_catalog_keeps_the_canonical_order_and_metadata():
    spec = importlib.util.spec_from_file_location("_test_web_syllabus", REPO / "tools/syllabus.py")
    syllabus = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(syllabus)
    found = syllabus.exercises()
    items = catalog.list_exercises()
    assert [item["id"] for item in items] == syllabus.order(found)
    assert len(items) == len(found) > 0
    for item in items:
        source = found[item["id"]]
        assert item["directory"] == source["dir"]
        assert item["title_en"] == source["title"]
        assert item["title"] and item["description"]
        assert item["command"] == f"make exercise EX={source['dir']}"
        for field in ("layer", "difficulty", "duration"):
            assert item[field] == source[field]


def test_console_document_is_the_original_readme_in_each_language():
    item = catalog.list_exercises()[0]
    for lang, name in (("ja", "README.ja.md"), ("en", "README.md")):
        assert catalog.read_exercise(item["id"], lang) == (
            REPO / "exercises" / item["directory"] / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("identifier", ["../SAFE_USE", "EX-S07/../../README", "EX-Z99", "", None])
def test_document_lookup_rejects_unknown_ids_and_paths(identifier):
    with pytest.raises(ValueError):
        catalog.read_exercise(identifier)


@pytest.mark.parametrize("lang", ["../../README", "fr", "", None])
def test_document_lookup_rejects_unknown_languages(lang):
    with pytest.raises(ValueError):
        catalog.read_exercise(catalog.list_exercises()[0]["id"], lang)


def test_objective_excerpt_stops_before_the_next_paragraph_or_heading():
    text = "## あなたの目的\n\nFirst line.\nSecond line.\n\nDifferent paragraph.\n\n## 次\nHidden."
    assert catalog._objective(text, "ja") == "First line. Second line."
