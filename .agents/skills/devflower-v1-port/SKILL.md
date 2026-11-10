---
name: devflower-v1-port
description: Use when restoring V1 features in DevFlower V2; examine working V1 source first and prefer direct port or thin adapter over reimplementation, with explicit V2 safety/data boundaries and focused tests.
---

# Port V1 behavior into V2 without dragging V1 architecture

Goal: recover user-visible features quickly and correctly. Reuse before rewrite; do not overengineer abstractions.

1. Read the matching row in `docs/v2/FEATURE_PARITY.md`; capture the exact V1 file, old behavior and V2 target. If missing, add a row before coding.
2. Inspect actual V1 source and its tests, not just README. Identify the smallest reusable pure function, parser, component or adapter. Preserve already-working logic unless evidence shows incorrect behavior.
3. Choose the **least invasive** option in this order: DIRECT_PORT (standalone utility) → THIN_ADAPTER (old logic behind V2 interface) → LOCAL_REFACTOR (change ownership/data model) → REWRITE (only with documented architectural conflict).
4. Prevent accidental multiple authorities: V2 owns draft, frozen definition, run, activation, event store, permissions and session identities. Imported V1 code cannot create its own parallel authoritative runtime or database.
5. Treat native history, secrets, process identity, approvals, idempotency and UNKNOWN outcomes as hard safety invariants. Do not bypass Provider capability checks or call a fake transport 'real'.
6. Adapt V1 UI to V2 component contracts and state ownership rather than copy the entire `workbench_ui/index.jsx` and its large App component.
7. Implement one vertical slice with focused unit/API/browser evidence, then an actual user scenario. Only change tests when a changed requirement is approved; never delete assertions to green CI.
8. Record a seven-line handoff: feature ID, V1 source, V2 target, chosen port strategy, interfaces altered, tests/exit codes, remaining gaps. Avoid a new Issue or branch if the existing module delivery owns it.
