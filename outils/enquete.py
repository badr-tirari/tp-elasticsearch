"""TP2 partie 4 — exécute les requêtes ES|QL de l'enquête et enregistre les résultats.

Usage (depuis la racine du dépôt, venv activé) : python outils/enquete.py
Résultats : tmp/enquete_resultats.json (dossier ignoré par Git).
Les heures sont converties en heure de Paris (UTC+2 en septembre) avec "+ 2 hours".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from es_client import get_client  # noqa: E402

DS = "logs-web-default"
PARIS = "EVAL t = @timestamp + 2 hours"
CHEMIN = 'EVAL chemin = MV_FIRST(SPLIT(url.original, "?"))'

REQUETES = {
    # 4.1 Vue d'ensemble
    "4.1_par_code": f"FROM {DS} | STATS n = COUNT(*) BY http.response.status_code | SORT n DESC",
    "4.1_par_methode": f"FROM {DS} | STATS n = COUNT(*) BY http.request.method | SORT n DESC",
    "4.1_par_jour": f"FROM {DS} | {PARIS} | STATS n = COUNT(*) BY jour = DATE_TRUNC(1 day, t) | SORT jour",
    "4.1_periode": f"FROM {DS} | {PARIS} | STATS total = COUNT(*), debut = MIN(t), fin = MAX(t)",
    # 4.2 Incident
    "4.2_5xx_par_heure": f"FROM {DS} | WHERE http.response.status_code >= 500 | {PARIS} "
                         f"| STATS erreurs = COUNT(*) BY heure = BUCKET(t, 1 hour) | SORT erreurs DESC | LIMIT 10",
    "4.2_5xx_par_code": f"FROM {DS} | WHERE http.response.status_code >= 500 "
                        f"| STATS n = COUNT(*) BY http.response.status_code",
    "4.2_503_par_5min": f"FROM {DS} | WHERE http.response.status_code == 503 | {PARIS} "
                        f"| STATS erreurs = COUNT(*) BY tranche = BUCKET(t, 5 minutes) | SORT tranche",
    "4.2_503_bornes": f"FROM {DS} | WHERE http.response.status_code == 503 | {PARIS} "
                      f"| STATS n = COUNT(*), premiere = MIN(t), derniere = MAX(t)",
    "4.2_503_url": f"FROM {DS} | WHERE http.response.status_code == 503 | {CHEMIN} "
                   f"| STATS n = COUNT(*) BY chemin | SORT n DESC",
    "4.2_pendant_incident_par_chemin": f"FROM {DS} | {PARIS} "
        f'| WHERE t >= TO_DATETIME("2026-09-28T14:00:00Z") AND t < TO_DATETIME("2026-09-28T14:45:00Z") '
        f'| {CHEMIN} | EVAL famille = CASE(chemin LIKE "/offres/*/postuler", "/offres/*/postuler", '
        f'chemin LIKE "/offres/*", "/offres/*", chemin) '
        f"| STATS total = COUNT(*), erreurs_5xx = COUNT(CASE(http.response.status_code >= 500, 1, null)) "
        f"BY famille | SORT total DESC",
    "4.2_api_meme_creneau_par_jour": f"FROM {DS} | {PARIS} | {CHEMIN} | WHERE chemin == \"/api/offres\" "
        f"| EVAL minute_jour = DATE_EXTRACT(\"hour_of_day\", t) * 60 + DATE_EXTRACT(\"minute_of_hour\", t) "
        f"| WHERE minute_jour >= 840 AND minute_jour < 885 "
        f"| STATS requetes_api = COUNT(*) BY jour = DATE_TRUNC(1 day, t) | SORT jour",
    "4.2_api_par_5min_autour": f"FROM {DS} | {PARIS} | {CHEMIN} | WHERE chemin == \"/api/offres\" "
        f'| WHERE t >= TO_DATETIME("2026-09-28T13:30:00Z") AND t < TO_DATETIME("2026-09-28T15:15:00Z") '
        f"| STATS requetes = COUNT(*), ok = COUNT(CASE(http.response.status_code == 200, 1, null)), "
        f"e503 = COUNT(CASE(http.response.status_code == 503, 1, null)) BY tranche = BUCKET(t, 5 minutes) | SORT tranche",
    "4.2_500_hors_incident": f"FROM {DS} | WHERE http.response.status_code == 500 | {PARIS} | {CHEMIN} "
                             f"| KEEP t, source.address, chemin | SORT t",
    # 4.3 Activité suspecte
    "4.3_404_par_ip": f"FROM {DS} | WHERE http.response.status_code == 404 "
                      f"| STATS n = COUNT(*) BY source.address | SORT n DESC | LIMIT 5",
    "4.3_robot_bornes": f'FROM {DS} | WHERE source.address == "203.0.113.66" | {PARIS} '
                        f"| STATS n = COUNT(*), debut = MIN(t), fin = MAX(t), codes = VALUES(http.response.status_code)",
    "4.3_robot_url": f'FROM {DS} | WHERE source.address == "203.0.113.66" '
                     f"| STATS n = COUNT(*) BY url.original | SORT n DESC",
    "4.3_robot_agent": f'FROM {DS} | WHERE source.address == "203.0.113.66" '
                       f"| STATS n = COUNT(*) BY user_agent.original, user_agent.name, user_agent.device.name",
    "4.3_404_par_minute_scan": f"FROM {DS} | WHERE http.response.status_code == 404 | {PARIS} "
                               f"| STATS n = COUNT(*) BY minute = BUCKET(t, 1 minute) | SORT n DESC | LIMIT 8",
    "4.3_autres_404": f'FROM {DS} | WHERE http.response.status_code == 404 AND source.address != "203.0.113.66" '
                      f"| {CHEMIN} | EVAL famille = CASE(chemin LIKE \"/offres/OFF-*\", \"/offres/OFF-xxxxx\", chemin) "
                      f"| STATS n = COUNT(*), ips = COUNT_DISTINCT(source.address), exemples = VALUES(labels.offre_id), "
                      f"referents = VALUES(http.request.referrer) BY famille",
    "4.3_autres_404_ip_max": f'FROM {DS} | WHERE http.response.status_code == 404 AND source.address != "203.0.113.66" '
                             f"| STATS n = COUNT(*) BY source.address | SORT n DESC | LIMIT 3",
    # 4.4 Offres les plus consultées
    "4.4_top_offres": f'FROM {DS} | WHERE http.request.method == "GET" AND http.response.status_code == 200 '
                      f"AND labels.offre_id IS NOT NULL | STATS vues = COUNT(*) BY labels.offre_id "
                      f"| SORT vues DESC, labels.offre_id ASC | LIMIT 10",
    # 4.5 Public
    "4.5_par_os": f"FROM {DS} | STATS n = COUNT(*) BY user_agent.os.name | SORT n DESC",
    "4.5_mobile": f'FROM {DS} | EVAL mobile = user_agent.os.name IN ("Android", "iOS") '
                  f"| STATS n = COUNT(*) BY mobile",
    "4.5_par_navigateur": f"FROM {DS} | STATS n = COUNT(*) BY user_agent.name | SORT n DESC",
    "4.5_par_appareil": f"FROM {DS} | STATS n = COUNT(*) BY user_agent.device.name | SORT n DESC",
}


def main() -> None:
    es = get_client()
    resultats: dict = {}
    for nom, requete in REQUETES.items():
        try:
            r = es.esql.query(query=requete)
            resultats[nom] = {"requete": requete,
                              "colonnes": [c["name"] for c in r["columns"]],
                              "lignes": r["values"]}
            print(f"OK     {nom} ({len(r['values'])} lignes)")
        except Exception as exc:  # on continue pour avoir toutes les requêtes
            resultats[nom] = {"requete": requete, "erreur": str(exc)[:500]}
            print(f"ERREUR {nom} : {str(exc)[:150]}")

    # 4.4 suite : titre, ville et contrat des offres les plus vues, en une requête sur l'index offres
    top = resultats.get("4.4_top_offres", {}).get("lignes", [])
    ids = [ligne[1] for ligne in top]
    if ids:
        r = es.search(index="offres", query={"ids": {"values": ids}}, size=len(ids),
                      source=["titre", "ville", "contrat", "entreprise"])
        resultats["4.4_details_offres"] = {
            "requete": {"query": {"ids": {"values": ids}}, "_source": ["titre", "ville", "contrat", "entreprise"]},
            "documents": {h["_id"]: h["_source"] for h in r["hits"]["hits"]}}
        print(f"OK     4.4_details_offres ({len(r['hits']['hits'])} offres)")

    sortie = Path("tmp/enquete_resultats.json")
    sortie.parent.mkdir(exist_ok=True)
    sortie.write_text(json.dumps(resultats, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\nRésultats écrits dans {sortie}")


if __name__ == "__main__":
    main()
