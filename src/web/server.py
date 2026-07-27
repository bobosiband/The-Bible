"""FastAPI SSE chat server — the local web front end for Shepherd.

Endpoints:
    GET /       → serves src/web/static/index.html verbatim
    GET /meta   → JSON provenance for the page footer
    POST /ask   → Server-Sent Events stream (passages → tokens → done)

Reuses the SAME pipeline helpers the eval runner uses
(`prepare_baseline`, `prepare_grounded`, `finalise_answer`) — so the
system the chat page talks to is exactly the system the eval measures.

Binds to 127.0.0.1 only. NEVER writes into `data/eval/runs/`. Errors
during retrieval or the model call surface as a single `error` event
and close the stream cleanly — never a hung response.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import ollama
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.corpus.references import parse_references, reference_to_dict
from src.eval.run_eval import (
    BASELINE_SYSTEM_PROMPT,
    DEFAULT_DB,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_S,
    GROUNDED_SYSTEM_PROMPT,
    _read_index_version,
    load_system_prompt,
    read_corpus_sha256,
    read_git_state,
)
from src.pipeline import (
    DEFAULT_ABSTENTION_THRESHOLD,
    DEFAULT_CONTEXT,
    DEFAULT_K,
    MODE_BASELINE,
    MODE_GROUNDED,
    PreparedCall,
    prepare_baseline,
    prepare_grounded,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    mode: str = Field(pattern=f"^({MODE_BASELINE}|{MODE_GROUNDED})$")


# ---------------------------------------------------------------------------
# SSE frame helper
# ---------------------------------------------------------------------------

def sse_frame(event: str, data: dict) -> bytes:
    """Format a Server-Sent Events frame. Every frame ends with a blank
    line so the client's SSE parser flushes it."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


# ---------------------------------------------------------------------------
# Model streaming abstraction
# ---------------------------------------------------------------------------

# A model_stream_fn takes (system_prompt, user_message) and yields
# string chunks of the model's reply. Kept as a function type so tests
# can inject a stub without going near Ollama.
ModelStreamFn = Callable[[str, str], Iterable[str]]


def make_ollama_stream(client: ollama.Client, model: str,
                        options: dict) -> ModelStreamFn:
    """Wrap the ollama client as a token-yielding callable."""
    def stream(system: str, user: str):
        it = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options=options,
            stream=True,
        )
        for chunk in it:
            # ollama 0.4 returns ChatResponse objects with attribute
            # access; be defensive in case a future version returns
            # plain dicts.
            msg = getattr(chunk, "message", None) or chunk.get("message")
            content = getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
            if content:
                yield content
    return stream


# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

@dataclass
class AppState:
    model: str
    model_stream: ModelStreamFn
    db_path: Path
    corpus_sha256: str | None
    index_version: str | None
    baseline_prompt: str
    baseline_prompt_sha: str
    grounded_prompt: str
    grounded_prompt_sha: str
    git_sha: str | None
    git_dirty: bool
    abstention_threshold: float
    retrieval_k: int
    retrieval_context: int


