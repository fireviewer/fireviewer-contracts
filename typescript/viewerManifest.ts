/**
 * DTO réseau public de GET /api/v1/incident/{fire_id}/manifest.
 *
 * Ce contrat est volontairement distinct de `IncidentData`, qui alimente la
 * démonstration complète. Un manifeste public ne contient ni sources, ni
 * historique, ni journal ; ces données ne doivent jamais être déduites du
 * fixture lorsque les mocks sont désactivés.
 */

export const VIEWER_MANIFEST_SCHEMA_VERSION = '2.0' as const;

export const VIEWER_MANIFEST_FIRE_ID_RE = /^FR-[0-9A-Z]{2,3}-[0-9]{5}$/;
export const VIEWER_MANIFEST_LOCAL_FRAME = 'ENU' as const;
export const VIEWER_MANIFEST_METERS_PER_UNIT = 0.01 as const;
export const VIEWER_MANIFEST_VERTICAL_DATUM = 'EPSG:4979' as const;

const STATUS_CODES = [
  'CANDIDATE',
  'UNDER_REVIEW',
  'ACTIVE_CONFIRMED',
  'MONITORING',
  'EXTINGUISHED',
  'CLOSED',
  'SUSPENDED',
  'REJECTED',
] as const;

const MODEL_STATES = ['available', 'not_available', 'withheld'] as const;
const ASSET_LODS = ['mobile', 'desktop'] as const;

/**
 * Projection publique autorisée par le cycle de vie backend. `TOMBSTONED`
 * n'apparaît pas ici : c'est une visibilité de série qui répond 410 avant
 * qu'un ViewerManifest ne soit sérialisé.
 */
const MODEL_STATES_BY_STATUS: Readonly<
  Record<(typeof STATUS_CODES)[number], readonly (typeof MODEL_STATES)[number][]>
> = {
  CANDIDATE: ['withheld'],
  UNDER_REVIEW: ['withheld'],
  ACTIVE_CONFIRMED: ['available', 'not_available'],
  MONITORING: ['available', 'not_available'],
  EXTINGUISHED: ['available', 'not_available'],
  CLOSED: ['not_available'],
  SUSPENDED: ['withheld'],
  REJECTED: ['withheld'],
};

export type ViewerManifestStatusCode = (typeof STATUS_CODES)[number];
export type ViewerManifestModelState = (typeof MODEL_STATES)[number];
export type ViewerManifestAssetLod = (typeof ASSET_LODS)[number];

export interface ViewerManifestStatus {
  code: ViewerManifestStatusCode;
  validated_at: string | null;
  review_required: boolean;
}

export interface ViewerManifestLocation {
  type: 'Point';
  coordinates: [number, number];
  horizontal_uncertainty_m: number;
  altitude_m: number | null;
  vertical_datum: string | null;
}

export interface ViewerManifestAsset {
  asset_id: string;
  version: number;
  url: string;
  sha256: string;
  size_bytes: number;
  lod: ViewerManifestAssetLod;
}

export interface ViewerManifestSceneFile {
  file_id: number;
  path: string;
  kind: 'COG' | 'JPEG' | 'PNG' | 'GLB' | 'FWTILE' | 'FWTERRAIN';
  url: string;
  sha256: string;
  size_bytes: number;
  media_type: string;
}

export interface ViewerManifestScene {
  package_id: string;
  catalog_url: string;
  files: ViewerManifestSceneFile[];
}

export interface ViewerManifestFrame {
  origin_wgs84: [number, number, number];
  local_frame: typeof VIEWER_MANIFEST_LOCAL_FRAME;
  meters_per_unit: typeof VIEWER_MANIFEST_METERS_PER_UNIT;
  vertical_datum: typeof VIEWER_MANIFEST_VERTICAL_DATUM;
}

export interface ViewerManifestFreshness {
  incident_at: string;
  terrain_source_year: number | null;
  generated_at: string | null;
}

export interface ViewerManifest {
  schema_version: typeof VIEWER_MANIFEST_SCHEMA_VERSION;
  fire_id: string;
  episode_id: string;
  status: ViewerManifestStatus;
  location: ViewerManifestLocation | null;
  asset: ViewerManifestAsset | null;
  scene: ViewerManifestScene | null;
  frame: ViewerManifestFrame | null;
  freshness: ViewerManifestFreshness;
  model_state: ViewerManifestModelState;
  public_notice: string;
}

