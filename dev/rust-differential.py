#!/usr/bin/env python3
"""Python/Rust differential over the real store — pc-71fb, pc-ddd9.

THE PORT'S ACCEPTANCE TEST, and it is symmetric by construction. Under the v2
form the only available test was "does Rust reproduce CPython's bytes", which
meant emulating a form no document defined. v3.0 writes the form down (RFC 8785
over a narrowed domain), so both implementations are checkable against a
SPECIFICATION and the comparison between them is a differential rather than an
imitation: for every line of the live log and the committed projection, the two
must agree on the canonical bytes AND on the SHA-256, and the chain each one
computes must verify.

Lives in dev/ rather than in `cargo test` because the live log is in the git
common dir and is not committed — a cargo test must run on any clone, so the
in-repo fixed-point test over the projection is the hermetic half.

Usage: dev/rust-differential.py [--log PATH] [--projection PATH]
Exit 0 agreement / 1 disagreement / 2 cannot-run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def die(msg: str) -> None:
    print(json.dumps({"ok": False, "cannot_run": msg}), file=sys.stderr)
    raise SystemExit(2)


def rust_side(lines: list[str]) -> list[str]:
    build = subprocess.run(["cargo", "build", "-q", "--release", "--example", "canon"],
                           cwd=ROOT, capture_output=True, text=True)
    if build.returncode != 0:
        die(f"cargo build failed: {build.stderr.strip()[:400]}")
    run = subprocess.run([str(ROOT / "target/release/examples/canon")],
                         input="\n".join(lines) + "\n",
                         capture_output=True, text=True, cwd=ROOT)
    if run.returncode != 0:
        die(f"the rust example failed: {run.stderr.strip()[:400]}")
    # LF only: a canonical line may carry a raw U+2028 (v3.0), which
    # splitlines() would shear.
    return [l for l in run.stdout.split("\n") if l]


def main() -> int:
    import pecia_cli as pc

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log", default=None, help="default: this clone's store")
    ap.add_argument("--projection", default=str(ROOT / ".pecia/work.jsonl"))
    args = ap.parse_args()

    log = Path(args.log) if args.log else pc.log_path()
    if log is None or not log.is_file():
        die(f"no log at {log}")

    report: dict[str, object] = {"note": 'Exit 0 means the two implementations agree '
                                         'ON WHAT WAS COMPARED, never that either is right.'}
    disagreements: list[dict] = []

    for name, path, chained in (("log", log, True),
                                ("projection", Path(args.projection), False)):
        text = path.read_text(encoding="utf-8")
        lines = [l for l in text.split("\n") if l.strip()]
        if not lines:
            die(f"{path} is empty — an empty denominator proves nothing")
        rust = rust_side(lines)
        if len(rust) != len(lines):
            die(f"{name}: rust returned {len(rust)} lines for {len(lines)}")

        prev_py = prev_rs = None
        for n, (line, answer) in enumerate(zip(lines, rust), start=1):
            if answer.startswith("!"):
                disagreements.append({"where": f"{name}:{n}",
                                      "kind": "rust refused what python parsed",
                                      "detail": answer[1:200]})
                continue
            rs_hash, _, rs_canonical = answer.partition("\t")
            value = pc.strict_json_loads(line)
            py_canonical = pc.canonical(value)
            py_hash = hashlib.sha256(py_canonical.encode("utf-8")).hexdigest()
            if py_canonical != rs_canonical:
                disagreements.append({"where": f"{name}:{n}", "kind": "canonical bytes",
                                      "python": py_canonical[:120], "rust": rs_canonical[:120]})
            if py_hash != rs_hash:
                disagreements.append({"where": f"{name}:{n}", "kind": "hash",
                                      "python": py_hash, "rust": rs_hash})
            if chained:
                declared = value.get("prev")
                for side, expected in (("python", prev_py), ("rust", prev_rs)):
                    if declared != expected:
                        disagreements.append({"where": f"{name}:{n}",
                                              "kind": f"chain ({side})",
                                              "declared": declared, "expected": expected})
                prev_py, prev_rs = py_hash, rs_hash
        report[f"{name}_lines"] = len(lines)

    report["ok"] = not disagreements
    report["disagreements"] = len(disagreements)
    for d in disagreements[:10]:
        print(json.dumps(d, ensure_ascii=False))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if disagreements else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — totality at the entry point (v2.11)
        die(f"{type(exc).__name__}: {exc}")
