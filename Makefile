# Shepherd — top-level task targets. Keep this file small; each
# `python -m …` module here is the real logic.

PY ?= .venv/bin/python
PYTEST ?= .venv/bin/pytest
MODEL ?= qwen2.5:3b
QUESTIONS ?= data/eval/questions.jsonl

.PHONY: help test test-corpus validate-questions experiment cli chat \
        ingest build-index

help:
	@echo "Shepherd — common tasks"
	@echo ""
	@echo "  make test               run the pytest suite"
	@echo "  make test-corpus        run the suite with --require-corpus"
	@echo "  make validate-questions lint data/eval/questions.jsonl"
	@echo "  make experiment         validate → baseline → grounded →"
	@echo "                          citation_check → compare_runs"
	@echo "  make cli                interactive Shepherd REPL"
	@echo "  make chat               local FastAPI chat page at 127.0.0.1:8765"
	@echo "  make ingest             download the BSB corpus into bible.db"
	@echo "  make build-index        build the FTS5 retrieval index"
	@echo ""
	@echo "Override defaults with MODEL=... QUESTIONS=..."

test:
	$(PYTEST) -q

test-corpus:
	$(PYTEST) --require-corpus -q

validate-questions:
	$(PY) -m src.eval.validate_questions $(QUESTIONS)

experiment:
	$(PY) -m src.eval.experiment --model $(MODEL) --questions $(QUESTIONS)

cli:
	$(PY) -m src.cli --model $(MODEL)

chat:
	$(PY) -m src.web.server --model $(MODEL)

ingest:
	$(PY) -m src.ingest.bsb

build-index:
	$(PY) -m src.retrieval.build_index
