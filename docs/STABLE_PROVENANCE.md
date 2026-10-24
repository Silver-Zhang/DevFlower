# v3 Stable release provenance, version 2

This is a consumer protocol for an independently approved frozen installation. Only the fixed external supervisor may emit the real evidence and owner record, after actual independent hard ACCEPT, the fixed external Gate, authorized merge and GitHub/main readback. The workbench neither issues these records nor provides a promotion endpoint. Version 1 records and existing Usable/Studio installations keep their legacy semantics; **new v3 Stable requires version 2**. No binding remains Development. Session-only entry rejects Stable.

The trust anchor is the owner-controlled operator file and its externally supplied SHA-256. Evidence must be pinned there through the release record and immutable outside all Agent-writable locations. File hashes/JSON cannot establish human authorship if an attacker controls that trust anchor; do not let the candidate choose, write, or re-pin operator/owner evidence. A real owner record must reference the preserved original user grant; a label, invented reference, or model ACCEPT is insufficient.

## References and identities

`Ref` is exactly `{"path":"/absolute/private/file","sha256":"<64 lowercase hex>"}`. Paths must be regular single-link files, with no symlink ancestors, no `..`, and at most 2,000,000 bytes each. All raw bytes are hashed; changing whitespace changes a pin. UTF-8 contract/user-grant bytes are retained exactly. All references, including nested references, are re-read at startup and every operator check before execution. Inode/mtime/size/mode changes and same-byte replacements require a restart with a new external binding. They cannot silently replace evidence during an active service.

Every `Identity` is exactly:

```json
{"run_id":"<external run>","round":1,"candidate_sha256":"<full manifest SHA256>","acceptance_sha256":"<raw contract Ref SHA256>"}
```

Run is nonempty, round is an integer >= 1. The owner/review/Gate identities must be identical. The actual review report and every mechanical verification also bind that run, round, candidate and contract. Required contract IDs and verification IDs come from the external owner, never hardcoded product IDs; both lists must be nonempty and unique.

## Exact records

All objects below are closed schemas except existing native result and process envelopes, which retain their additional captured diagnostics. Each versioned evidence object has integer `version: 1` and the indicated `kind`.

| Object | Fields in addition to version/kind |
| --- | --- |
| Release record (`version: 2`, **no kind**) | `root`, `git_commit`, `git_tree`, `candidate_sha256`, `files`, `provenance` |
| `provenance` (unversioned) | `contract`, `review`, `gate`, `owner`, `commit`, each a Ref |
| `release-review` | `identity`, `provider: "claude"`, `report` Ref, `native_result` Ref, `invocation` Ref |
| `release-gate` | `identity`, `verdict: "ACCEPT"`, `process`, `review` Ref, `report_sha256`, `supervisor` Ref, `implementation` Ref, `verification` array, `implementation_invocation` Ref |
| `release-owner` | `identity`, `root`, `git_commit`, `git_tree`, `scope: ["install","start"]`, `required_contracts`, `verification_ids`, `reviewer_session_id`, `implementation_session_id`, `contract` Ref, `review` Ref, `gate` Ref, `commit` Ref, `supervisor` Ref, `user_grant` Ref, `native_bindings`, `invocations` |

The owner pins the exact install root, full source identity, Git objects, reviewer/implementation sessions, obligations, evidence references and scope. Its contract/review/Gate/commit Refs equal the release record's Refs. Its supervisor Ref equals the Gate's supervisor Ref. `user_grant` references the original real user authorization, not a summary claiming it exists. `supervisor` references the fixed external Gate/supervisor code bytes, outside the candidate; the external owner must establish this is the approved old supervisor and capture its actual execution.

`files` is the complete sorted `orchestration.common.manifest(root)` list, including ignored/untracked files, without .git. `candidate_sha256` hashes `canonical(files)`. Symlinks are forbidden. `git_tree` is computed from those exact file bytes and executable modes using Git blob/tree object framing and Git byte ordering, even when the installed copy has no .git. `commit` references the **raw uncompressed commit payload**, as read externally with `git cat-file commit <commit>` (without the Git object header). The loader checks `SHA1("commit " + decimal_length + NUL + payload) == git_commit`, its unique tree header equals the computed tree, and its author/committer/parent headers have valid forms. Both IDs are lowercase 40-hex SHA-1 identities. Merely supplying shape-valid IDs fails. The external deployer additionally verifies this commit/tree on GitHub/main; object bytes alone do not prove remote publication or human authorization.

