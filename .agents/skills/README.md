# DevFlower V2 project-local engineering skills

These are **project-scoped Codex skills**, placed under `.agents/skills/`. They do not install a plugin, executable, or model. New Codex sessions opened at this repo should discover them according to the installed Codex version; verify using the client's skill listing before claiming activation.

- `devflower-v1-port`: use when implementing a feature whose V1 code already exists. Preference: port → thin adapter → focused refactor → rewrite.
- `devflower-module-delivery`: use for entire feature-module batches; restrict long handoffs, per-issue branching, repeated full CI.
- `frontend-design`: official upstream Anthropic skill for front-end design/review. Use only when UI work is in scope; DevFlower's existing design brief, Sim-workflow UX, accessibility and runtime correctness take precedence over generic aesthetics.

Sources: project skills authored for DevFlower (2026-11-10). Third-party frontend skill copied from `anthropics/skills`, `skills/frontend-design`, GitHub blob `a5333457c414d20d625f307df945842c0952ecc3`; LICENSE.txt blob `f433b1a53f5b830a205fd2df78e2b34974656c7b` (Apache-2.0). No executable scripts, network installers, hooks or credentials.

Usage: A and B read `devflower-module-delivery`; B reads `devflower-v1-port` when porting; B/C read `frontend-design` for real UI design/review only. D needs no extra installed skill to monitor requirements and evidence. Do not load all three for every microtask.

Before use, check which project-level skill directories your Codex build recognizes. Existing running sessions may require session restart/refresh to discover newly committed skills.

## Additional verified project skills (2026-11-10)

- `systematic-debugging` — `obra/superpowers`, SKILL blob `62fc752aaef59a8bbed5d305f4cadc60f8ae07ed`, root MIT license `abf0390320aa14406af7a520b9b0739fdda9bf08`. B uses for reproducible defects; A reviews only when root-cause analysis is unresolved. Supporting reference files and optional `find-polluter.sh` are vendored; no script executes at installation.
- `verification-before-completion` — `obra/superpowers`, SKILL blob `7d45333cc4a49c57a80df6c1fe2fa777a207afbc`, MIT. A/C use to tie completion claims to exact commit, UI path, fresh evidence, and test scope. **Verification is proportional**: focused local tests per fix, one full CI per integrated module candidate, never a full remote CI per micro-edit.
- `webapp-testing` — `anthropics/skills`, SKILL blob `4726215301db64a0cc4d41fc3219c61f37a30f4a`, Apache-2.0 license blob `4f881c52d1f72f4cfb720e339e2d35c3058d01a9`. C uses for browser user journeys and screenshots, reusing existing `tests/v2_*browser*` infrastructure where possible, not replacing it. Optional `scripts/with_server.py` is included but uses `subprocess.Popen(shell=True)`; only run literal trusted commands in a disposable test workspace, never on secrets or production state. Examples contain illustrative paths: adjust them before use.

## Lean usage protocol

1. Read only the skill needed for the current stage. A+B: module delivery; B: V1 port; B on repeatable defects: debugging; A/C at module gate: verification; C for UI testing: webapp-testing; B/C for visual design only: frontend-design.
2. Limit any single defect to two attempts without new evidence; then change diagnosis, isolate, or escalate. Do not let `systematic-debugging` expand every trivial fix into a broad audit.
3. A green synthetic GitHub Actions run does not prove real models, working native sessions, or end-user usability. Report `synthetic / real-model / manually verified` separately.
4. No automatic CI disablement, no bypass of security/permission tests. Run focused tests locally and let a single integration push trigger the existing workflow; rerun only for substantive fixes or required gates.
5. Do not install other toolchains or run external scripts just to discover Skills. Codex/Claude project skill discovery remains client/version dependent: inspect the client's actual skill list. Codex project path is `.agents/skills/`; Claude's equivalent discovery must be checked separately.
6. Sources above are pinned by verified blob SHA. Bundled scripts and examples are **copied, not executed** by this commit. Preserve their licenses and inspect helper commands before use.