/**
 * Surface minimale qui peut être affichée avant les endpoints publics dédiés
 * aux Sources, Historique et Journal. Les tableaux vides sont intentionnels.
 */
export interface ViewerManifestSummary {
  schemaVersion: typeof VIEWER_MANIFEST_SCHEMA_VERSION;
  fireId: string;
  episodeId: string;
  statusCode: ViewerManifestStatusCode;
  validatedAt: string | null;
  reviewRequired: boolean;
  location: ViewerManifestLocation | null;
  asset: ViewerManifestAsset | null;
  scene: ViewerManifestScene | null;
  frame: ViewerManifestFrame | null;
  freshness: ViewerManifestFreshness;
  modelState: ViewerManifestModelState;
  publicNotice: string;
  sources: readonly [];
  history: readonly [];
  journal: readonly [];
}

export class ViewerManifestParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ViewerManifestParseError';
  }
}

type JsonRecord = Record<string, unknown>;

function fail(path: string, message: string): never {
  throw new ViewerManifestParseError(`${path} ${message}`);
}

function strictRecord(value: unknown, path: string, keys: readonly string[]): JsonRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return fail(path, 'doit être un objet JSON.');
  }

  const record = value as JsonRecord;
  const unexpected = Object.keys(record).filter((key) => !keys.includes(key));
  const missing = keys.filter((key) => !Object.hasOwn(record, key));
  if (unexpected.length > 0 || missing.length > 0) {
    const details = [
      unexpected.length > 0 ? `clés inattendues : ${unexpected.join(', ')}` : '',
      missing.length > 0 ? `clés absentes : ${missing.join(', ')}` : '',
    ]
      .filter(Boolean)
      .join('; ');
    return fail(path, details);
  }

  return record;
}

function string(value: unknown, path: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    return fail(path, 'doit être une chaîne non vide.');
  }
  return value;
}

function nullableString(value: unknown, path: string): string | null {
  return value === null ? null : string(value, path);
}

function finiteNumber(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return fail(path, 'doit être un nombre fini.');
  }
  return value;
}

function integer(value: unknown, path: string): number {
  const parsed = finiteNumber(value, path);
  if (!Number.isInteger(parsed)) {
    return fail(path, 'doit être un entier.');
  }
  return parsed;
}

function boolean(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') {
    return fail(path, 'doit être un booléen.');
  }
  return value;
}

function enumValue<T extends string>(
  value: unknown,
  path: string,
  values: readonly T[],
): T {
  if (typeof value !== 'string' || !values.includes(value as T)) {
    return fail(path, `doit être l'une des valeurs : ${values.join(', ')}.`);
  }
  return value as T;
}

function tuple(value: unknown, path: string, length: number): number[] {
  if (!Array.isArray(value) || value.length !== length) {
    return fail(path, `doit être un tableau de ${length} nombres.`);
  }
  return value.map((item, index) => finiteNumber(item, `${path}[${index}]`));
}

function boundedNumber(
  value: unknown,
  path: string,
  minimum: number,
  maximum: number,
): number {
  const parsed = finiteNumber(value, path);
  if (parsed < minimum || parsed > maximum) {
    return fail(path, `doit être compris entre ${minimum} et ${maximum}.`);
  }
  return parsed;
}

function parseStatus(value: unknown): ViewerManifestStatus {
  const record = strictRecord(value, 'status', ['code', 'validated_at', 'review_required']);
  return {
    code: enumValue(record.code, 'status.code', STATUS_CODES),
    validated_at: nullableString(record.validated_at, 'status.validated_at'),
    review_required: boolean(record.review_required, 'status.review_required'),
  };
}

