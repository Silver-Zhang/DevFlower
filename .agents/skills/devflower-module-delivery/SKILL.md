---
name: devflower-module-delivery
description: Use when planning, implementing, reviewing or integrating a DevFlower V2 feature module; minimize context switching, branch proliferation, repeated CI and agent chatter while preserving evidence and safety.
---

# DevFlower module delivery

Use this for a meaningful module, not for every one-line change. Keep messages short and stateful.

1. **One module, one owner**. A defines a concise user-facing scenario and API boundary (max one page), B owns product changes, C tests at module exit. D intervenes only for blockers or major deviations.
2. **Write a small contract**: target behaviors, existing V1 source to reuse, V2 invariants, acceptance commands, rollback path. Do not write a new long PRD for each tiny task.
3. **Prefer an existing integration branch**. B makes local commits and runs targeted tests; no task branch unless concurrent writers need isolation. Never force-push or rewrite protected history.
4. **CI budget**: preflight locally at each logical change; trigger remote full CI once after an integrated module candidate is ready, plus once after substantive fixes if needed. Security-critical changes may require immediate targeted gate; do not disable CI to satisfy a numerical budget.
5. **No micro-agent ping pong**. A delegates one coherent implementation batch. B handles local bugs with focused diagnostics. C receives a pinned, reviewable candidate once; consolidate findings by root cause.
6. **Two-cycle rule**: after two unsuccessful attempts without new evidence, stop patching and change the hypothesis or design. Record one concise decision, not repeated discussion.
7. **Evidence before status**: record exact SHA/tree, test command and exit code, scope of synthetic/real tests, C verdict, user-visible behavior. Never turn a green synthetic CI into a real-model acceptance claim.
8. **Exit**: only declare module finished after UI user path works, targeted tests pass, root-cause fixes are reviewed, and legacy feature parity rows are updated. No extra design polish unless a proven usability failure blocks the path.
