# What the two implementations establish

The Python CLI is the reference implementation. Rust was built separately
against the same on-disk format and command behavior. The shared Python
`unittest` suite mostly drives a subprocess named by `PECIA_TEST_CLI`; it
observes stdout, stderr, exit code, and the files a command writes. That
lets one set of behavior assertions run against both executables.

The public repository began at commit `223ec84`, curated from private source
commit `0ddbf9f26eaaf429a02da8bce12c51fb3cd17f2e`. Historical research
and process-only tests were removed. These are local arm64 macOS observations
from the public repo on 2026-09-26, before a public-host CI run:

| Run | Result | Scope |
| --- | --- | --- |
| Python behavioral suite | 1,252 discovered; OK, 11 skipped | Public candidate after removing eight private review-report tests, Python 3.12. The first commit's 1,260-test run also passed. |
| Rust behavioral suite | 1,252 discovered; OK, 89 skipped | Same final curated candidate and suite, Rust CLI built from the public tree. The first commit's 1,260-test run also passed. |
| Rust internal tests | Passed, release profile | The first public cut exposed tests that assumed a large private ledger. The public corpus now combines its committed lines with fixed generated records; the complete Rust workspace test run passed after that repair. |
| Fresh-clone install | Python copy and Cargo install passed | From a clone of `223ec84`, each CLI completed `init`, `add`, `snapshot`, `check`, `next`, and a hook-mediated commit in a scratch adopter repo. |
| New public ledger | Six records published and synced | A temporary local bare remote held `refs/pecia/log`; a second clone ran `sync`, `doctor`, `check`, and `next`. Before sync, `doctor` reported D009 as expected. This is local transport evidence, not GitHub publication evidence. |
| Python 3.14 smoke | Import and explicit-ledger `check` passed | The public commit hook first failed under unpinned Python 3.14 because a source docstring contained an unpaired surrogate escape. Escaping the example fixed the local run; CI now has version smoke jobs awaiting the public host. |

The skipped Rust arms include Python-only import and implementation probes.
They are not Rust passes. Rust's own tests exercise internal properties,
and the shared suite supplies a cross-implementation behavioral comparison.
Agreement can still reflect a mistaken shared specification or a test that
does not observe the relevant behavior. A green checker establishes structural
well-formedness, never truth.

The curated test tree omits private agent-review round tests and sibling
adapter tests because their fixtures live in the separate development archive.
The first cut without those fixtures failed solely in those areas; after
removing the corresponding test modules and classes, the suite above passed.
The public release SHA still needs its own CI run and an outsider first-use
trial before a hard-launch claim is warranted.

## Reproduce on this tree

```sh
uv run --python 3.12 --script dev/test-runner.py -j 4
cargo build --release --locked
PECIA_TEST_CLI="$PWD/target/release/pecia" uv run --python 3.12 --script dev/test-runner.py -j 4
cargo test --release --locked
uv run --python 3.12 --script dev/claims-check.py
```

The suite may need `uv` for development scripts and dependencies. The
adopter-facing Python CLI has a separate standard-library-only contract.

## Claim boundaries

[claims.yaml](../claims.yaml) is the current register. The composed v2 format
review remains asserted pending a clean pass over a frozen public release.
The dogfooding report is also asserted in this public tree: one real sibling
adoption, two one-shot imports, and pecia's own ledger are different
observations. The full record remains in the development archive. No benchmark
number is a release claim yet.
