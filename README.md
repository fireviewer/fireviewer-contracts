# fireviewer-contracts

Versioned evidence and reconstruction contracts and bounded backend clients.

Python package: `fireviewer_contracts`. Version: `0.1.0`.

## Installation

Install the versioned release wheels (including private FireViewer dependencies) from the release bundle. No sibling source checkout is required.

```sh
python -m pip install --find-links /path/to/release/wheels fireviewer-contracts==0.1.0
python -m pytest tests -q
```

Optional model/provider environments are separate extras and retain their existing upstream constraints. Model weights, credentials, datasets and local evidence are external inputs.

## Ownership and compatibility

Target account: `fireviewer`. Target stewardship: Association FIRE-VIEWER. Historical authorship and AGPL-3.0-or-later notices are retained. This technical extraction is not a signed assignment of rights.

Source correspondence and hashes are recorded in the migration dossier. Existing schema IDs, algorithm revisions and evidence/publication gates are preserved. The former `firewarning_worker` or backend module paths are compatibility adapters in their original repository.

## Delivery boundary

Docker, image ownership, registries and production deployment are deferred by the project owner. CPU/schema tests do not qualify GPU, visual or scientific performance.

## Sources et commandes propres au composant

Contrats et parseur TypeScript : `src/fireviewer_contracts`, `typescript`, `schemas.lock.json`. Les schémas sont accessibles via `fireviewer_contracts.resources.schema_path`. Les fixtures de manifeste public et de worker conservent leurs formats.

Les dépendances de base sont verrouillées avec hashes dans `requirements.lock.txt` (Python 3.13). Installer les wheels privés du même bundle via `--find-links`. Les extras lourds restent liés à leurs versions existantes et ne qualifient aucun GPU. Les tests de composant et leurs dépendances de test sont recensés dans le dossier unique de migration.
