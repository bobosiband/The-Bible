"""Tests for src.eval.validate_questions.

Every failure mode surfaces as a line-numbered `path:lineno: message`
diagnostic. Successful validation is silent (empty list). The runner
reuses validate_file, so the same rules apply in both places.
"""
from __future__ import annotations

import pytest

from src.eval.validate_questions import (
    ValidationError,
    main,
    parse_valid_records,
    validate_file,
)


def _write(tmp_path, content: str):
    p = tmp_path / "q.jsonl"
    p.write_text(content)
    return p


def test_valid_file_returns_no_errors(tmp_path):
    p = _write(tmp_path, '{"id":"q001","question":"What is love?"}\n')
    assert validate_file(p) == []


def test_empty_file_is_valid_but_yields_no_records(tmp_path):
    """The runner's Stage-3 refusal treats emptiness separately; the
    validator itself accepts an empty file."""
    p = _write(tmp_path, "")
    assert validate_file(p) == []
    assert parse_valid_records(p) == []


def test_comment_and_blank_lines_are_skipped(tmp_path):
    p = _write(tmp_path, '# header\n\n{"question":"q"}\n\n# tail\n')
    assert validate_file(p) == []
    recs = parse_valid_records(p)
    assert len(recs) == 1 and recs[0]["question"] == "q"


def test_invalid_json_line_reported_with_lineno(tmp_path):
    p = _write(tmp_path, '{"question":"ok"}\nnot json\n{"question":"ok2"}\n')
    errors = validate_file(p)
    assert len(errors) == 1
    assert errors[0].lineno == 2
    assert "invalid JSON" in errors[0].message


def test_missing_question_field_reported(tmp_path):
    p = _write(tmp_path, '{"id":"q001"}\n')
    errors = validate_file(p)
    assert len(errors) == 1
    assert errors[0].lineno == 1
    assert "missing required field 'question'" in errors[0].message


def test_empty_question_string_reported(tmp_path):
    p = _write(tmp_path, '{"question":"   "}\n')
    errors = validate_file(p)
    assert len(errors) == 1
    assert "non-empty string" in errors[0].message


def test_non_object_json_reported(tmp_path):
    p = _write(tmp_path, '["not", "an", "object"]\n')
    errors = validate_file(p)
    assert len(errors) == 1
    assert "expected a JSON object" in errors[0].message


def test_duplicate_id_reported_with_first_line_hint(tmp_path):
    p = _write(
        tmp_path,
        '{"id":"q001","question":"a"}\n{"id":"q001","question":"b"}\n',
    )
    errors = validate_file(p)
    assert len(errors) == 1
    assert errors[0].lineno == 2
    assert "duplicate id 'q001'" in errors[0].message
    assert "line 1" in errors[0].message


def test_bad_expected_refs_type_reported(tmp_path):
    p = _write(
        tmp_path,
        '{"question":"q","expected_refs":"John 3:16"}\n',
    )
    errors = validate_file(p)
    assert len(errors) == 1
    assert "array of strings" in errors[0].message


def test_all_errors_collected_not_first_only(tmp_path):
    p = _write(
        tmp_path,
        '{"id":"q001"}\n'                 # missing question
        '{"question":""}\n'               # empty question
        'not json\n',                     # bad JSON
    )
    errors = validate_file(p)
    assert [e.lineno for e in errors] == [1, 2, 3]


def test_validation_error_format_is_compiler_style(tmp_path):
    p = _write(tmp_path, '{"id":"q001"}\n')
    err = validate_file(p)[0]
    formatted = err.format()
    assert formatted.startswith(str(p) + ":1:")
    assert "missing required field 'question'" in formatted


def test_validate_file_raises_for_missing_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        validate_file(tmp_path / "nope.jsonl")


def test_cli_returns_zero_on_valid(tmp_path, capsys):
    p = _write(tmp_path, '{"question":"q"}\n')
    rc = main([str(p)])
    assert rc == 0
    assert "validates cleanly" in capsys.readouterr().out


def test_cli_returns_two_on_invalid(tmp_path, capsys):
    p = _write(tmp_path, '{"id":"q001"}\n')
    rc = main([str(p)])
    assert rc == 2
    err = capsys.readouterr().err
    assert str(p) + ":1:" in err
    assert "1 validation error" in err


def test_cli_returns_three_on_missing_file(tmp_path, capsys):
    rc = main([str(tmp_path / "nope.jsonl")])
    assert rc == 3
    assert "not found" in capsys.readouterr().err


def test_parse_valid_records_auto_assigns_id_from_lineno(tmp_path):
    p = _write(tmp_path, '{"question":"q1"}\n{"question":"q2"}\n')
    recs = parse_valid_records(p)
    assert [r["id"] for r in recs] == ["q001", "q002"]


def test_parse_valid_records_respects_explicit_id(tmp_path):
    p = _write(tmp_path, '{"id":"custom","question":"q1"}\n')
    recs = parse_valid_records(p)
    assert recs[0]["id"] == "custom"
