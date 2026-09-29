# TP Introduction à Elasticsearch — Réponses aux questions

Environnement : Elasticsearch 9.5.4 + Kibana 9.5.4 (Docker Compose, nœud unique `es01`, cluster `tp-eisi`).
Les requêtes correspondantes sont dans `requetes/`.

## Exercice 0 — Vérifier l'accès au cluster

**Les réponses sont-elles identiques d'un outil à l'autre ?**
Oui. Kibana Dev Tools, curl (et Hoppscotch) appellent la même API REST sur le port 9200 : le JSON renvoyé par `GET /` est identique (`name: es01`, `cluster_name: tp-eisi`, `version.number: 9.5.4`, `tagline: "You Know, for Search"`). Seule la présentation change.

**Quel code HTTP sans authentification, et que dit le message d'erreur ?**
`401 Unauthorized`, avec une erreur `security_exception` : *missing authentication credentials for REST request [/?pretty]*, et l'en-tête `WWW-Authenticate` indique les méthodes acceptées (`Basic`, `ApiKey`). La sécurité est activée (`xpack.security.enabled=true`) : toute requête doit porter des identifiants (Basic Auth, clé d'API ou jeton).

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

## Partie 2 — Ingestion en Python

### Exercice 2.1 — `ingest.py`

`python ingest.py --reset` : l'index `offres` est recréé avec le mapping de l'exercice 1.4, puis **5000 documents indexés, 0 erreurs, 5000 documents dans 'offres'**.

Points clés du script :
- `lire_actions()` est un **générateur** (`yield`) : le fichier est lu ligne à ligne en `utf-8`, la mémoire consommée ne dépend pas de la taille du fichier ;
- chaque action fixe `_id` = champ métier `id` (`OFF-00001`…) ;
- `--reset` → `es.indices.delete(..., ignore_unavailable=True)` (pas d'erreur si l'index n'existe pas) ;
- création conditionnelle (`es.indices.exists()`), `helpers.bulk(chunk_size=1000, raise_on_error=False)`, affichage des erreurs, puis `refresh` et `count`.

### Exercice 2.2 — Idempotence et identifiants

**Le nombre de documents a-t-il doublé ?**
Non : après une seconde exécution sans `--reset`, on a toujours **5000 documents**. En revanche `GET offres/_doc/OFF-00002` montre `_version: 3` : le document a été réécrit à chaque ingestion (remplacé, pas dupliqué), et `_cat/indices` affiche des `docs.deleted` correspondant aux anciennes versions en attente de fusion des segments.

**Pourquoi fixer `_id` à partir du champ `id` est-il essentiel ?**
L'action `index` avec un `_id` connu **remplace** le document existant. Relancer le pipeline (après une panne, une correction, un rejeu) ne crée donc jamais de doublon : l'ingestion est **idempotente**, et l'offre `OFF-00002` reste adressable directement par son identifiant métier.

**Que se passerait-il avec des identifiants générés par Elasticsearch ?**
Chaque exécution créerait de **nouveaux** documents avec de nouveaux `_id` aléatoires : 10 000 documents après deux lancements, 15 000 après trois… Les comptages et agrégations seraient faussés, et il faudrait dédoublonner a posteriori.

### Exercice 2.3 — Provoquer une erreur de mapping

Ligne ajoutée : copie de la dernière offre avec `"id": "OFF-99999"` et `"prime": 3000`. Sortie :

```
5000 documents indexés, 1 erreurs
  - _id=OFF-99999 : strict_dynamic_mapping_exception — [1:703] mapping set to strict, dynamic introduction of [prime] within [_doc] is not allowed
5000 documents dans 'offres'
```

**Le lot entier est-il rejeté ou seulement ce document ?**
**Seulement ce document.** L'API `_bulk` renvoie un statut par opération : les 5000 autres documents du même lot sont indexés, `OFF-99999` est refusé (`GET offres/_doc/OFF-99999` → `found: false`).

**Intérêt de `raise_on_error=False` pour un pipeline ?**
Avec la valeur par défaut (`True`), `helpers.bulk` lève une `BulkIndexError` au premier lot contenant une erreur, et le script s'arrête sans traiter la suite. Avec `False`, le pipeline **va jusqu'au bout**, renvoie la liste des rejets et permet de les journaliser, de les compter ou de les mettre de côté (file d'erreurs / *dead letter queue*) pour correction, sans bloquer les données valides.

Le fichier propre a ensuite été régénéré (`python data/generate_offres.py`).

### Exercice 2.4 — Vérifier dans Kibana

- `GET _cat/indices/offres?v` : index **green**, 1 primaire, 0 réplique, `docs.count` 5000 (≈ 3,9 Mo) ;
- `GET offres/_count` → `5000` ;
- `GET offres/_doc/OFF-00002` → *Analyste Cybersécurité Senior*, Cévennes Data, Paris.

Data view `offres` créée avec le champ temporel `date_publication`. Dans **Discover** sur « Last 1 year », l'histogramme couvre avril → septembre 2026, mais le compteur affiche **4 968** documents et non 5 000 : les **32 offres datées du 30/09/2026** (date de référence du générateur) sont dans le futur au moment de la consultation (29/09/2026) et donc hors de la plage « jusqu'à maintenant ». 4 968 + 32 = 5 000. Les dates s'affichent à 02:00 car elles sont stockées en UTC et affichées dans le fuseau du navigateur (Europe/Paris).
