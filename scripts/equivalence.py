"""Golden-master equivalence harness for behaviour-preserving refactors.

Runs the public entry points (``run_translation_pipeline`` and
``rebuild_module``) on the real-module corpus against a deterministic
stand-in for the chat-completions endpoint, and records everything a
refactor must not change:

* every request sent to the model, as the exact keyword arguments of
  ``chat.completions.create``; equal request multisets mean equal LLM cost;
* the output and rebuilt archives (header build date masked) and the bytes
  of every resource inside them;
* the metrics document, the translation log and the run statistics, with
  wall-clock fields and random ids removed;
* the wall-clock duration of each run.

The stand-in answers each request as a pure function of the request, so
concurrency cannot change a response. It injects deterministic faults
(omitted keys, truncated JSON) to drive the recovery paths as well.

Usage:
    python scripts/equivalence.py record --out DIR [--src SRC] [--jobs N]
    python scripts/equivalence.py compare BASELINE CANDIDATE [--time-tolerance 0.15]

``--src`` selects the source tree to import (for example a worktree of the
baseline commit); by default the repository's ``src`` is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]

#: Scenario name -> (TranslationConfig overrides, module filter or ``None`` for all).
SCENARIOS: Dict[str, Tuple[Dict[str, Any], Optional[Tuple[str, ...]]]] = {
    "ctx-ru": ({"target_lang": "russian", "use_context": True}, None),
    "plain-de": ({"target_lang": "german", "use_context": False}, None),
    "ctx-pl-female": (
        {
            "target_lang": "polish",
            "use_context": True,
            "player_gender": "female",
            "skip_ncs_llm_gate": True,
            "reasoning_effort": "high",
            "model": "openai/gpt-5.6-luna",
        },
        ("Sandy Valley Days v2.mod", "LES LIONS DIFFAMES_25fev2007.mod", "Midnight.mod"),
    ),
}

#: Keys whose values change between identical runs (clocks, random ids).
_VOLATILE_KEYS = {"latency_ms", "avg_latency_ms", "created_at", "request_id"}

#: JSONL line separator. ``str.splitlines`` would also split on U+2028 and U+0085,
#: which ``json.dumps(ensure_ascii=False)`` leaves unescaped inside strings.
NEWLINE = "\n"


# ---------------------------------------------------------------------------
# Deterministic model stand-in
# ---------------------------------------------------------------------------


def _h(*parts: str) -> int:
    """Returns a stable 48-bit hash of *parts*, the stand-in's only source of variation."""
    return int(hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:12], 16)


def _mark(text: str, salt: str = "") -> str:
    """Returns a deterministic ASCII-marked 'translation' that keeps placeholders intact."""
    return f"[T{_h(text, salt) % 0xFFFF:04x}] {text}"


def _content_text(content: Any) -> str:
    """Returns the text of a message content: a string or a list of text parts."""
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content or "")


def _json_tail(prompt: str) -> Any:
    """Decodes the JSON value that starts at the first ``{`` of *prompt*, or ``None``."""
    idx = prompt.find("{")
    if idx < 0:
        return None
    try:
        return json.JSONDecoder(strict=False).raw_decode(prompt, idx)[0]
    except json.JSONDecodeError:
        return None


