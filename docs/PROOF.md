# What the two implementations establish

The Python CLI is the reference implementation. Rust was built separately
against the same on-disk format and command behavior. The shared Python
`unittest` suite mostly drives a subprocess named by `PECIA_TEST_CLI`; it
observes stdout, stderr, exit code, and the files a command writes. That
lets one set of behavior assertions run against both executables.

At private source commit `0ddbf9f26eaaf429a02da8bce12c51fb3cd17f2e`,
a curated source tree with historical research and process-only tests
removed gave these local results on arm64 macOS, 2026-09-26:

| Run | Result | Scope |
| --- | --- | --- |
| Python behavioral suite | 1,260 discovered; OK, 11 skipped | Curated public tree, Python 3.12. |
| Rust behavioral suite | 1,260 discovered; OK, 89 skipped | Same tree and suite, Rust CLI built from the source commit. |
| Rust internal tests | Passed | Original source tree, release profile. |

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
This scope is explicit in the public repository. The public release SHA needs
its own CI run, both install paths reproduced from a fresh clone, and a
five-minute first-use trial by someone outside the project before a hard
launch claim is warranted.

## Reproduce on this tree

```sh
python3 dev/test-runner.py -j 4
cargo build --release --locked
PECIA_TEST_CLI="$PWD/target/release/pecia" python3 dev/test-runner.py -j 4
cargo test --release --locked
python3 dev/claims-check.py
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