function parseLocation(value: unknown): ViewerManifestLocation | null {
  if (value === null) return null;
  const record = strictRecord(value, 'location', [
    'type',
    'coordinates',
    'horizontal_uncertainty_m',
    'altitude_m',
    'vertical_datum',
  ]);
  const coordinates = tuple(record.coordinates, 'location.coordinates', 2);
  if (record.type !== 'Point') {
    return fail('location.type', 'doit être "Point".');
  }
  return {
    type: 'Point',
    coordinates: [coordinates[0], coordinates[1]],
    horizontal_uncertainty_m: finiteNumber(
      record.horizontal_uncertainty_m,
      'location.horizontal_uncertainty_m',
    ),
    altitude_m:
      record.altitude_m === null ? null : finiteNumber(record.altitude_m, 'location.altitude_m'),
    vertical_datum: nullableString(record.vertical_datum, 'location.vertical_datum'),
  };
}

function parseAsset(value: unknown): ViewerManifestAsset | null {
  if (value === null) return null;
  const record = strictRecord(value, 'asset', [
    'asset_id',
    'version',
    'url',
    'sha256',
    'size_bytes',
    'lod',
  ]);
  return {
    asset_id: string(record.asset_id, 'asset.asset_id'),
    version: integer(record.version, 'asset.version'),
    url: string(record.url, 'asset.url'),
    sha256: string(record.sha256, 'asset.sha256'),
    size_bytes: integer(record.size_bytes, 'asset.size_bytes'),
    lod: enumValue(record.lod, 'asset.lod', ASSET_LODS),
  };
}

export function parseViewerManifestScene(value: unknown): ViewerManifestScene | null {
  if (value === null) return null;
  const record = strictRecord(value, 'scene', ['package_id', 'catalog_url', 'files']);
  if (!Array.isArray(record.files) || record.files.length > 2_000) {
    return fail('scene.files', 'doit contenir au maximum 2 000 fichiers.');
  }
  return {
    package_id: string(record.package_id, 'scene.package_id'),
    catalog_url: string(record.catalog_url, 'scene.catalog_url'),
    files: record.files.map((value, index) => {
      const file = strictRecord(value, `scene.files[${index}]`, [
        'file_id', 'path', 'kind', 'url', 'sha256', 'size_bytes', 'media_type',
      ]);
      return {
        file_id: integer(file.file_id, `scene.files[${index}].file_id`),
        path: string(file.path, `scene.files[${index}].path`),
        kind: enumValue(file.kind, `scene.files[${index}].kind`, ['COG', 'JPEG', 'PNG', 'GLB', 'FWTILE', 'FWTERRAIN'] as const),
        url: string(file.url, `scene.files[${index}].url`),
        sha256: string(file.sha256, `scene.files[${index}].sha256`),
        size_bytes: integer(file.size_bytes, `scene.files[${index}].size_bytes`),
        media_type: string(file.media_type, `scene.files[${index}].media_type`),
      };
    }),
  };
}

function parseFrame(value: unknown): ViewerManifestFrame | null {
  if (value === null) return null;
  const record = strictRecord(value, 'frame', [
    'origin_wgs84',
    'local_frame',
    'meters_per_unit',
    'vertical_datum',
  ]);
  const origin = tuple(record.origin_wgs84, 'frame.origin_wgs84', 3);
  const longitude = boundedNumber(origin[0], 'frame.origin_wgs84[0]', -180, 180);
  const latitude = boundedNumber(origin[1], 'frame.origin_wgs84[1]', -90, 90);
  const ellipsoidHeight = finiteNumber(origin[2], 'frame.origin_wgs84[2]');
  if (record.local_frame !== VIEWER_MANIFEST_LOCAL_FRAME) {
    return fail('frame.local_frame', `doit être "${VIEWER_MANIFEST_LOCAL_FRAME}".`);
  }
  if (record.meters_per_unit !== VIEWER_MANIFEST_METERS_PER_UNIT) {
    return fail(
      'frame.meters_per_unit',
      `doit être ${VIEWER_MANIFEST_METERS_PER_UNIT} (100 unités Unity par mètre).`,
    );
  }
  if (record.vertical_datum !== VIEWER_MANIFEST_VERTICAL_DATUM) {
    return fail('frame.vertical_datum', `doit être "${VIEWER_MANIFEST_VERTICAL_DATUM}".`);
  }
  return {
    origin_wgs84: [longitude, latitude, ellipsoidHeight],
    local_frame: VIEWER_MANIFEST_LOCAL_FRAME,
    meters_per_unit: VIEWER_MANIFEST_METERS_PER_UNIT,
    vertical_datum: VIEWER_MANIFEST_VERTICAL_DATUM,
  };
}