def _truncate(payload: str) -> str:
    """Cuts a reply in half, the injected "truncated JSON" fault."""
    return payload[: max(1, len(payload) // 2)]


def _answer_batch(user: str) -> str:
    """Answers a batch request, leaving out some keys of multi-item batches."""
    data = _json_tail(user)
    if not isinstance(data, dict):
        return "{}"
    items = data.get("items") if isinstance(data.get("items"), dict) else data
    out = {}
    for key, cell in items.items():
        text = cell.get("text", "") if isinstance(cell, dict) else str(cell)
        salt = json.dumps(cell, sort_keys=True, ensure_ascii=False)
        if len(items) > 1 and _h("drop", salt) % 23 == 0:
            continue
        out[key] = _mark(text, salt)
    return json.dumps(out, ensure_ascii=False)


def _answer_single(user: str) -> str:
    """Answers a single-string request."""
    match = re.search(r"Text to translate from [^\n]*:\n\n(.*)\Z", user, re.DOTALL)
    text = match.group(1) if match else user
    return json.dumps({"translation": _mark(text, user)}, ensure_ascii=False)


_NODE_RE = re.compile(r"^\[([ER]\d+)\] \[[^\n]*\]:\n<<<(.*?)>>>\s*(?:\n|$)", re.M | re.S)


def _answer_dialog(user: str) -> str:
    """Answers a dialog or grouped dialog request, leaving out some lines."""

    def nodes(script: str) -> Dict[str, str]:
        """Translates the nodes of one script."""
        found = _NODE_RE.findall(script)
        return {
            key: _mark(text, key)
            for key, text in found
            if not (len(found) > 2 and _h("drop", key, text) % 29 == 0)
        }

    files = re.split(r"^=== FILE: ([^\n]+) ===\n", user, flags=re.M)
    if len(files) > 1:
        body = {files[i]: nodes(files[i + 1]) for i in range(1, len(files), 2)}
    else:
        body = nodes(user)
    return json.dumps(body, ensure_ascii=False)


def _answer_gate(user: str) -> str:
    """Answers an NCS gate request with a verdict per entry."""
    data = _json_tail(user)
    if not isinstance(data, dict):
        return "{}"
    entries = data.get("entries") if isinstance(data.get("entries"), dict) else data
    out = {}
    for key, cell in entries.items():
        text = cell.get("text", "") if isinstance(cell, dict) else str(cell)
        verdict = _h("gate", text) % 4 != 0
        out[key] = {"translate": verdict, "reason": "mock_yes" if verdict else "mock_no"}
    return json.dumps(out, ensure_ascii=False)


_NAME_RE = re.compile(r"(?<![\w'])([A-Z][a-z]{2,}(?: [A-Z][a-z]{2,}){0,2})")
_CATEGORIES = ("character", "location", "organization", "item", "nickname", "unknown")


def _answer_entities(user: str) -> str:
    """Answers an entity-extraction request with capitalized phrases of the texts."""
    entities = []
    seen = set()
    for line in user.splitlines():
        match = re.match(r'\[\d+\] "(.*)"$', line)
        if not match:
            continue
        for name in _NAME_RE.findall(match.group(1))[1:]:
            if name in seen or _h("entity", name) % 3 == 0:
                continue
            seen.add(name)
            entities.append({"name": name, "type": _CATEGORIES[_h("cat", name) % 6]})
    return json.dumps({"entities": entities}, ensure_ascii=False)


def _answer_curator(user: str) -> str:
    """Answers a curation request with pseudo-random decisions, some aliases and gaps."""
    data = _json_tail(user)
    if not isinstance(data, dict):
        return "{}"
    names = sorted(data)
    out = {}
    for name in names:
        if len(names) > 3 and _h("omit", name) % 13 == 0:
            continue
        roll = _h("curate", name) % 20
        decision = "keep" if roll < 12 else "local_only" if roll < 15 else "drop"
        cell: Dict[str, Any] = {"decision": decision, "reason": "mock", "priority": roll % 5}
        if roll == 19 and len(names) > 1:
            cell = {"decision": "alias_of", "reason": "mock_alias", "priority": 1}
            cell["alias_of"] = names[0] if names[0] != name else names[-1]
        out[name] = cell
    return json.dumps(out, ensure_ascii=False)


def _answer_glossary(user: str) -> str:
    """Answers a glossary request, leaving out some names."""
    names = [line[2:].split(" (", 1)[0] for line in user.splitlines() if line.startswith("- ")]
    out = {
        name: _mark(name, "glossary")
        for name in names
        if not (len(names) > 1 and _h("omit", name) % 17 == 0)
    }
    return json.dumps(out, ensure_ascii=False)


def fake_completion(kwargs: Dict[str, Any]) -> Tuple[str, str]:
    """Answers one ``chat.completions.create`` call as a pure function of it.

    Args:
        kwargs: Keyword arguments of the call.

    Returns:
        ``(kind, content)``: the request kind recognized from the prompts and
        the reply text, cut in half for some requests.
    """
    messages = kwargs.get("messages") or []
    system = _content_text(messages[0].get("content")) if messages else ""
    user = _content_text(messages[-1].get("content")) if messages else ""

    if "safety gate for translating string literals" in system:
        kind, body = "gate", _answer_gate(user)
    elif "curate proper-name candidates" in system:
        kind, body = "curator", _answer_curator(user)
    elif user.startswith("Extract proper nouns"):
        kind, body = "entities", _answer_entities(user)
    elif "preparing a translation glossary" in system:
        kind, body = "glossary", _answer_glossary(user)
    elif "BATCH MODE" in system:
        kind, body = "batch", _answer_batch(user)
    elif "exactly ONE key" in system:
        kind, body = "single", _answer_single(user)
    elif "<<<" in user:
        kind, body = "dialog", _answer_dialog(user)
    else:
        kind, body = "unknown", "{}"

    if _h("truncate", system, user) % 37 == 0:
        body = _truncate(body)
    return kind, body


class _Recorder:
    """Fake endpoint that answers every call and records its canonical form.

    Attributes:
        lock: Guards the records; the pipeline calls from several threads.
        requests: Canonical JSON of every call, in arrival order.
        kinds: Number of calls per request kind.
    """

    def __init__(self) -> None:
        """Starts with no recorded requests."""
        self.lock = threading.Lock()
        self.requests: List[str] = []
        self.kinds: Dict[str, int] = {}

    def __call__(self, kwargs: Dict[str, Any]) -> Any:
        """Records one call and returns its fake ``ChatCompletion``."""
        from openai.types.chat import ChatCompletion

        kind, content = fake_completion(kwargs)
        canonical = json.dumps(kwargs, sort_keys=True, ensure_ascii=False, default=repr)
        with self.lock:
            self.requests.append(canonical)
            self.kinds[kind] = self.kinds.get(kind, 0) + 1
        prompt_chars = sum(
            len(_content_text(m.get("content"))) for m in kwargs.get("messages") or []
        )
        return ChatCompletion.model_validate(
            {
                "id": "equivalence",
                "object": "chat.completion",
                "created": 0,
                "model": kwargs.get("model", ""),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_chars // 4 + 1,
                    "completion_tokens": len(content) // 4 + 1,
                    "total_tokens": prompt_chars // 4 + len(content) // 4 + 2,
                },
            }
        )


def _install_fake_endpoint(recorder: _Recorder) -> None:
    """Routes the SDK's sync and async ``chat.completions.create`` to *recorder*."""
    from openai.resources.chat.completions import AsyncCompletions, Completions

    def create(self: Any, **kwargs: Any) -> Any:
        """Answers a sync call."""
        return recorder(kwargs)

    async def acreate(self: Any, **kwargs: Any) -> Any:
        """Answers an async call."""
        return recorder(kwargs)

    Completions.create = create  # type: ignore[method-assign]
    AsyncCompletions.create = acreate  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _strip_volatile(value: Any) -> Any:
    """Removes the :data:`_VOLATILE_KEYS` from nested dicts and lists."""
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items() if k not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def _canonical_lines(records: Iterable[Any]) -> List[str]:
    """Returns the records as sorted canonical JSON lines."""
    return sorted(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records)


def _erf_digest(path: Path) -> Dict[str, Any]:
    """Returns the archive digest with the build date masked, plus one digest per resource."""
    raw = bytearray(path.read_bytes())
    raw[32:40] = b"\0" * 8  # BuildYear, BuildDay
    count = struct.unpack_from("<I", raw, 16)[0]
    key_off, res_off = struct.unpack_from("<II", raw, 24)
    resources = {}
    for i in range(count):
        resref = (
            bytes(raw[key_off + i * 24 : key_off + i * 24 + 16]).rstrip(b"\0").decode("latin-1")
        )
        res_type = struct.unpack_from("<H", raw, key_off + i * 24 + 20)[0]
        offset, size = struct.unpack_from("<II", raw, res_off + i * 8)
        resources[f"{resref}.{res_type}"] = hashlib.sha256(raw[offset : offset + size]).hexdigest()
    return {"archive": hashlib.sha256(bytes(raw)).hexdigest(), "resources": resources}


# ---------------------------------------------------------------------------
# One run (child process)
# ---------------------------------------------------------------------------


def _rebuild_edits(log_lines: List[dict]) -> Dict[str, Dict[str, str]]:
    """Picks a deterministic subset of editor rows and edits their translations."""
    edits: Dict[str, Dict[str, str]] = {}
    for entry in log_lines:
        if "event" in entry or not entry.get("item_id") or not entry.get("file"):
            continue
        if _h("edit", entry["file"], entry["item_id"]) % 7:
            continue
        edits.setdefault(entry["file"], {})[entry["item_id"]] = "EDIT " + str(entry["translated"])
    return edits


def run_one(scenario: str, module: Path, out_dir: Path, concurrency: int = 1) -> None:
    """Translates and rebuilds *module* under *scenario*; writes normalized results.

    Args:
        scenario: Key of :data:`SCENARIOS`.
        module: Module of the corpus (its ``manifest.json`` names the language).
        out_dir: Directory of the run's result files.
        concurrency: ``max_concurrent_requests`` of the run.
    """
    recorder = _Recorder()
    _install_fake_endpoint(recorder)

    from nwn_translator.config import TranslationConfig
    from nwn_translator.main import rebuild_module, run_translation_pipeline

    overrides, _modules = SCENARIOS[scenario]
    manifest = json.loads((module.parent / "manifest.json").read_text(encoding="utf-8"))
    language = next(
        (m.get("language") for m in manifest["modules"] if m["file"] == module.name), "english"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="equiv_"))
    try:
        config = TranslationConfig(
            api_key="sk-or-equivalence",
            input_file=module,
            output_file=work / "output.mod",
            source_lang="auto" if language == "english" else language,
            translation_log=work / "log.jsonl",
            metrics_output=work / "metrics.json",
            temp_dir=work,
            skip_cleanup=True,
            quiet=True,
            # One worker is the reference; more workers must give the same results.
            max_concurrent_requests=concurrency,
            **overrides,
        )
        t0 = time.perf_counter()
        result_path, translator = run_translation_pipeline(config)
        translate_seconds = time.perf_counter() - t0
        stats = translator.get_statistics()

        log_lines = [
            json.loads(line)
            # JSONL keeps U+2028/U+0085 unescaped, so split on newlines only.
            for line in (work / "log.jsonl").read_text(encoding="utf-8").split(NEWLINE)
            if line.strip()
        ]
        t1 = time.perf_counter()
        rebuilt = rebuild_module(
            translator.extract_dir,
            _rebuild_edits(log_lines),
            work / "rebuilt.mod",
            module,
            target_lang=config.target_lang,
        )
        rebuild_seconds = time.perf_counter() - t1

        metrics = json.loads((work / "metrics.json").read_text(encoding="utf-8"))
        stats["errors"] = sorted(str(e) for e in stats.get("errors", []))
        result = {
            "requests_total": len(recorder.requests),
            "request_kinds": recorder.kinds,
            "output": _erf_digest(result_path),
            "rebuilt": _erf_digest(rebuilt),
            "stats": _strip_volatile(stats),
            "metrics_summary": _strip_volatile(metrics["summary"]),
            "seconds": {"translate": translate_seconds, "rebuild": rebuild_seconds},
        }
        (out_dir / "result.json").write_text(
            json.dumps(result, sort_keys=True, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (out_dir / "requests.jsonl").write_text(
            "\n".join(sorted(recorder.requests)) + "\n", encoding="utf-8"
        )
        (out_dir / "metrics_requests.jsonl").write_text(
            "\n".join(_canonical_lines(_strip_volatile(metrics["requests"]))) + "\n",
            encoding="utf-8",
        )
        (out_dir / "log.jsonl").write_text(
            "\n".join(_canonical_lines(_strip_volatile(log_lines))) + "\n", encoding="utf-8"
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# Record / compare (parent process)
# ---------------------------------------------------------------------------


def _plan(
    corpus: Path, scenarios: Optional[List[str]], names: Optional[List[str]]
) -> List[Tuple[str, Path]]:
    """Returns the ``(scenario, module)`` runs selected by the command line."""
    modules = sorted(corpus.glob("*.mod"))
    runs = []
    for scenario, (_overrides, subset) in SCENARIOS.items():
        if scenarios and scenario not in scenarios:
            continue
        for module in modules:
            if subset is not None and module.name not in subset:
                continue
            if names and not any(name.lower() in module.name.lower() for name in names):
                continue
            runs.append((scenario, module))
    return runs


def _slug(scenario: str, module: Path) -> str:
    """Returns the directory name of one run."""
    return f"{scenario}__{re.sub(r'[^A-Za-z0-9]+', '-', module.stem).strip('-')}"


def record(args: argparse.Namespace) -> int:
    """Records every selected run, each in a child process with a fixed hash seed.

    Args:
        args: ``record`` command line.

    Returns:
        The process exit code: 1 when a run failed.
    """
    src = Path(args.src).resolve()
    corpus = Path(args.corpus).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONHASHSEED": "0", "PYTHONPATH": str(src)}
    env.pop("NWN_TRANSLATE_API_KEY", None)
    runs = _plan(corpus, args.scenario, args.module)
    failures = []

    def launch(run: Tuple[str, Path]) -> None:
        """Runs one scenario on one module in a child process."""
        scenario, module = run
        target = out / _slug(scenario, module)
        with open(out / f"{_slug(scenario, module)}.stderr.log", "wb") as err:
            code = subprocess.call(
                [
                    sys.executable,
                    __file__,
                    "run",
                    scenario,
                    str(module),
                    str(target),
                    "--concurrency",
                    str(args.concurrency),
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=err,
            )
        status = "ok" if code == 0 else f"exit {code}"
        print(f"{status:8} {_slug(scenario, module)}", flush=True)
        if code:
            failures.append(_slug(scenario, module))

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        list(pool.map(launch, runs))
    (out / "source.txt").write_text(str(src), encoding="utf-8")
    return 1 if failures else 0


def _read_lines(path: Path) -> List[str]:
    """Returns the newline-separated lines of *path*, none when it is missing."""
    return path.read_text(encoding="utf-8").split(NEWLINE) if path.exists() else []


def _diff_multiset(a: List[str], b: List[str]) -> Tuple[int, int]:
    """Counts the lines only in *a* and only in *b*, as multisets."""
    from collections import Counter

    ca, cb = Counter(a), Counter(b)
    return sum((ca - cb).values()), sum((cb - ca).values())


def compare(args: argparse.Namespace) -> int:
    """Compares two recorded directories run by run and prints the verdict.

    Args:
        args: ``compare`` command line.

    Returns:
        The process exit code: 1 when a run differs, is missing or the total
        time regressed beyond the tolerance.
    """
    base, cand = Path(args.baseline), Path(args.candidate)
    slugs = sorted(p.name for p in base.iterdir() if (p / "result.json").exists())
    if args.partial:
        slugs = [slug for slug in slugs if (cand / slug / "result.json").exists()]
    failed = False
    total_base = total_cand = 0.0
    print(f"{'run':52} {'req':>7} {'out':>4} {'rbl':>4} {'log':>4} {'met':>4} {'sta':>4} time")
    for slug in slugs:
        if not (cand / slug / "result.json").exists():
            print(f"{slug:52} MISSING in candidate")
            failed = True
            continue
        rb = json.loads((base / slug / "result.json").read_text(encoding="utf-8"))
        rc = json.loads((cand / slug / "result.json").read_text(encoding="utf-8"))
        req = _diff_multiset(
            _read_lines(base / slug / "requests.jsonl"), _read_lines(cand / slug / "requests.jsonl")
        )
        checks = {
            "req": req == (0, 0),
            "out": rb["output"] == rc["output"],
            "rbl": rb["rebuilt"] == rc["rebuilt"],
            "log": _read_lines(base / slug / "log.jsonl") == _read_lines(cand / slug / "log.jsonl"),
            "met": rb["metrics_summary"] == rc["metrics_summary"]
            and _read_lines(base / slug / "metrics_requests.jsonl")
            == _read_lines(cand / slug / "metrics_requests.jsonl"),
            "sta": rb["stats"] == rc["stats"],
        }
        tb = rb["seconds"]["translate"] + rb["seconds"]["rebuild"]
        tc = rc["seconds"]["translate"] + rc["seconds"]["rebuild"]
        total_base += tb
        total_cand += tc
        marks = " ".join(f"{'ok' if ok else 'DIFF':>4}" for ok in checks.values())
        req_note = f"{rb['requests_total']:>7}" if checks["req"] else f"-{req[0]}/+{req[1]}"
        print(f"{slug:52} {req_note:>7} {marks} {tb:6.1f}s -> {tc:6.1f}s")
        failed |= not all(checks.values())
    ratio = total_cand / total_base if total_base else 1.0
    print(f"total time {total_base:.1f}s -> {total_cand:.1f}s (x{ratio:.3f})")
    if ratio > 1 + args.time_tolerance:
        print("TIME REGRESSION beyond tolerance")
        failed = True
    print("EQUIVALENT" if not failed else "NOT EQUIVALENT")
    return 1 if failed else 0


def main(argv: Optional[List[str]] = None) -> int:
    """Runs the ``record``, ``compare`` or internal ``run`` command.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="run every scenario and store normalized results")
    rec.add_argument("--out", required=True)
    rec.add_argument("--src", default=str(REPO / "src"))
    rec.add_argument("--corpus", default=os.getenv("NWN_TEST_CORPUS", str(REPO / "test_corpus")))
    rec.add_argument("--jobs", type=int, default=4)
    rec.add_argument("--scenario", action="append", help="limit to these scenarios")
    rec.add_argument("--module", action="append", help="limit to modules containing this text")
    rec.add_argument("--concurrency", type=int, default=1, help="max_concurrent_requests")
    cmp_ = sub.add_parser("compare", help="compare two recorded result directories")
    cmp_.add_argument("baseline")
    cmp_.add_argument("candidate")
    cmp_.add_argument("--time-tolerance", type=float, default=0.15)
    cmp_.add_argument(
        "--partial", action="store_true", help="compare only runs present in the candidate"
    )
    one = sub.add_parser("run", help="(internal) one scenario on one module")
    one.add_argument("scenario")
    one.add_argument("module")
    one.add_argument("out")
    one.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args(argv)
    if args.command == "record":
        return record(args)
    if args.command == "compare":
        return compare(args)
    run_one(args.scenario, Path(args.module), Path(args.out), args.concurrency)
    return 0


if __name__ == "__main__":
    sys.exit(main())
