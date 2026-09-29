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

## Partie 3 — Recherche et analyseurs

### Exercice 3.1 — Voir travailler un analyseur

Phrase : *Les développeuses travaillaient sur l'analyse des données*

| Analyseur | Tokens produits |
| --- | --- |
| `standard` | `les` · `développeuses` · `travaillaient` · `sur` · `l'analyse` · `des` · `données` |
| `french` | `developeu` · `travailaient` · `analys` · `done` |

**Quels mots disparaissent avec `french` ?** Les **mots vides** : `les`, `sur`, `des` (et l'article élidé `l'`). `standard` ne fait que découper et mettre en minuscules : il garde tous les mots.

**Que devient `l'analyse` ?** Avec `standard`, c'est un seul token `l'analyse` (l'apostrophe n'est pas un séparateur). Avec `french`, le filtre d'**élision** retire `l'`, puis la **racinisation** réduit le mot à `analys`.

**« donnée » et « données » donnent-ils le même terme ?**
- `standard` : non — `donnée` et `données` sont deux termes différents ;
- `french` : oui — les deux deviennent `done` (accents retirés, pluriel et « e » final supprimés par le stemmer léger français).

**Conséquence pour la recherche :** avec un champ analysé en `french`, une recherche sur « donnée » retrouve aussi les documents qui contiennent « données » (et inversement), de même pour singulier/pluriel, masculin/féminin ou avec/sans accent. Avec `standard`, seule la forme exacte (en minuscules) correspondrait. C'est pour cela que `titre`, `description` et `competences.texte` utilisent l'analyseur `french`. Contrepartie : la racinisation peut rapprocher des mots différents (perte de précision).

### Exercice 3.2 — `match` contre `term`

| Requête | Résultats |
| --- | --- |
| `match` `description: "projets bancaires"` | 4 190 |
| `term` `ville: "paris"` | **0** |
| `term` `titre: "Data Engineer Senior"` | **0** |
| `term` `ville: "Paris"` (corrigée) | 1 492 |
| `term` `titre.brut: "Data Engineer Senior"` (corrigée) | 103 |
| `match` `description` avec `"operator": "and"` | 393 |

**Pourquoi les deux `term` renvoient-ils 0 ?** `term` n'analyse **pas** la valeur cherchée : il la compare telle quelle aux termes de l'index.
- `ville` est un `keyword` stocké tel quel (`Paris`, avec majuscule) : `paris` ≠ `Paris`. Correction : `"ville": "Paris"`.
- `titre` est un `text` analysé en `french` : l'index ne contient pas la chaîne entière mais les tokens `data`, `engin`, `senio`. La chaîne `Data Engineer Senior` n'y existe donc pas. Correction : chercher sur le sous-champ `keyword` → `"titre.brut": "Data Engineer Senior"` (ou utiliser `match` sur `titre`).

**Effet de `"operator": "and"` :** on passe de **4 190 à 393** résultats. Par défaut, `match` combine les tokens en **OU** : un document qui contient seulement « projets » (présent dans une grande partie des descriptions) suffit. Avec `and`, **tous** les tokens doivent être présents : il ne reste que les offres sur des projets **bancaires**, qui ont en outre le meilleur score (2,82).

### Exercice 3.3 — Plusieurs champs, pondération et fautes de frappe

| Variante | Résultats |
| --- | --- |
| `multi_match` « kubernetis terraform » | 739 (seul « terraform » correspond ; « kubernetis » seul → 0) |
| + `"fuzziness": "AUTO"` | 969 |
| + `titre^3` | 969, même ordre et mêmes scores |

**Quel paramètre rattrape la faute ?** `"fuzziness": "AUTO"`. Il accepte des termes à une distance d'édition de Levenshtein limitée (1 modification pour 3 à 5 caractères, 2 au-delà) : `kuberneti` (racine de « kubernetis ») retrouve `kubernet` (racine de « Kubernetes »). Le nombre de résultats passe de 739 à 969 et les offres qui ont **Kubernetes et Terraform** remontent en tête (score 5,74 contre 3,11).

**Comment évolue l'ordre avec le poids sur `titre` ?** Ici, **il ne change pas** : aucun titre ne contient « kubernetes » ni « terraform » (les titres sont « métier + niveau »). Le poids `^3` multiplie un score nul sur `titre`, et `multi_match` (`best_fields`) garde le meilleur champ, qui reste `competences.texte`/`description`. Pour vérifier l'effet du poids, avec « cloud terraform » : sans poids, le top 5 mélange Architecte Cloud et Ingénieur DevOps (score 3,11) ; avec `titre^3`, **les 5 premiers sont tous des « Architecte Cloud »** (score 6,83), car « cloud » apparaît dans leur titre. Le poids fait remonter les documents dont le champ pondéré correspond.

### Exercice 3.4 — Requête `bool`

`must` : `multi_match` « données » sur `titre` + `description` ; `filter` : `contrat = CDI`, `ville ∈ {Montpellier, Toulouse}`, `salaire_max ≥ 50 000` ; `must_not` : `teletravail = aucun` ; `should` : `competences = Elasticsearch`.

Résultat : **25 offres**, toutes des « Administrateur Bases de Données » (seuls titres contenant « données » ; aucune description n'emploie ce mot).

**Comparaison des `_score` avec et sans `should` :**
- sans `should` : tous les résultats ont le même score **2,048** ;
- avec `should` : les offres dont les compétences contiennent Elasticsearch passent à **4,015** et sont classées en tête ; les autres restent à 2,048.

Le nombre de résultats est **identique (25)** : en présence de `must`/`filter`, le `should` n'est pas obligatoire, il ne fait qu'ajouter un **bonus de score**.

**Pourquoi mettre les critères exacts dans `filter` plutôt que dans `must` ?**
1. **Pertinence** : un critère oui/non (CDI, ville, salaire) ne doit pas influencer le score. Dans `filter`, il ne calcule pas de score ; dans `must`, il fausserait le classement (un document « plus CDI » que l'autre n'a pas de sens).
2. **Performance** : le contexte filtre évite le calcul BM25 et ses résultats sont **mis en cache** (bitsets réutilisés d'une requête à l'autre).

### Exercice 3.5 — Recherche géographique

**340 offres** à moins de 20 km de Montpellier (43.6108, 3.8767), toutes situées à Montpellier (aucune autre ville du corpus dans ce rayon). Triées par distance : la plus proche à **0,19 km** (Développeur Java Confirmé), la plus lointaine à **6,65 km** — cohérent avec le bruit de ±0,05° appliqué par le générateur autour du centre-ville. La valeur de `sort` donne la distance en km ; `_score` vaut `null` car on trie par distance et le critère est en contexte filtre.

### Exercice 3.6 — Pagination et surlignage

Page 2 par pages de 5 : `"from": 5, "size": 5` (résultats 6 à 10) ; `"_source": ["titre", "entreprise", "ville"]` ; `"highlight": { "fields": { "description": {} } }`.

Constat : le surlignage sur `description` est **vide**, car aucune description du corpus ne contient « donnée(s) » : c'est le titre qui correspond. J'ai donc ajouté `titre` au `highlight`, qui renvoie par exemple `Administrateur Bases de <em>Données</em> Lead`. Le surligneur applique le même analyseur que la recherche (`Données` est mis en évidence grâce à la racine `done`).

**Pourquoi `from + size` est-il limité à 10 000, et quoi utiliser au-delà ?**
Pour renvoyer les résultats `from` à `from + size`, chaque shard doit trier et renvoyer ses `from + size` meilleurs résultats au nœud coordinateur, qui les fusionne : le coût en mémoire et en CPU croît avec la profondeur de la page. La limite `index.max_result_window = 10 000` protège le cluster. Au-delà, on utilise **`search_after`** (on passe les valeurs de tri du dernier résultat de la page précédente) avec un **point in time** (`POST offres/_pit?keep_alive=1m`) pour que la pagination porte sur une vue figée et cohérente de l'index, même s'il est modifié entre deux pages.

## Partie 4 — Agrégations

### Exercice 4.1 — Offres et salaire moyen par ville

| Ville | Offres | Offres avec salaire | Salaire min. moyen |
| --- | --- | --- | --- |
| **Paris** | 1 492 | 994 | **57 442 €** |
| Grenoble | 90 | 65 | 53 046 € |
| Nantes | 414 | 280 | 52 125 € |
| Toulouse | 424 | 296 | 52 047 € |
| Strasbourg | 177 | 126 | 51 976 € |
| Marseille | 325 | 216 | 51 407 € |
| Montpellier | 340 | 227 | 51 203 € |
| Rennes | 269 | 186 | 51 038 € |
| Bordeaux | 402 | 277 | 51 036 € |
| Lyon | 589 | 412 | 50 818 € |
| Lille | 367 | 231 | 50 316 € |
| Nice | 111 | 79 | 49 456 € |

**Quelle ville a le salaire moyen le plus élevé ?** **Paris** (≈ 57 442 €), environ 4 000 à 8 000 € de plus que les autres villes : on retrouve la majoration parisienne de +6 000 € du générateur.

**Sur combien d'offres la moyenne est-elle réellement calculée ?** Uniquement sur les documents qui **ont** le champ `salaire_min` : **3 389 offres sur 5 000** (CDI et CDD). Les 1 611 offres en alternance, stage et freelance n'ont pas ce champ et sont ignorées par `avg` (un champ absent n'est pas compté comme 0). Exemple : Paris a 1 492 offres mais la moyenne porte sur 994 d'entre elles (vérifié avec une sous-agrégation `value_count`).

**En remplaçant `ville` par `titre` :** erreur `400` — *Fielddata is disabled on [titre] in [offres]. Text fields are not optimised for operations that require per-document field data like aggregations and sorting…* Un champ `text` est découpé en tokens et n'a pas de *doc values* : on ne peut pas regrouper sur sa valeur entière. **Correction :** agréger sur le sous-champ `keyword` → `"field": "titre.brut"` (activer `fielddata` sur `titre` est déconseillé : coûteux en mémoire, et les buckets seraient des tokens, pas des titres).

### Exercice 4.2 — Publications par mois

| Mois | Offres | CDI | Alternance | CDD | Freelance | Stage |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-04 | 763 | 443 | 114 | 76 | 92 | 38 |
| 2026-05 | 865 | 476 | 127 | 105 | 103 | 54 |
| 2026-06 | 820 | 461 | 125 | 102 | 94 | 38 |
| 2026-07 | 835 | 459 | 135 | 100 | 108 | 33 |
| 2026-08 | 880 | 476 | 134 | 119 | 108 | 43 |
| 2026-09 | 837 | 460 | 119 | 112 | 108 | 38 |

Total : 5 000 offres sur 6 mois (avril → septembre 2026), réparties de façon régulière ; le CDI représente environ 55 % des offres chaque mois.

### Exercice 4.3 — Tranches de salaire et statistiques

| Tranche de `salaire_min` | Offres |
| --- | --- |
| < 40 k | 484 |
| 40–55 k | 1 363 |
| ≥ 55 k | 1 542 |

Total 3 389 (seules les offres avec salaire entrent dans les tranches). Rappel : dans une agrégation `range`, `from` est **inclus** et `to` **exclu**.

`stats` sur `experience_annees` : `count` 5 000, `min` 0, `max` 15, `avg` ≈ 5,91 ans, `sum` 29 564.

### Exercice 4.4 — Requête + agrégation

Requête `match_phrase` « Data Engineer » sur `titre` → **462 offres**.

- **5 compétences les plus demandées :** Airflow (315), Spark (313), Kafka (312), Python (311), SQL (301).
- **Télétravail le plus fréquent :** **partiel** (284 offres sur 462).

**L'agrégation porte-t-elle sur tout l'index ou seulement sur les résultats ?** **Seulement sur les documents sélectionnés par la `query`.** Preuve : sur tout l'index, le top 5 est différent — Python (1 320), Elasticsearch (1 279), Linux (1 003), Git (1 000), Docker (986). Pour agréger sur tout l'index malgré une requête, il faudrait une agrégation `global`.

### Bonus — ES|QL

```
POST _query
{ "query": "FROM offres | STATS salaire_moyen = AVG(salaire_min) BY ville | SORT salaire_moyen DESC" }
```

Même résultat que l'exercice 4.1 : Paris 57 441,6 €, Grenoble 53 046,2 €, Nantes 52 125 €…

## Mini-défi — `search.py`

- Requête `bool` : `must` = `multi_match` sur `titre^3`, `competences.texte^2`, `description` avec `fuzziness: AUTO` ; `filter` optionnels = `ville`, `contrat`, `teletravail` (`term`), `salaire_max ≥ --salaire-min` (`range`), `geo_distance` autour de `--autour` dans `--rayon`.
- Pagination `from = (page − 1) × taille`, `size = taille`, avec garde-fou à 10 000 résultats.
- Extrait surligné (description, compétences ou titre ; `<em>` affiché en `[ ]` dans le terminal).
- Trois facettes `terms` : villes, contrats, compétences — calculées sur les résultats filtrés.

Exemples de volumes obtenus : « développeur python » → 3 996 offres (les « Développeur Python » en tête, score 10,62) ; « données spark » à Lyon en CDI avec salaire ≥ 45 000 → 180 offres (Data Engineer en tête).

Limite observée : `fuzziness: AUTO` élargit parfois trop (ex. « données » → racine `done`, qui rapproche aussi `bonne`). En production, on limiterait le flou aux termes longs (`"fuzziness": "AUTO:4,7"`) ou on le réserverait aux champs `titre`/`competences`.