def _load_state(model: str, host: str | None,
                 timeout_s: float, db_path: Path) -> AppState:
    baseline, baseline_sha = load_system_prompt(BASELINE_SYSTEM_PROMPT)
    grounded, grounded_sha = load_system_prompt(GROUNDED_SYSTEM_PROMPT)
    client = ollama.Client(host=host, timeout=timeout_s)
    stream_fn = make_ollama_stream(client, model, {
        "temperature": 0.0, "top_p": 1.0, "seed": 1,
    })
    git_sha, git_dirty = read_git_state()
    return AppState(
        model=model, model_stream=stream_fn,
        db_path=db_path,
        corpus_sha256=read_corpus_sha256(db_path),
        index_version=_read_index_version(db_path),
        baseline_prompt=baseline, baseline_prompt_sha=baseline_sha,
        grounded_prompt=grounded, grounded_prompt_sha=grounded_sha,
        git_sha=git_sha, git_dirty=git_dirty,
        abstention_threshold=DEFAULT_ABSTENTION_THRESHOLD,
        retrieval_k=DEFAULT_K, retrieval_context=DEFAULT_CONTEXT,
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(state: AppState) -> FastAPI:
    app = FastAPI(
        title="Shepherd chat",
        description="Local Bible-study assistant chat, grounded + baseline.",
    )

    @app.get("/")
    def index():
        if not INDEX_HTML.exists():
            raise HTTPException(500, "index.html missing")
        return FileResponse(INDEX_HTML, media_type="text/html")

    @app.get("/meta")
    def meta():
        return JSONResponse({
            "model": state.model,
            "prompts": {
                "baseline": {
                    "path": str(BASELINE_SYSTEM_PROMPT.relative_to(REPO_ROOT)),
                    "sha256": state.baseline_prompt_sha,
                },
                "grounded": {
                    "path": str(GROUNDED_SYSTEM_PROMPT.relative_to(REPO_ROOT)),
                    "sha256": state.grounded_prompt_sha,
                },
            },
            "corpus_sha256": state.corpus_sha256,
            "git_sha": state.git_sha,
            "git_dirty": state.git_dirty,
            "abstention_threshold": state.abstention_threshold,
            "retrieval_k": state.retrieval_k,
            "retrieval_context": state.retrieval_context,
            "index_version": state.index_version,
        })

    @app.post("/ask")
    def ask(req: AskRequest):
        # FastAPI validates the pattern on `mode`; we still guard
        # explicitly to keep the branching close.
        if req.mode not in (MODE_BASELINE, MODE_GROUNDED):
            raise HTTPException(422, f"unknown mode {req.mode!r}")
        return StreamingResponse(
            _stream_response(state, req),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _provenance(state: AppState, mode: str) -> dict:
    if mode == MODE_GROUNDED:
        prompt_sha = state.grounded_prompt_sha
        retrieval_params = {
            "k": state.retrieval_k,
            "context": state.retrieval_context,
            "threshold": state.abstention_threshold,
            "index_version": state.index_version,
        }
    else:
        prompt_sha = state.baseline_prompt_sha
        retrieval_params = None
    return {
        "model": state.model,
        "mode": mode,
        "prompt_sha256": prompt_sha,
        "corpus_sha256": state.corpus_sha256,
        "git_sha": state.git_sha,
        "retrieval_params": retrieval_params,
    }


def _stream_response(state: AppState, req: AskRequest):
    """Generator yielding SSE frames for one /ask call.

    Contract:
    - Always emits `passages` first (empty for baseline; abstention
      marker for grounded-abstain).
    - Emits zero or more `token` events (chunks from the model). No
      tokens when abstaining — the pipeline does NOT call the model.
    - Emits exactly one terminal `done` event with the full answer and
      extracted refs (or `null` on abstention/error).
    - On failure, emits a single `error` event and closes.
    """
    mode = req.mode
    provenance = _provenance(state, mode)

    # Prepare the call (retrieval + prompt assembly for grounded;
    # prompt assembly for baseline). Retrieval errors surface here.
    try:
        if mode == MODE_GROUNDED:
            prep = prepare_grounded(
                req.question,
                system_prompt=state.grounded_prompt,
                k=state.retrieval_k, context=state.retrieval_context,
                threshold=state.abstention_threshold,
                db_path=state.db_path, index_version=state.index_version,
            )
            if prep.output is not None:
                # Abstention or retrieval error.
                out = prep.output
                if out.abstained:
                    yield sse_frame("passages", {
                        "passages": [],
                        "abstained": True,
                        "abstention_reason": (out.retrieval or {}).get(
                            "abstention_reason", "unknown"),
                        "mode": mode,
                    })
                    yield sse_frame("done", {
                        "answer": None, "refs_in_answer": [],
                        "abstained": True,
                        "abstention_reason": (out.retrieval or {}).get(
                            "abstention_reason", "unknown"),
                        "provenance": provenance,
                    })
                    return
                # Retrieval error.
                yield sse_frame("error", {
                    "error": out.error, "stage": "retrieval",
                })
                return
            call: PreparedCall = prep.call
            yield sse_frame("passages", {
                "passages": [
                    {
                        "reference": p.reference, "book": p.book,
                        "chapter": p.chapter,
                        "verse_start": p.verse_start,
                        "verse_end": p.verse_end,
                        "end_chapter": p.end_chapter,
                        "text": p.text, "score": p.score, "rank": p.rank,
                    }
                    for p in call.passages
                ],
                "mode": mode,
            })
        else:
            call = prepare_baseline(req.question, state.baseline_prompt)
            yield sse_frame("passages", {"passages": [], "mode": mode})
    except Exception as e:
        yield sse_frame("error", {
            "error": f"{type(e).__name__}: {e}", "stage": "setup",
        })
        return

    # Stream the model tokens.
    tokens: list[str] = []
    try:
        for chunk in state.model_stream(call.system_prompt, call.user_message):
            tokens.append(chunk)
            yield sse_frame("token", {"text": chunk})
    except Exception as e:
        yield sse_frame("error", {
            "error": f"{type(e).__name__}: {e}", "stage": "model",
        })
        return

    answer = "".join(tokens)
    refs = [reference_to_dict(r) for r in parse_references(answer)]
    yield sse_frame("done", {
        "answer": answer, "refs_in_answer": refs,
        "provenance": provenance,
    })


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import uvicorn
    p = argparse.ArgumentParser(description="Local Shepherd chat server.")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--host", default=DEFAULT_HOST,
                   help="Bind address (default 127.0.0.1 — local only).")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--ollama-host", default=None,
                   help="Override the Ollama endpoint (default localhost).")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    args = p.parse_args(argv)

    if args.host != DEFAULT_HOST:
        # Interactive tool bound to a non-loopback interface would be a
        # security hazard on a laptop that jumps between coffee-shop
        # networks. Fail loudly.
        print(
            f"[refuse] --host {args.host} would expose Shepherd on a "
            f"non-loopback interface. Use 127.0.0.1 or set up a proper "
            f"reverse proxy.",
        )
        return 2

    state = _load_state(args.model, args.ollama_host, args.timeout,
                         DEFAULT_DB)
    app = create_app(state)
    url = f"http://{args.host}:{args.port}"
    print(f"Shepherd chat: {url}")
    print(f"  model={state.model}  corpus_sha={state.corpus_sha256 or 'MISSING'}")
    if state.corpus_sha256 is None:
        print("  [warn] corpus DB missing — grounded mode will error.")
    if state.index_version is None:
        print("  [warn] FTS5 index missing — grounded mode will error.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
