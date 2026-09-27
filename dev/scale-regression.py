#!/usr/bin/env python3
"""Exercise the Rust CLI on a generated, 400k-issue v3 timeline.

The fixture is built outside the checkout. Its records use only ASCII and
integers, for which sorted compact JSON is pecia's canonical form. The test
checks the generated chain, the reviewable snapshot, and query results; it is
not a throughput comparison with Python or a claim about every graph shape.
"""

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record(number):
    return {
        "body": "",
        "created": "2026-09-27",
        "disposition": None,
        "edges": {
            "blocks": [], "caused_by": None, "discovered_from": None,
            "duplicate_of": None, "parent": None, "retires": [],
            "supersedes": None, "validates": None,
        },
        "evidence": "unknown",
        "id": f"pc-{number:012x}",
        "labels": [],
        "owner": "ci:scale-regression",
        "priority": 2,
        "rev": 1,
        "status": "open",
        "title": f"Synthetic issue {number}",
        "type": "task",
        "updated": "2026-09-27",
    }


def generate(root, count):
    store = root / ".git" / "pecia"
    projection = root / ".pecia"
    store.mkdir(parents=True)
    projection.mkdir()
    (projection / "config.yaml").write_text("# generated CI fixture\n")
    previous = None
    with (store / "log.jsonl").open("w") as log, (projection / "work.jsonl").open("w") as snapshot:
        for number in range(1, count + 1):
            rec = record(number)
            snapshot.write(canonical(rec) + "\n")
            entry = {"prev": previous, "rec": rec, "seq": number, "touched": []}
            line = canonical(entry)
            log.write(line + "\n")
            previous = hashlib.sha256(line.encode()).hexdigest()
    (store / "log.mark").write_text(f"{count} {previous}\n")
    (projection / "snapshot.head").write_text(f"{previous}\n")
    return previous


def call(cli, root, *args):
    result = subprocess.run([str(cli), *args, "--json"], cwd=root, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"pecia {' '.join(args)} exited {result.returncode}:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}")
    return [json.loads(line) for line in result.stdout.splitlines() if line]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True, type=Path)
    parser.add_argument("--count", type=int, default=400_000)
    args = parser.parse_args()
    cli = args.cli.resolve(strict=True)
    assert args.count > 0
    with tempfile.TemporaryDirectory(prefix="pecia-scale-") as folder:
        root = Path(folder)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        start = time.perf_counter()
        head = generate(root, args.count)
        generated = time.perf_counter()
        checked = call(cli, root, "check")
        checked_at = time.perf_counter()
        assert checked[-1]["ok"] is True and checked[-1]["records"] == args.count, checked[-1]
        selected = call(cli, root, "next", "--limit", "10")
        queried = time.perf_counter()
        assert len(selected[0]) == min(10, args.count), selected[0]
        assert selected[0][0]["id"] == "pc-000000000001", selected[0][0]
        last_id = f"pc-{args.count:012x}"
        shown = call(cli, root, "show", last_id)
        assert shown[0]["id"] == last_id, shown[0]
        finished = time.perf_counter()
        print(f"scale regression passed: {args.count} issues, check/next/show, head {head[:12]}")
        print(f"seconds: generate={generated-start:.2f} check={checked_at-generated:.2f} "
              f"next={queried-checked_at:.2f} show={finished-queried:.2f}")


if __name__ == "__main__":
    main()
