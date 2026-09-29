"""Partie 2 — Crée l'index `offres` avec un mapping explicite puis ingère le NDJSON en bulk.

Usage : python ingest.py [--fichier data/offres.ndjson] [--reset]
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path

from elasticsearch import helpers

from es_client import INDEX, get_client

SETTINGS = {"number_of_shards": 1, "number_of_replicas": 0}

MAPPINGS = {
    "dynamic": "strict",
    "properties": {
        "id": {"type": "keyword"},
        "titre": {"type": "text", "analyzer": "french", "fields": {"brut": {"type": "keyword"}}},
        "entreprise": {"type": "keyword"},
        "description": {"type": "text", "analyzer": "french"},
        "competences": {
            "type": "keyword",
            "fields": {"texte": {"type": "text", "analyzer": "french"}},
        },
        "ville": {"type": "keyword"},
        "localisation": {"type": "geo_point"},
        "contrat": {"type": "keyword"},
        "teletravail": {"type": "keyword"},
        "experience_annees": {"type": "integer"},
        "salaire_min": {"type": "integer"},
        "salaire_max": {"type": "integer"},
        "date_publication": {"type": "date", "format": "yyyy-MM-dd"},
    },
}


def lire_actions(fichier: Path) -> Iterator[dict]:
    """Générateur : lit le fichier ligne à ligne (mémoire constante) et produit
    une action bulk par offre, avec un _id stable tiré du champ métier `id`."""
    with fichier.open(encoding="utf-8") as f:
        for numero, ligne in enumerate(f, start=1):
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                doc = json.loads(ligne)
            except json.JSONDecodeError as exc:
                print(f"Ligne {numero} ignorée (JSON invalide) : {exc}")
                continue
            yield {"_index": INDEX, "_id": doc["id"], "_source": doc}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fichier", type=Path, default=Path("data/offres.ndjson"))
    parser.add_argument("--reset", action="store_true", help="supprime l'index s'il existe")
    args = parser.parse_args()

    if not args.fichier.exists():
        raise SystemExit(f"Fichier introuvable : {args.fichier} (lancez python data/generate_offres.py)")

    es = get_client()
    print("Cluster :", es.info()["version"]["number"])

    # TODO 3 : --reset -> suppression sans erreur si l'index n'existe pas
    if args.reset:
        es.indices.delete(index=INDEX, ignore_unavailable=True)
        print(f"Index '{INDEX}' supprimé")

    # TODO 4 : création de l'index s'il n'existe pas
    if not es.indices.exists(index=INDEX):
        es.indices.create(index=INDEX, settings=SETTINGS, mappings=MAPPINGS)
        print(f"Index '{INDEX}' créé")
    else:
        print(f"Index '{INDEX}' déjà présent : les documents de même _id seront remplacés")

    # TODO 5 : ingestion en lots de 1000, sans lever d'exception sur les rejets
    ok, erreurs = helpers.bulk(
        es, lire_actions(args.fichier), chunk_size=1000, raise_on_error=False
    )
    print(f"{ok} documents indexés, {len(erreurs)} erreurs")
    for err in erreurs[:10]:
        detail = next(iter(err.values()))
        cause = detail.get("error", {})
        print(f"  - _id={detail.get('_id')} : {cause.get('type')} — {cause.get('reason')}")
    if len(erreurs) > 10:
        print(f"  ... et {len(erreurs) - 10} autres erreurs")

    # TODO 6 : refresh puis comptage
    es.indices.refresh(index=INDEX)
    total = es.count(index=INDEX)["count"]
    print(f"{total} documents dans '{INDEX}'")


if __name__ == "__main__":
    main()
