# Demo dataset v1

These artifacts describe the repository's synthetic public-contract demo,
`FR-83-00042`. The identifier, names, coordinates, and dates are fictional and
do not represent a real fire or operational location.

- `seed-manifest.json` is the Viewer Manifest v2 produced from a clean database
  after running `fire-viewer-seed` with the default public settings.
- `seed-manifest.sha256` contains the SHA-256 of the canonical compact JSON with
  sorted keys. The same value is used as the strong ETag without HTTP quotes.
- `visibility-matrix.json` locks the projections allowed by the public state
  machine. Entries ending in `.invalid` do not represent files or downloads.

The reference seed publishes no spatial asset, so its manifest is
`not_available`. `available` fixtures exist only to validate the contract with
fictional metadata; they are not production artifacts.
