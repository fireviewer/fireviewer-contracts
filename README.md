# fireviewer-contracts

## Repères documentaires — 19 septembre 2026

- **Rôle :** Contrats versionnés d’évidence, géométrie, reconstruction et clients backend bornés.
- **Statut :** Actif — package `fireviewer_contracts` v0.1.2 dans le README actuel.
- **Entrées :** Schémas et contrats partagés.
- **Sorties :** Modèles Python, types TypeScript, schemas lockés et fixtures.
- **Limites :** Ne pas dupliquer les contrats ailleurs. Les IDs et formats compatibles ne se renomment pas pendant un simple nettoyage documentaire.

[Fiche du dépôt](https://github.com/fireviewer/Fireviewer_doc/blob/main/docs/public/repositories/fireviewer-contracts.md) · [Architecture](https://github.com/fireviewer/Fireviewer_doc/blob/main/docs/public/ARCHITECTURE.md) · [Statuts et vocabulaire](https://github.com/fireviewer/Fireviewer_doc/blob/main/docs/public/STATUTS_ET_VOCABULAIRE.md).

Cette revue documentaire ne renouvelle aucun test ni aucune réception. Les procédures, versions et preuves techniques ci-dessous conservent leur périmètre et leur date.

> **Source active FV · private.** Contrats métier et géométrie partagée, modèles Python, types TypeScript et fixtures. Voir [où travailler, quoi commiter et comment reprendre](ORGANISATION.md).

Versioned evidence and reconstruction contracts and bounded backend clients.

Python package: `fireviewer_contracts`. Version: `0.1.2`.

## Installation

Install the versioned release wheels (including private FireViewer dependencies) from the release bundle. No sibling source checkout is required.

```sh
python -m pip install --find-links /path/to/release/wheels fireviewer-contracts==0.1.2
python -m pytest tests -q
```

Optional model/provider environments are separate extras and retain their existing upstream constraints. Model weights, credentials, datasets and local evidence are external inputs.

## Canonical repository and rights

Canonical source: [`fireviewer/fireviewer-contracts`](https://github.com/fireviewer/fireviewer-contracts). Technical stewardship: FIRE-VIEWER. Repository access: private.

Historical authorship, AGPL-3.0-or-later notices and third-party rights are retained. Technical stewardship and repository placement are not a signed assignment of intellectual-property rights. Any pre-association assets remain subject to their documented licences or agreements.

This repository is the maintained implementation location for the responsibility stated above. Existing schema IDs, algorithm revisions and evidence/publication gates are preserved. Older `firewarning_worker` or backend imports remain compatibility adapters where required; they are not alternative locations for new component logic.

## Delivery and qualification

Versioned packages are distributed through the authorised private release bundles. Current container locks, reconstruction inputs and dated acceptance records are maintained in [fireviewer-docker](https://github.com/fireviewer/fireviewer-docker).

Package installation, CPU/schema tests, service deployment and real-data acceptance are separate checks. CPU/schema tests do not qualify GPU, visual or scientific performance. This documentation update does not publish a package, rebuild an image or change production configuration.

Extraction correspondence and hashes remain in the historical migration dossier. They record the restructuring, not the current deployment state.

## Sources et commandes propres au composant

Contrats et parseur TypeScript : `src/fireviewer_contracts`, `typescript`, `schemas.lock.json`. Les schémas sont accessibles via `fireviewer_contracts.resources.schema_path`. Les fixtures de manifeste public et de worker conservent leurs formats.

Les dépendances de base sont verrouillées avec hashes dans `requirements.lock.txt` (Python 3.13). Installer les wheels privés du même bundle via `--find-links`. Les extras lourds restent liés à leurs versions existantes et ne qualifient aucun GPU. Les commandes de reprise et leurs prérequis sont décrits dans [ORGANISATION.md](ORGANISATION.md). Les reçus du dossier de migration restent des preuves historiques, pas une nouvelle qualification.
