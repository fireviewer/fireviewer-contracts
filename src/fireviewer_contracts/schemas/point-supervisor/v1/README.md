# Point supervisor contracts v1

These JSON Schemas are generated from the frozen Pydantic contracts:

- `fireviewer.point-evidence-bundle.v1` is the bounded, evidence-backed input for one
  existing spatial hypothesis computed upstream by the deterministic registration chain;
- `fireviewer.point-assessment.v1` is an advisory evidence-consistency assessment for that
  immutable point. Its `accept/reject/abstain` value can drive internal ranking, filtering and
  recalculation requests, but it is not itself a coordinate mutation or publication decision.

Both contracts explicitly forbid geometry mutation. A previous published perimeter may only
appear as a digest-qualified, read-only `PriorFireStateReference`; neither schema contains a
map, polygon, or perimeter output. Human review remains the gate for the final retained/public
state unless the deterministic publication policy receives an `accept` assessment with calibrated
confidence strictly greater than `0.85`. A score equal to `0.85` is held for review.

Each correction is a standalone `fireviewer.competing-point-correction.v1` JSON document. It may
contain an alternative `CandidatePoint`, but it has its own identifier, references the immutable
source through `source_point_id` and `source_bundle_sha256`, and is always marked
`relationship=competes_with_source` with `source_mutation_allowed=false`. It must pass through the
same assessment and publication policy as the original point.

Regenerate and verify the snapshots from the repository root:

```powershell
uv run python tools/export_point_supervision_schemas.py
uv run pytest -q tests/test_mvp_point_supervision_schemas.py
```
