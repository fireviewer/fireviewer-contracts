/** Versioned incident contracts. Instants are timezone-qualified ISO 8601 strings. */
export type ReconstructionMode = 'causal' | 'retrospective';
export interface EvidenceRevisionRef {
  readonly evidence_id: string;
  readonly revision: number;
  readonly content_sha256: string;
}
export interface TemporalEvidence extends EvidenceRevisionRef {
  readonly schema: 'fireviewer.temporal-evidence.v1';
  readonly incident_id: string;
  readonly episode_id: string | null;
  readonly source_id: string;
  readonly license: string;
  readonly access: 'private' | 'public';
  readonly public_derivative_allowed: boolean;
  readonly admissibility: 'pending' | 'admitted' | 'rejected' | 'withdrawn';
  readonly media_kind: 'geometry' | 'image' | 'video' | 'text' | 'audio' | 'satellite';
  readonly observed_at: string | null;
  readonly observed_until: string | null;
  readonly time_precision: 'instant' | 'interval' | 'unknown';
  readonly published_at: string | null;
  readonly available_at: string | null;
  readonly retrieved_at: string;
  readonly recorded_at: string;
  readonly observations: readonly Readonly<Record<string, unknown>>[];
  readonly needs_human_review: boolean;
  readonly supersedes_revision: number | null;
  readonly enrichment_state: 'pending' | 'completed' | 'abstained' | 'failed';
  readonly enrichment_receipt_sha256: string | null;
  readonly processing: readonly ProcessingStep[];
}
export interface IncidentGeometry {
  readonly type: 'Polygon' | 'MultiPolygon';
  readonly coordinates: readonly unknown[];
}
export interface IncidentStateRevision {
  readonly schema: 'fireviewer.incident-state-revision.v1';
  readonly revision_id: string;
  readonly incident_id: string;
  readonly episode_id: string;
  readonly valid_from: string;
  readonly valid_until: string | null;
  readonly knowledge_cutoff: string;
  readonly reconstruction_mode: ReconstructionMode;
  readonly calculated_at: string;
  readonly parent_revision_id: string | null;
  readonly supersedes_revision_id: string | null;
  readonly affected_geometry: IncidentGeometry | null;
  readonly active_geometry: IncidentGeometry | null;
  readonly uncertainty_geometry: IncidentGeometry | null;
  readonly observable_fraction: number | null;
  readonly probability_grid: Readonly<Record<string, unknown>> | null;
  readonly provenance_grid: Readonly<Record<string, unknown>> | null;
  readonly evidence_refs: readonly EvidenceRevisionRef[];
  readonly algorithm_id: string;
  readonly algorithm_revision: string;
  readonly fusion_profile_sha256: string;
  readonly spatial_context_sha256: string;
  readonly seed_id: string;
  readonly source_input_sha256: string;
  readonly quality: 'observed' | 'fused' | 'interpolated' | 'insufficient';
  readonly calibration_state: 'uncalibrated' | 'calibrated';
  readonly limitations: readonly string[];
  readonly human_review_state: 'pending' | 'approved' | 'rejected';
  readonly publication_state: 'unpublished' | 'published' | 'withdrawn';
}
export interface IncidentUpdateRequest {
  readonly schema: 'fireviewer.incident-update-request.v1';
  readonly incident_id: string;
  readonly episode_id: string;
  readonly trigger: 'evidence' | 'correction' | 'withdrawal' | 'expiry' | 'manual';
  readonly valid_at: string;
  readonly knowledge_cutoff: string;
  readonly reconstruction_mode: ReconstructionMode;
  readonly expected_current_revision_id: string | null;
  readonly changed_evidence_refs: readonly EvidenceRevisionRef[];
  readonly idempotency_key: string;
}
export interface ProcessingStep {
  readonly component: 'vision' | 'ocr' | 'transcription' | 'geolocation' | 'supervisor' | 'fusion';
  readonly status: 'required' | 'completed' | 'not_applicable' | 'blocked';
  readonly reason: string;
}
export interface IncidentUpdateResult {
  readonly schema: 'fireviewer.incident-update-result.v1';
  readonly incident_id: string;
  readonly status: 'created' | 'unchanged' | 'awaiting_initialization' | 'awaiting_evidence' | 'awaiting_enrichment';
  readonly revision: IncidentStateRevision | null;
  readonly affected_from: string | null;
  readonly affected_until: string | null;
  readonly recalculated_revision_ids: readonly string[];
  readonly processing: readonly ProcessingStep[];
}
