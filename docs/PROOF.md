# What the two implementations establish

The Python CLI is the reference implementation. Rust was built separately
against the same on-disk format and command behavior. The shared Python
`unittest` suite mostly drives a subprocess named by `PECIA_TEST_CLI`; it
observes stdout, stderr, exit code, and the files a command writes. That
lets one set of behavior assertions run against both executables.

The public repository began at commit `223ec84`, curated from private source
commit `0ddbf9f26eaaf429a02da8bce12c51fb3cd17f2e`. Historical research
and process-only tests were removed. The table below records local arm64
macOS observations from 2026-09-26. Public workflow results belong to exact
commits: [CI at `cd80560`](https://github.com/gruetech/pecia/actions/runs/36491147659)
and [x86-64 at `cd80560`](https://github.com/gruetech/pecia/actions/runs/36491147653)
passed on 2026-09-28; [CI at `05cba86`](https://github.com/gruetech/pecia/actions/runs/36812156794)
and [x86-64 at `05cba86`](https://github.com/gruetech/pecia/actions/runs/36812156791)
also passed. [CI at `df18859`](https://github.com/gruetech/pecia/actions/runs/36963433678)
and [x86-64 at `df18859`](https://github.com/gruetech/pecia/actions/runs/36963433662)
both passed on 2026-10-02. Later commits require their own runs.

| Run | Result | Scope |
| --- | --- | --- |
| Python behavioral suite | 1,252 discovered; OK, 11 skipped | Public candidate after removing eight private review-report tests, Python 3.12. The first commit's 1,260-test run also passed. |
| Rust behavioral suite | 1,252 discovered; OK, 89 skipped | Same final curated candidate and suite, Rust CLI built from the public tree. The first commit's 1,260-test run also passed. |
| Rust internal tests | Passed, release profile | The first public cut exposed tests that assumed a large private ledger. The public corpus now combines its committed lines with fixed generated records; the complete Rust workspace test run passed after that repair. |
| Fresh-clone install | Python copy and Cargo install passed | From a clone of `223ec84`, each CLI completed `init`, `add`, `snapshot`, `check`, `next`, and a hook-mediated commit in a scratch adopter repo. |
| New public ledger | Six records published and synced | A temporary local bare remote held `refs/pecia/log`; a second clone ran `sync`, `doctor`, `check`, and `next`. Before sync, `doctor` reported D009 as expected. This is local transport evidence, not GitHub publication evidence. |
| Python 3.14 smoke | Import and explicit-ledger `check` passed | The public commit hook first failed under unpinned Python 3.14 because a source docstring contained an unpaired surrogate escape. Escaping the example fixed the local run. The `df18859` CI run linked above includes Python 3.11, 3.13, and 3.14 smoke jobs. |

The public GitHub remote later accepted `refs/pecia/log` with a matching
read-back. An anonymous clone of `cd80560` ran `sync`, `doctor`, `check`, and
`next` on its six records. The first-use runs remain scripted scratch trials,
not independent adopter use.
<!-- claims: self-hosting -->

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
A hard-launch claim requires complete CI on the chosen exact release SHA
and a fresh-clone first-use check on its final executable. The local runs in
this table do not supply that verdict for later executable changes.

## 2026-09-30 publication and sparse-gap fixes

Two survey findings now have regression arms in both implementations.
`publish` checks and publishes one captured generation of log bytes; the
Python and Rust arms append an invalid line after validation and verify the
published blob contains only the validated bytes. E008 reports missing
revision intervals without expanding every absent integer; shared Python
and Rust cases include a two-record gap ending at revision 1,000,000,000,000.
In the curated public clone, the Python suite ran 1,255 tests (OK, 11
skipped), the Rust workspace tests passed, and the focused E008 cases passed
against the Rust CLI. Both public workflows passed at the exact `05cba86`
commit, linked above. A later executable needs its own evidence.
<!-- claims: publish-validated-generation, e008-bounded-gaps -->

## 2026-10-01 locale hardening

An independent Claude Opus 5.5 high-effort review found that Python
`publish` could change validated UTF-8 log bytes while handing them to Git
under a non-UTF-8 locale with Python UTF-8 mode disabled. A C-locale run
with `PYTHONUTF8=0` refused a valid title; an ISO-8859-1 run succeeded
but wrote the wrong blob bytes. The same review
found locale-dependent log and snapshot writers and Git metadata handling.
The repair writes canonical UTF-8 bytes explicitly and preserves file-path
and remote-name bytes when calling the operating system. Rust's `init`
now puts the lock-ignore rule on a new line even when the adopter's
`.gitignore` contains invalid UTF-8; its fallback line check reads bytes.

The failing cases were reproduced before repair. On the reviewed candidate,
the focused Python publish and locale classes passed on macOS Python 3.12;
the locale class also passed on Python 3.14. A local Ubuntu Python 3.12
C-locale probe with `PYTHONUTF8=0` passed with a UTF-8 title, owner,
ledger path, repository path, and remote name. Rust's new invalid-UTF-8
`.gitignore` test passed.
The shared invalid-UTF-8 adopter-file case passed explicitly against both
CLIs. It checks that `merge=union` is stripped from a legacy line retaining
`text` while unrelated bytes, including vertical tabs and form feeds,
survive. Another shared case checks that `init` leaves unrelated CRLF and
bare-CR attribute lines byte-identical. On `05cba86`, both CLIs rewrote
such a line and changed Git's `merge` attribute answer for two unrelated
paths; the repaired case preserves both answers. On the 2026-10-02 local
candidate, the full shared suite passed 1,269 tests against Python (11
skipped) and 1,269 against the release Rust CLI (101 skipped);
`cargo test --locked` passed.
Both public workflows passed on exact commit `df18859`, linked above. The
fresh-clone check is recorded below.
<!-- claims: publish-validated-generation, python-utf8-storage-locale, rust-nonutf8-gitignore-init, init-gitattributes-lf-only, reference-implementation, rust-parity -->

## 2026-10-02 first use from the public commit

An anonymous HTTPS clone of `df18859` began with the expected D001 and D009
posture findings: hooks are local configuration, and a plain clone does not
fetch `refs/pecia/log`. `doctor --fix` enabled the hooks and refused to erase
the tracked projection. `sync` hydrated seven published log entries (six
issue IDs); a second `doctor`, `check`, and `next` passed. The clone's worktree
stayed clean.

From that clone, `cargo install --path crates/pecia-cli --locked` installed the
Rust binary in a temporary root. It and a copy of the one-file Python CLI
each completed `init`, a Unicode `add`, `snapshot`, `check`, and `next` in
separate scratch Git repositories. Each reported an active gate after
installing the adopter hook, then completed a hook-mediated commit of its
ledger and hook.
The installed Rust binary also answered a stdio MCP `initialize`,
`tools/list`, and `next` tool call. These are local first-use checks on the
public bytes, not an independent adopter trial.

The README's `cargo install --git` path also installed commit `df18859`
into a temporary root. That binary completed a Unicode `add`, snapshot,
`check`, `next`, an active-hook `doctor`, and a hook-mediated commit in a
third scratch Git repository.
<!-- claims: self-hosting, stdlib-only, mcp-server, adopter-template -->

## Scale regression

On 2026-09-27, the release-profile Rust CLI locally completed `check`,
`next --limit 10`, and `show` on a generated timeline of 400,000 independent
open tasks, one revision each. The generator checks the record count and query
results. The x86-64 CI workflow runs the same script; its
[first public successful run](https://github.com/gruetech/pecia/actions/runs/36491147653)
is a second host observation at `cd80560`; the
[run at `df18859`](https://github.com/gruetech/pecia/actions/runs/36963433662)
also passed. This workload exercises a large
issue count; it does not establish performance for a dense dependency graph,
repeated revisions, or writes at that size.
<!-- claims: rust-scale-local -->

## Reproduce on this tree

```sh
uv run --python 3.12 --script dev/test-runner.py -j 4
cargo build --release --locked
PECIA_TEST_CLI="$PWD/target/release/pecia" uv run --python 3.12 --script dev/test-runner.py -j 4
cargo test --release --locked
uv run --python 3.12 --script dev/claims-check.py
uv run --python 3.12 python dev/scale-regression.py --cli target/release/pecia --count 400000
```

The suite may need `uv` for development scripts and dependencies. The
adopter-facing Python CLI has a separate standard-library-only contract.

## Claim boundaries

[claims.yaml](../claims.yaml) is the current register. The composed v2 format
review remains asserted pending a clean pass over a frozen public release.
The dogfooding report is also asserted in this public tree: one real sibling
adoption, two one-shot imports, and pecia's own ledger are different
observations. The full record remains in the development archive. No benchmark
result is a general performance guarantee; the 400,000-task observation is
bounded to its generated workload and the hosts actually observed.
