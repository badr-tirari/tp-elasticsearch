# TP Introduction à Elasticsearch — Réponses aux questions

Environnement : Elasticsearch 9.5.4 + Kibana 9.5.4 (Docker Compose, nœud unique `es01`, cluster `tp-eisi`).
Les requêtes correspondantes sont dans `requetes/`.

## Exercice 0 — Vérifier l'accès au cluster

**Les réponses sont-elles identiques d'un outil à l'autre ?**
Oui. Kibana Dev Tools, curl (et Hoppscotch) appellent la même API REST sur le port 9200 : le JSON renvoyé par `GET /` est identique (`name: es01`, `cluster_name: tp-eisi`, `version.number: 9.5.4`, `tagline: "You Know, for Search"`). Seule la présentation change.

**Quel code HTTP sans authentification, et que dit le message d'erreur ?**
`401 Unauthorized`, avec une erreur `security_exception` : *missing authentication credentials for REST request [/]*. La sécurité est activée (`xpack.security.enabled=true`) : toute requête doit porter des identifiants (Basic Auth, clé d'API ou jeton).

**Pourquoi Kibana n'a-t-il pas besoin du mot de passe à chaque requête ?**
On s'authentifie une seule fois à la connexion à Kibana : Kibana crée une **session** (cookie dans le navigateur, session stockée dans l'index système `.kibana_security_session_*`). Les requêtes de la Console passent ensuite par le serveur Kibana, qui les relaie à Elasticsearch avec les droits de l'utilisateur connecté (`elastic`). Kibana lui-même se connecte au cluster avec son compte technique `kibana_system`.

## Partie 1 — Concepts, CRUD et mapping

### Exercice 1.1 — Explorer le cluster

**Quelle version tourne ?** Elasticsearch **9.5.4** (build Docker, Lucene 10.5.1).

**Combien de nœuds ?** **Un seul** : `es01`. Il porte tous les rôles (`cdfhilmrstw` : master, data, ingest, ml, transform…) et il est le master élu (`*`). Santé du cluster : **green**, 0 shard non alloué.

**Pourquoi des index commençant par un point ?**
Ce sont des **index système / cachés** créés par la stack elle-même : objets sauvegardés de Kibana (`.kibana_*`), sessions de sécurité (`.kibana_security_session_1`), alertes (`.internal.alerts-*`), SLO, etc. Ils sont masqués par défaut ; le paramètre `expand_wildcards=all` les fait apparaître. Il ne faut pas les modifier à la main.

### Exercice 1.2 — CRUD

**Comment évolue `_version` ?**
`PUT essai/_doc/1` → `_version: 1` (`result: created`) ; `POST essai/_update/1` → `_version: 2` (`result: updated`) ; la suppression incrémente encore le compteur (`result: deleted`). `_version` compte les écritures sur un même `_id` ; `_seq_no` suit la même progression (0, 1, …).

**Quel identifiant reçoit le document créé par `POST essai/_doc` ?**
Un identifiant **généré automatiquement** par Elasticsearch, une chaîne aléatoire de 20 caractères (dans mon cas `dZmY7KAB1cV7yDRxhMor`). Il n'a aucun sens métier.

**L'index `essai` existait-il avant le premier `PUT` ?**
Non (`HEAD essai` → 404). Il a été **créé automatiquement** au premier document, avec un mapping dynamique et les réglages par défaut : 1 shard primaire et **1 réplique**. Sur un nœud unique, la réplique ne peut pas être allouée : l'index `essai` est donc **yellow**.

`GET essai/_doc/1` après le `DELETE` renvoie `"found": false`.

### Exercice 1.3 — Les pièges du mapping dynamique

Mapping obtenu (`GET essai2/_mapping`) :

| Champ | Valeur envoyée | Type déduit |
| --- | --- | --- |
| `salaire` | `"45000"` (chaîne) | `text` + sous-champ `salaire.keyword` (`keyword`) |
| `publication` | `"2026-08-02"` | `date` (détection de date activée par défaut) |
| `actif` | `"true"` (chaîne) | `text` + sous-champ `keyword` (pas de détection booléenne sur une chaîne) |

**Pourquoi le document 2 est-il accepté ?**
Le type de `salaire` est figé par le **premier** document (`text`). Le nombre `52000` du document 2 est simplement converti en chaîne `"52000"` et indexé comme du texte : pas d'erreur, mais un type incohérent avec la donnée.

**Conséquence pour un tri ou un filtre `salaire > 50000` ?**
Sur `text`/`keyword`, les comparaisons sont **lexicographiques** (caractère par caractère) : `"9000" > "50000"` et `"100000" < "45000"`. Un `range` ou un tri numérique donne donc des résultats faux, et on ne peut pas calculer de moyenne (`avg`) sur ce champ. Le type ne pouvant pas être changé après coup, il faudrait recréer l'index et réindexer : d'où l'intérêt d'un mapping explicite.

### Exercice 1.4 — Mapping explicite de l'index `offres`

Choix des types :

| Champ(s) | Type | Justification |
| --- | --- | --- |
| `id`, `entreprise`, `ville`, `contrat`, `teletravail` | `keyword` | Valeur exacte : filtres `term`, facettes, tri |
| `titre` | `text` (analyseur `french`) + `titre.brut` (`keyword`) | Recherche plein texte en français **et** tri/facette sur la valeur brute |
| `description` | `text` (analyseur `french`) | Recherche plein texte (mots vides, élisions, racinisation) |
| `competences` | `keyword` + `competences.texte` (`text`, `french`) | Facettes et filtres exacts **et** recherche plein texte |
| `localisation` | `geo_point` | Requêtes `geo_distance` et tri par distance |
| `experience_annees`, `salaire_min`, `salaire_max` | `integer` | Intervalles (`range`), moyennes, statistiques |
| `date_publication` | `date` (format `yyyy-MM-dd`) | Filtres par période, `date_histogram` |

Index créé avec `"dynamic": "strict"`, 1 shard, 0 réplique → santé **green** (aucune réplique à placer).

**Quelle erreur obtenez-vous avec `PUT offres/_doc/test { "champ_inconnu": 1 }` ?**
`400 Bad Request` — `strict_dynamic_mapping_exception` : *mapping set to strict, dynamic introduction of [champ_inconnu] within [_doc] is not allowed*. Le document est refusé.

**Pourquoi est-ce une bonne pratique en production ?**
- Le schéma reste **maîtrisé** : une faute de frappe (`vile` au lieu de `ville`) ou un champ inattendu provoque une erreur visible au lieu de créer silencieusement un nouveau champ mal typé.
- On évite l'**explosion du mapping** (des milliers de champs créés par des données non contrôlées), qui dégrade les performances et la mémoire du cluster.
- Chaque champ a le type voulu dès le départ (les types ne peuvent plus changer ensuite sans réindexation).

Nettoyage effectué : `DELETE essai` et `DELETE essai2` → `acknowledged: true`.
