"""Root-level pytest configuration.

Registers the `corpus` marker and its --require-corpus / SHEPHERD_REQUIRE_CORPUS
control surface. Any test that touches data/corpus/bible.db should carry
`@pytest.mark.corpus`; those tests are skipped by default when the DB is
absent, but the run prints a loud summary line so nobody mistakes a green
run for a real corpus audit.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

DB_PATH = Path(__file__).resolve().parent / "data" / "corpus" / "bible.db"

_SKIPPED_CORPUS_TESTS: list[str] = []


def pytest_addoption(parser):
    parser.addoption(
        "--require-corpus",
        action="store_true",
        default=False,
        help=(
            "Turn 'corpus test skipped because DB is absent' into a hard error. "
            "Also enabled by setting SHEPHERD_REQUIRE_CORPUS=1."
        ),
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "corpus: test requires data/corpus/bible.db (run `python -m src.ingest.bsb`)",
    )


def _require_corpus(config) -> bool:
    return (
        config.getoption("--require-corpus")
        or os.environ.get("SHEPHERD_REQUIRE_CORPUS") == "1"
    )


def pytest_collection_modifyitems(config, items):
    """Skip corpus-marked tests when the DB is absent — unless --require-corpus
    is set, in which case fail the whole session with a clear message."""
    db_present = DB_PATH.exists()
    require = _require_corpus(config)

    if require and not db_present:
        raise pytest.UsageError(
            "--require-corpus (or SHEPHERD_REQUIRE_CORPUS=1) was set, but "
            f"{DB_PATH} does not exist. Run `python -m src.ingest.bsb` first."
        )

    if db_present:
        return

    skip_marker = pytest.mark.skip(
        reason="requires data/corpus/bible.db (run `python -m src.ingest.bsb`)"
    )
    for item in items:
        if "corpus" in item.keywords:
            item.add_marker(skip_marker)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "setup" and rep.skipped and "corpus" in item.keywords:
        _SKIPPED_CORPUS_TESTS.append(item.nodeid)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not _SKIPPED_CORPUS_TESTS:
        return
    n = len(_SKIPPED_CORPUS_TESTS)
    terminalreporter.write_sep("=", "CORPUS AUDIT INCOMPLETE", red=True, bold=True)
    terminalreporter.write_line(
        f"WARNING: {n} corpus-dependent test(s) SKIPPED — the DB is not present.",
        red=True, bold=True,
    )
    terminalreporter.write_line(
        "  A green run without the corpus proves nothing about corpus fidelity.",
        red=True,
    )
    terminalreporter.write_line(
        "  Run  `python -m src.ingest.bsb`  and re-run pytest for the full audit,",
        red=True,
    )
    terminalreporter.write_line(
        "  or  `pytest --require-corpus`  (or SHEPHERD_REQUIRE_CORPUS=1) to make",
        red=True,
    )
    terminalreporter.write_line(
        "  the missing DB a hard failure.",
        red=True,
    )
