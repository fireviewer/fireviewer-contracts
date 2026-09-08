# FireViewer geographic contracts

Normative rules only: horizontal/vertical reference, units, coordinate order,
time convention and information classes. No terrain, map, cache, asset, solver
output, credentials or production service belongs here.

The web worker embeds a versioned snapshot, so its installation and execution
do not depend on this checkout or the Unreal builder. The Unreal roadmap and
implementation are not modified by creating this first contract.

`geo-contract.v1.json` currently describes mainland France only. It is not a
worldwide datum resolver or an exhaustive provenance schema. Its canonical
JSON digest is recorded by the Web worker in each build manifest.
