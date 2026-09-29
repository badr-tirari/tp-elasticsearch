"""Mini-défi — moteur de recherche d'offres en ligne de commande.

Attendu :
  python search.py "développeur python"
  python search.py "données spark" --ville Lyon --contrat CDI --salaire-min 45000
  python search.py "kubernetes" --autour "43.6108,3.8767" --rayon 50km --teletravail partiel --page 2
"""

from __future__ import annotations

import argparse
import re

from es_client import INDEX, get_client

FACETTES = {"ville": "Villes", "contrat": "Contrats", "competences": "Compétences"}


def construire_requete(args: argparse.Namespace) -> dict:
    """Requête bool :
    - must   : multi_match sur titre (x3), competences.texte (x2), description, tolérant aux fautes
    - filter : ville, contrat, teletravail (term), salaire_max >= --salaire-min (range),
               distance autour d'un point (geo_distance) si --autour est fourni
    """
    must = [
        {
            "multi_match": {
                "query": args.texte,
                "fields": ["titre^3", "competences.texte^2", "description"],
                "fuzziness": "AUTO",
            }
        }
    ]

    filtres: list[dict] = []
    if args.ville:
        filtres.append({"term": {"ville": args.ville}})
    if args.contrat:
        filtres.append({"term": {"contrat": args.contrat}})
    if args.teletravail:
        filtres.append({"term": {"teletravail": args.teletravail}})
    if args.salaire_min is not None:
        filtres.append({"range": {"salaire_max": {"gte": args.salaire_min}}})
    if args.autour:
        lat, lon = lire_point(args.autour)
        filtres.append(
            {"geo_distance": {"distance": args.rayon, "localisation": {"lat": lat, "lon": lon}}}
        )

    return {"bool": {"must": must, "filter": filtres}}


def lire_point(texte: str) -> tuple[float, float]:
    """Convertit "lat,lon" en tuple de floats (erreur lisible sinon)."""
    try:
        lat, lon = (float(v) for v in texte.split(","))
    except ValueError:
        raise SystemExit(f'--autour attend "lat,lon" (ex. "43.6108,3.8767"), reçu : {texte!r}')
    return lat, lon


def formater_salaire(src: dict) -> str:
    if "salaire_min" in src:
        return f"{src['salaire_min'] // 1000}–{src['salaire_max'] // 1000} k€"
    return "salaire non communiqué"


def extrait(hit: dict) -> str:
    """Premier fragment surligné (description puis titre), <em> -> [ ] pour le terminal."""
    hl = hit.get("highlight", {})
    fragments = hl.get("description") or hl.get("competences.texte") or hl.get("titre")
    if not fragments:
        return hit["_source"]["description"][:120] + "…"
    return re.sub(r"</?em>", lambda m: "]" if m.group().startswith("</") else "[", fragments[0])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("texte")
    p.add_argument("--ville")
    p.add_argument("--contrat", choices=["CDI", "CDD", "Alternance", "Freelance", "Stage"])
    p.add_argument("--teletravail", choices=["aucun", "partiel", "total"])
    p.add_argument("--salaire-min", type=int)
    p.add_argument("--autour", help="lat,lon")
    p.add_argument("--rayon", default="30km")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--taille", type=int, default=10)
    args = p.parse_args()

    if args.page < 1 or args.taille < 1:
        raise SystemExit("--page et --taille doivent être >= 1")
    if args.page * args.taille > 10_000:
        raise SystemExit("Pagination limitée à 10 000 résultats (utiliser search_after au-delà)")

    es = get_client()
    reponse = es.search(
        index=INDEX,
        query=construire_requete(args),
        from_=(args.page - 1) * args.taille,
        size=args.taille,
        track_total_hits=True,
        highlight={
            "fields": {"description": {}, "competences.texte": {}, "titre": {}},
            "fragment_size": 120,
            "number_of_fragments": 1,
        },
        aggs={champ: {"terms": {"field": champ, "size": 10}} for champ in FACETTES},
    )

    total = reponse["hits"]["total"]["value"]
    nb_pages = max(1, -(-total // args.taille))
    print(f"\n{total} offre(s) pour « {args.texte} » — page {args.page}/{nb_pages}\n")

    debut = (args.page - 1) * args.taille
    for rang, hit in enumerate(reponse["hits"]["hits"], start=debut + 1):
        src = hit["_source"]
        print(f"{rang:>3}. [{hit['_score']:.2f}] {src['titre']} — {src['entreprise']}")
        print(f"     {src['ville']} · {src['contrat']} · télétravail {src['teletravail']} · {formater_salaire(src)}")
        print(f"     {extrait(hit)}\n")

    if not reponse["hits"]["hits"]:
        print("Aucun résultat sur cette page.\n")

    for champ, titre in FACETTES.items():
        buckets = reponse["aggregations"][champ]["buckets"]
        valeurs = ", ".join(f"{b['key']} ({b['doc_count']})" for b in buckets) or "—"
        print(f"{titre} : {valeurs}")


if __name__ == "__main__":
    main()