function parseFreshness(value: unknown): ViewerManifestFreshness {
  const record = strictRecord(value, 'freshness', [
    'incident_at',
    'terrain_source_year',
    'generated_at',
  ]);
  return {
    incident_at: string(record.incident_at, 'freshness.incident_at'),
    terrain_source_year:
      record.terrain_source_year === null
        ? null
        : integer(record.terrain_source_year, 'freshness.terrain_source_year'),
    generated_at: nullableString(record.generated_at, 'freshness.generated_at'),
  };
}

function validateModelStateInvariants(manifest: ViewerManifest): void {
  if (manifest.model_state === 'available') {
    if (!manifest.location || (!manifest.asset && !manifest.scene) || !manifest.frame) {
      fail('model_state', '"available" exige location, frame et asset ou scene.');
    }
    return;
  }

  if (manifest.model_state === 'not_available') {
    if (!manifest.location || manifest.asset || manifest.scene || manifest.frame) {
      fail('model_state', '"not_available" exige location et asset/scene/frame à null.');
    }
    return;
  }

  if (manifest.location || manifest.asset || manifest.scene || manifest.frame) {
    fail('model_state', '"withheld" exige location, asset, scene et frame à null.');
  }
}

function validateLifecycleInvariants(manifest: ViewerManifest): void {
  const allowedModelStates = MODEL_STATES_BY_STATUS[manifest.status.code];
  if (!allowedModelStates.includes(manifest.model_state)) {
    fail(
      'status.code',
      `"${manifest.status.code}" ne peut pas être associé à model_state "${manifest.model_state}".`,
    );
  }
}

/** Parse et valide strictement le JSON réseau du manifeste public. */
export function parseViewerManifest(value: unknown): ViewerManifest {
  const record = strictRecord(value, 'manifest', [
    'schema_version',
    'fire_id',
    'episode_id',
    'status',
    'location',
    'asset',
    'scene',
    'frame',
    'freshness',
    'model_state',
    'public_notice',
  ]);
  if (record.schema_version !== VIEWER_MANIFEST_SCHEMA_VERSION) {
    return fail('schema_version', `doit être "${VIEWER_MANIFEST_SCHEMA_VERSION}".`);
  }

  const fireId = string(record.fire_id, 'fire_id');
  if (!VIEWER_MANIFEST_FIRE_ID_RE.test(fireId)) {
    return fail('fire_id', 'ne respecte pas ^FR-[0-9A-Z]{2,3}-[0-9]{5}$.');
  }

  const manifest: ViewerManifest = {
    schema_version: VIEWER_MANIFEST_SCHEMA_VERSION,
    fire_id: fireId,
    episode_id: string(record.episode_id, 'episode_id'),
    status: parseStatus(record.status),
    location: parseLocation(record.location),
    asset: parseAsset(record.asset),
    scene: parseViewerManifestScene(record.scene),
    frame: parseFrame(record.frame),
    freshness: parseFreshness(record.freshness),
    model_state: enumValue(record.model_state, 'model_state', MODEL_STATES),
    public_notice: string(record.public_notice, 'public_notice'),
  };
  validateModelStateInvariants(manifest);
  validateLifecycleInvariants(manifest);
  return manifest;
}

/**
 * Adapte uniquement les informations explicitement publiques du manifeste.
 * Les vues riches devront employer leurs futurs endpoints dédiés.
 */
export function toViewerManifestSummary(manifest: ViewerManifest): ViewerManifestSummary {
  return {
    schemaVersion: manifest.schema_version,
    fireId: manifest.fire_id,
    episodeId: manifest.episode_id,
    statusCode: manifest.status.code,
    validatedAt: manifest.status.validated_at,
    reviewRequired: manifest.status.review_required,
    location: manifest.location,
    asset: manifest.asset,
    scene: manifest.scene,
    frame: manifest.frame,
    freshness: manifest.freshness,
    modelState: manifest.model_state,
    publicNotice: manifest.public_notice,
    sources: [],
    history: [],
    journal: [],
  };
}
