# Incident revisions v1

Status: additive implementation, not a production migration or scientific qualification.

The canonical identity is an incident and a versioned state at an instant. Episode identities
are preserved. A local day is a query/protocol, not the primary key of the new state.

## Time and immutable history

- All instants require a timezone and normalize to UTC. State validity is `[valid_from, valid_until)`.
- `observed_at` describes the phenomenon. An uncertain interval retains its bounds; unknown time
  stays unknown and cannot participate in spatial fusion. No midnight is fabricated.
- `published_at` and `available_at` describe the source. `retrieved_at` describes collection;
  `recorded_at` is assigned by the backend to a newly accepted evidence version.
- Knowledge of a version starts at the latest of these availability/recording instants. Historical
  corpus visibility remains a separately labelled Part.4 protocol, not an imported system timestamp.
- Causal requests set `knowledge_cutoff == valid_at`. Retrospective requests permit a later cutoff.
  A late photograph can revise retrospective history without changing a frozen causal result.
- `calculated_at` is the actual calculation time. It is never evidence availability.
- At a cutoff, select the latest proof version known by that cutoff, then apply its admissibility.
  A withdrawal is a new version, not a deletion. Old versions remain addressable.
- `parent_revision_id` records a computational dependency. `supersedes_revision_id` records a
  correction of the same effective instant and mode. Neither link rewrites the older record.
- At a given effective instant, the largest knowledge cutoff wins, then calculation time and
  revision identity break ties. A later calculation with less knowledge is a historical variant;
  it does not supersede the more informed state. `as_of` first limits recorded calculations and
  decision events, and causal/retrospective modes are selected explicitly.
- Review/publication fields are projections of separate immutable decision events. Persisted
  calculation payloads start pending/unpublished. Approval is never inherited by a replacement.
- An open validity end does not mean indefinitely observed activity. Expiry can request a new
  calculation without new evidence. Public views retain the exact approved revision and timestamp.

## Contracts and ownership

`TemporalEvidence` records versioned admissible inputs. `IncidentUpdateRequest` records an
explicit cutoff, mode, trigger, optimistic predecessor and idempotency key.
`IncidentStateRevision` records outputs and exact input identities, profile and spatial context.
`IncidentUpdateResult` distinguishes completion from missing initialization, evidence or enrichment.
Waiting is not successful reconstruction and never produces a fabricated geometry.

Backend owns storage, jobs, authorization and decisions. Fire State owns deterministic fusion.
Orchestrator selects enrichment needs. Map Builder and terrain publication are unaffected.
`observable`, probability and provenance grids remain distinct from active/affected geometry.
Calibration state and calculation limitations travel with the result. Human approval does not
turn an uncalibrated model into a calibrated one.

## Compatibility and rollout

Daily APIs and benchmark protocols remain available. Daily cutoff uses the requested IANA timezone
and the next local midnight (exclusive), including DST days of 23 or 25 hours. Existing Part.4 daily
artifacts and hashes remain immutable. The old frozen protocol is not silently relabelled continuous.
The continuous fusion uses explicit instants and retains an internal Part.4 checkpoint adapter for
probability/provenance raster restoration. It does not require filling intervening calendar days.

Retire a daily business consumer only after its replacement is exercised. Research/export adapters
are intentionally retained. Deployment, real-data acceptance and scientific calibration are separate.

## Current implementation boundary

The backend accepts normalized versioned proofs under the private incident API, atomically queues
CPU reconstruction, and exposes revision history, a daily snapshot, review and publication events.
`fire-viewer-incident-updates --maximum-jobs 10` drains those durable jobs. A correction replays from
the dated seed at each impacted instant, including pending targets. This conservative approach
does not use an invalid carried parent and deliberately makes no early-stop equivalence claim.
Seed replacement queues a replay for existing temporal work. Worker rollback leaves its job queued;
content-addressed raster writes may be retried. Waiting receipts preserve their original cutoff.

With `FV_INCIDENT_REVISIONS_ENABLED=true`, media enqueue and dispatch claim reserve a pending proof
in the same database transaction. The existing conditional worker pipeline remains responsible for
vision, transcription, OCR and geolocation. Validated completion appends a version with its receipt
hash and actual processing steps; a retry, manual rejection or withdrawal never silently revives an
obsolete proof. Unsupported geometry after successful processing produces `enrichment_state=abstained`, not an invented point. Worker failures without usable observations remain `failed` and block fusion.
Known-time abstentions do not block other usable proofs; unknown observation times still wait.

External spatial claims, accepted satellite receipts and localized event candidates are connected
to the same versioned inputs. Satellite coverage may update observability without asserting absent
fire. Artifact retraction, rejected media proposals, consent withdrawal and expiry append corrections
or withdrawals. Public derivatives require the contributing proof's publication rights independently
of permission to analyze it privately. Historical records are not bulk backfilled by enabling the flag.

Active positive observations schedule eight finite half-life checkpoints from the observation end,
using the `part4-framed-v1` 3.3.0 profile. `run_after` prevents execution before each cutoff. Late results
make elapsed checkpoints immediately eligible; each job resolves proof versions at its cutoff.
Ageing does not erase affected area or automatically publish a new state. Resident/hosted dispatcher
ticks drain one CPU update and scan at most 100 expired media rights. An authenticated CPU-only cron
endpoint is also available; deployment must arrange its invocation if no dispatcher is running.
No deployed scheduler or model was activated during local qualification. Daily producers remain active.

The frontend is opt-in with `VITE_FV_INCIDENT_REVISIONS_ENABLED=true`. It integrates revision
records into the existing saved layers, proof-reference table, spatial publication card and Atlas
timeline. The web shell and map renderers stay in place. Review/publication belong to the private
web administration; the Android application has no administration workspace and is unchanged.
The Atlas requests the incident associated with its territory, refuses a foreign response, and
preserves holes and disjoint components in computed geometry. Historic daily readers remain usable.

## Acceptance cases

| Case | Required behavior |
| --- | --- |
| Two observations within one day | Two independently addressable state revisions |
| Late evidence | Revised retrospective state; unchanged frozen causal/published state |
| Correction or withdrawal | Latest version selected by knowledge cutoff; affected history replayed |
| Duplicate delivery | Same receipt, no extra revision or double-counting |
| Concurrent completion | Optimistic conflict, no lost revision |
| Crash/retry | Durable request can resume; immutable inputs and result identities retained |
| No new evidence | Explicit expiry update may age active knowledge without growing affected area |
| Unknown/interval time | Unknown waits; interval evidence is not admitted before its end |
| DST midnight | Daily view honors the local interval rather than adding 24 hours |
| Missing initialization | Explicit waiting result, no invented perimeter |
| Worker completion or abstention | New proof version with actual receipt; no fabricated location/time |
| Retry after failure | One current result; obsolete callbacks cannot replace a manual correction |
| Consent expiry or artifact retraction | Withdraw inputs and replay; preserve immutable published history |
| Private analysis rights only | Draft calculation allowed; public derivative publication rejected |
| Scheduled ageing | No execution before cutoff; finite checkpoints independent of midnight |
| Pilot disabled | Existing frontend remains visible and makes no revision/proof requests |
| Review/publication | Exact revision approved; correcting it does not transfer approval |
| Compatibility | Same frozen daily inputs retain historical numerical/provenance behavior |
