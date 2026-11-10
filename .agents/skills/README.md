# DevFlower V2 project-local engineering skills

These are **project-scoped Codex skills**, placed under `.agents/skills/`. They do not install a plugin, executable, or model. New Codex sessions opened at this repo should discover them according to the installed Codex version; verify using the client's skill listing before claiming activation.

- `devflower-v1-port`: use when implementing a feature whose V1 code already exists. Preference: port → thin adapter → focused refactor → rewrite.
- `devflower-module-delivery`: use for entire feature-module batches; restrict long handoffs, per-issue branching, repeated full CI.
- `frontend-design`: official upstream Anthropic skill for front-end design/review. Use only when UI work is in scope; DevFlower's existing design brief, Sim-workflow UX, accessibility and runtime correctness take precedence over generic aesthetics.

Sources: project skills authored for DevFlower (2026-11-10). Third-party frontend skill copied from `anthropics/skills`, `skills/frontend-design`, GitHub blob `a5333457c414d20d625f307df945842c0952ecc3`; LICENSE.txt blob `f433b1a53f5b830a205fd2df78e2b34974656c7b` (Apache-2.0). No executable scripts, network installers, hooks or credentials.

Usage: A and B read `devflower-module-delivery`; B reads `devflower-v1-port` when porting; B/C read `frontend-design` for real UI design/review only. D needs no extra installed skill to monitor requirements and evidence. Do not load all three for every microtask.

Before use, check which project-level skill directories your Codex build recognizes. Existing running sessions may require session restart/refresh to discover newly committed skills.