`report` is the existing closed `docs/design/v0.2-a/schemas/review-result.schema.json` format. Only hard `ACCEPT` is allowed, not ACCEPT_WITH_LIMITATIONS/BLOCKED. All owner-required contracts must be PASS with known evidence references; no mandatory open finding is allowed under the existing gate rules. `native_result` and `implementation` are pinned JSON files preserving actual native adapter output with at least:

```json
{"report":{},"raw_report_sha256":"<SHA256(canonical(report))>","session_id":"<actual UUID>","requested_model":"<actual requested model>","requested_effort":"<actual requested effort>","resolved_model":"unknown","resolved_effort":"unknown","process":{"exit":0,"timeout":false,"cancelled":false,"overflow":false}}
```

For review, the embedded report must equal the separately pinned raw report. For implementation, report.status must be IMPLEMENTED. The session UUIDs must be distinct and equal those pinned by the owner. Requested model/effort cannot be unknown; resolved values stay unknown unless actual provenance proves them. Gate.report_sha256 hashes the raw **report file bytes**, not the canonical embedded object. Gate.review pins the review receipt. All native, Gate and verification processes must actually exit integer 0 without timeout/cancel/overflow.

`verification` uses existing mechanical Gate records: each has `id`, `passed`, `run_id`, `round`, `candidate_sha256`, `acceptance_sha256`, `counts: {tests,failures,errors,skipped}`, and `process`. IDs must exactly cover owner.verification_ids. Counts are integers with tests >= 1 and failures/errors/skipped = 0. Replaying the existing gate's consistency predicates inside the loader only checks this external evidence; it does not replace the fixed external Gate or issue a new acceptance.

## Observed CLI invocation binding (required)

Old Stable adapter native result files do **not** contain provider/role/executable fields. Preserve their exact original bytes and SHA, including all process/transport diagnostics. Do not add fields, re-serialize, or replace them to satisfy this protocol. The fixed external CLI supervisor separately emits the following closed version-1 attestation from its actual CLI selection, configured request, executable/argv/kernel-process observation, and returned session. Candidate code never emits a real attestation.

```json
{
  "version": 1, "kind": "release-invocation",
  "identity": {"run_id":"<run>","round":1,"candidate_sha256":"<candidate SHA256>","acceptance_sha256":"<contract SHA256>"},
  "supervisor": {"path":"/external/SYNTHETIC/fixed-supervisor.py","sha256":"<code SHA256>"},
  "result": {"path":"/external/SYNTHETIC/original-native-result.json","sha256":"<EXACT original file SHA256>"},
  "binding": {
    "provider":"claude", "role":"reviewer",
    "executable":{"path":"/external/SYNTHETIC/actual-cli-entrypoint","sha256":"<executable SHA256>"},
    "requested_model":"<exact approved opaque proxy alias>","requested_effort":"<exact approved effort>",
    "session_id":"<actual canonical UUID>"
  },
  "process":{"exit":0,"timeout":false,"cancelled":false,"overflow":false}
}
```

This schematic contains invalid placeholders and is not an approval. `process` must equal the original result's **entire captured process object**, including any extra diagnostics, not merely the four schematic fields. `identity` and `supervisor` must equal the owner/Gate bindings. `result` must equal review.native_result or Gate.implementation, including the exact original path AND SHA. `binding` is exactly the six fields shown; its executable Ref is the canonical absolute, non-symlink CLI entrypoint actually selected by the supervisor (e.g. the configured CLI program, even if the kernel runs its shebang interpreter). The supervisor must establish that entrypoint/argv and requested configuration were really used; a name or release label is not an observation. Physical backend and effective effort remain unknown unless independently proved.

The owner additionally supplies two closed maps:

- `native_bindings: {"reviewer": Binding, "implementer": Binding}`: the separately approved exact invocation requests, including returned session IDs and executable Refs.
- `invocations: {"reviewer": Ref, "implementer": Ref}`: immutable observation files. review.invocation equals owner.invocations.reviewer; Gate.implementation_invocation equals owner.invocations.implementer.

This release protocol requires **Codex/provider codex with role implementer**, and a **distinct Claude/provider claude with role reviewer**. Both attestation Bindings must equal their owner-authorized Binding, the observed process/result must match the unchanged native envelope, and model/effort/session must match exactly. review.provider must be claude and match the actual reviewer invocation. Required roles cannot be swapped even if all outer hashes are re-pinned.

Model names are opaque strings. There is no family/prefix classification, model-name allowlist or catalog/cache inference: an explicitly approved alias with any spelling is accepted when the observed request and original native receipt agree. Resolved model/effort remain separately reported; they are not inferred from the alias. If an original result already contains provider/role/requested_provider/requested_role/model/effort/executable/executable_sha256 or source identity claims, including in optional binding/identity objects, contradictions are refused. A string executable claim must equal the canonical Ref path; a Ref claim must equal the complete Ref.

All new observation files are private authority: regular-file, SHA, replacement, overlap and mount checks apply exactly as for existing proof files. The referenced executable is streamed and re-hashed (it may exceed the 2 MB authority-file budget), must have an executable mode, and cannot also serve as an authority proof file. Its code may be shared in readonly native/dependency mounts. It remains forbidden in writable workspaces, storage/native homes and external write roots. Changes to executable bytes/mode/inode are refused on the next operator check. This distinction preserves shared readonly code without exposing private attestation files.

Pre-binding v2 proof graphs cannot launch new v3 Stable; their missing invocation evidence fails closed. Version-1 legacy Usable/Studio records retain their existing compatibility. No existing real native result, authorization, or old Stable adapter is modified.

## Synthetic example and adoption

This schematic is **SYNTHETIC, contains invalid placeholders, and is not an approval**:

```json
{
  "version": 2,
  "root": "/external/SYNTHETIC/immutable-install",
  "git_commit": "<raw commit object SHA1>",
  "git_tree": "<complete frozen tree SHA1>",
  "candidate_sha256": "<SHA256(canonical(files))>",
  "files": [{"path":"example.txt","mode":"100644","size":1,"sha256":"<file SHA256>"}],
  "provenance": {
    "contract": {"path":"/external/SYNTHETIC/contract.txt","sha256":"<contract SHA256>"},
    "review": {"path":"/external/SYNTHETIC/review-receipt.json","sha256":"<review receipt SHA256>"},
    "gate": {"path":"/external/SYNTHETIC/gate-receipt.json","sha256":"<Gate receipt SHA256>"},
    "owner": {"path":"/external/SYNTHETIC/owner-release.json","sha256":"<owner SHA256>"},
    "commit": {"path":"/external/SYNTHETIC/commit.raw","sha256":"<raw commit SHA256>"}
  }
}
```

A complete runnable synthetic graph is generated only in temporary test directories by `tests/stable_provenance_fixture.py`; no product API writes evidence. Its records are all named SYNTHETIC and never constitute real acceptance. External deployment emits the same schema from actual preserved report/native/Gate/user-grant bytes, in dependency order: raw contract/grant/supervisor/commit/report/original native results; separately observed invocation attestations; review receipt; Gate receipt; owner record; release record; finally the externally pinned operator binding `stable: {record, sha256}`. Do not edit or re-serialize original raw evidence to manufacture a desired result.

Release root, record, every proof file, operator and policy remain protected from writable workspaces, storage/native homes and external write roots. Proof/authority bytes cannot be native credential overlays, native read mounts or external source/dependency mounts. Shared readonly executable/code dependencies remain supported when they do not expose authority evidence. Detached flow/session workers and PTY entry reconstruct the same pinned WorkbenchRuntime; proof drift refuses launch. Public status/header expose only verified channel, candidate and Git IDs, never evidence or authorization paths. Existing storage/HOME/cwd/UUID are not migrated or deleted.
