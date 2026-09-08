# Vendored spatial contract v1

These artifacts describe a fictional, versioned FireViewer spatial profile.
They contain no real terrain, incident, GLB asset, or PNG preview.

| Artifact | Purpose |
| --- | --- |
| `spatial-contract.schema.json` | JSON Draft 2020-12 schema for the compatibility fixtures. |
| `fixtures/enu-unity-points.json` | ENU → glTF → Unity and round-trip control vectors. |
| `fixtures/zone-registry.json` | Reusable, versioned zones limited to their declared footprints. |
| `fixtures/zone-revision.json` | A new zone revision that does not mutate its predecessor. |
| `fixtures/spatial-snapshot.json` | Immutable snapshot tied to one incident-manifest revision and its PNG archive. |

## Locked conventions

- The profile covers local rural mainland France. Corsica and overseas regions
  are outside this RAF20/NGF-IGN69 profile.
- Origins use WGS 84 3D / `EPSG:4979`.
  `origin_wgs84` is always
  `[longitude_deg, latitude_deg, ellipsoidal_height_m]`.
- The vertical source is `NGF-IGN69`, converted locally through RAF20. The grid
  is pinned to its official URI and SHA-256; transformations download nothing.
- ENU is physical and metric. GLB stores `(E, U, -N)` in metres with
  `gltf_meters_per_unit = 1.0`. Unity represents `(100E, 100U, 100N)` and its
  manifest therefore exposes `meters_per_unit = 0.01`.
- A PNG belongs to one manifest-revision snapshot archive, not to every active
  zone. URI, hash, dimensions, and production time identify it.
- No globe, tile runtime, or Cesium integration belongs to this profile.

The current producer contracts live in
[`fireviewer-spatial`](https://github.com/fireviewer/fireviewer-spatial/tree/main/contracts/spatial/v1).
This vendored v1 copy is a compatibility fixture. Its control values are not a
geographic data source or an event-localisation attempt.
