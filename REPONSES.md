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

---

# TP2 — Ingestion et analyse de logs avec Logstash

## Mise en place — compte dédié

Rôle `logstash_writer` (cluster : `monitor`, `manage_index_templates` ; index `offres` et `logs-web-*` : `write`, `create`, `create_index`, `auto_configure`) et utilisateur `logstash_internal` créés. Vérification avec `GET _security/_authenticate` sous ce compte : `"username": "logstash_internal"`, `"roles": ["logstash_writer"]`, realm `default_native`. Requêtes dans `requetes/logstash.txt` (sans le mot de passe).

**Pourquoi ne pas utiliser le compte `elastic` pour Logstash ?**
`elastic` est le **super-utilisateur** : il peut tout lire, tout supprimer, gérer la sécurité. Principe du **moindre privilège** : Logstash n'a besoin que d'écrire dans `offres` et `logs-web-*`. Si son mot de passe fuit (fichier, log, conteneur compromis) ou si un pipeline est mal écrit (mauvais index, `DELETE`…), les dégâts restent limités à ces index. Un compte dédié permet aussi de **tracer** qui écrit quoi (journal d'audit) et de révoquer ou faire tourner son mot de passe sans toucher aux accès administrateur.

**Que se passerait-il si le pipeline `web` tentait d'écrire dans `logs-generic-default` ?**
L'écriture serait **refusée** : le rôle n'autorise que les noms `offres` et `logs-web-*`. Elasticsearch renvoie une erreur `403 security_exception` (*action [indices:data/write/bulk[s]] is unauthorized for user [logstash_internal]…*) pour chaque document ; Logstash journalise l'échec et les événements ne sont pas indexés. C'est le comportement voulu : un pipeline ne peut pas polluer un autre flux de données.

**Pourquoi le mot de passe passe-t-il par variable d'environnement plutôt que dans les `.conf` ?**
Les fichiers `.conf` sont **versionnés dans Git** et partagés : un secret écrit dedans finirait sur GitHub (pénalité au barème, et surtout fuite définitive dans l'historique). Le mot de passe vit dans `.env` (ignoré par Git), Docker Compose l'injecte dans le conteneur (`ES_PASSWORD`), et le `.conf` n'y fait référence que par `${ES_PASSWORD}`. On sépare ainsi **configuration** (versionnée, identique partout) et **secrets** (propres à chaque environnement, faciles à changer).

## Exercice 0 — Premier pipeline

Pipeline `stdin` → `stdout { codec => rubydebug }`. Pour la saisie « Nouvelle offre Data Engineer à Montpellier » :

```
{
         "event" => { "original" => "Nouvelle offre Data Engineer à Montpellier" },
    "@timestamp" => 2026-10-02T12:13:08.878875824Z,
          "host" => { "hostname" => "f99e5c5adc05" },
      "@version" => "1",
       "message" => "Nouvelle offre Data Engineer à Montpellier"
}
```

Avec `filter { mutate { uppercase => ["message"] } }` : `"message" => "NOUVELLE OFFRE DATA ENGINEER À MONTPELLIER"`, tandis que `event.original` garde le texte brut. Le filtre transforme le champ de travail, la donnée d'origine reste disponible.

**Quels champs Logstash a-t-il ajoutés à la phrase ?**
- `message` : la ligne lue (le seul contenu réellement saisi) ;
- `@timestamp` : horodatage de l'événement ;
- `@version` : version du format d'événement Logstash (`"1"`) ;
- `event.original` : copie brute de la ligne, ajoutée en mode ECS v8 (`pipeline.ecs_compatibility: v8` dans les journaux) ;
- `host.hostname` : nom de la machine qui a produit l'événement — ici l'identifiant du conteneur Docker (`f99e5c5adc05`, différent à chaque `docker compose run`).

Remarque : stdin découpe sur le retour à la ligne. Une commande collée par erreur sans Entrée, suivie de « Bonjour Logstash », a produit **un seul** événement contenant les deux textes : une ligne = un événement.

**Que contient `@timestamp` : l'heure de quoi ?**
L'heure à laquelle **Logstash a reçu/créé l'événement** (lecture de la ligne), en **UTC** (suffixe `Z` : 12:13 UTC = 14:13 à Paris). Ce n'est pas l'heure à laquelle le fait décrit s'est produit : pour des logs, il faudra la remplacer par la date contenue dans la ligne avec le filtre `date` (partie 3).

**À quoi sert `--path.data /tmp/essai` ?**
`path.data` est le dossier de travail de Logstash : UUID du nœud, file d'attente persistée, dead letter queue, sincedb, plugins. Logstash pose un **verrou** sur ce dossier : deux instances ne peuvent pas partager le même. Le service `logstash` du compose utilise `/usr/share/logstash/data` (volume `lsdata`) ; en donnant un dossier temporaire distinct à l'instance éphémère, on évite le conflit de verrou (« Logstash could not be started because there is already another instance using the configured data directory ») et on ne pollue pas les données (sincedb, files) du vrai service. Les journaux le confirment : *Creating directory for setting path.data: /tmp/essai*, puis `queue` et `dead_letter_queue` créés dedans.

## Partie 1 — Recharger les offres avec Logstash

### Exercice 1.1 — `offres.conf`

| TODO | Réglage | Rôle |
| --- | --- | --- |
| 1 | `mode => "read"` | Lit le fichier en entier, une fois (pas de suivi de fin de fichier comme `tail`) |
| 2 | `codec => "json"` | Chaque ligne est un objet JSON dont les clés deviennent les champs de l'événement |
| 3 | `sincedb_path => "/dev/null"` | Pas de mémoire de position : fichier relu à chaque démarrage (labo) |
| 4 | `file_completed_action => "log"` + `file_completed_log_path` | Ne **supprime pas** le fichier après lecture (défaut `delete` en mode `read`), note son nom dans `offres_lus.log` |
| 5 | `index => "offres"`, `document_id => "%{id}"`, `action => "index"`, `data_stream => "false"`, `manage_template => false` | Écrit dans l'index existant, `_id` métier, pas de data stream ni de template |

Validation : `--config.test_and_exit` → **`Config Validation Result: OK`**. (Avertissement non bloquant du codec `json` : sans option `target`, les champs sont placés à la racine de l'événement — c'est voulu ici.)

Incident de mise en place : `docker compose up -d logstash` a échoué avec *ports are not available: … 127.0.0.1:9600 … bind: Une tentative d'accès à un socket de manière interdite*. Cause : sous Windows, le port 9600 est dans une **plage réservée** par Hyper-V/WSL (`netsh interface ipv4 show excludedportrange protocol=tcp` → 9502-9601). Correction : port hôte **9700** dans `docker-compose.override.yml` (`127.0.0.1:9700:9600`), l'API reste sur 9600 dans le conteneur.

### Exercice 1.2 — Premier lancement

Avant le lancement : `GET offres/_doc/OFF-00002` → `_version: 3`.

**Les documents sont-ils indexés ?** **Non.** Chaque événement est refusé ; Logstash journalise un `WARN … Could not index event to Elasticsearch` par offre et continue avec les suivantes.

**Quelle erreur, quel code HTTP, quel type d'exception ?**
`status: 400` (Bad Request) — `strict_dynamic_mapping_exception` : *mapping set to strict, dynamic introduction of [@version] within [_doc] is not allowed*.

**Quels noms de champs sont cités ?** L'erreur cite **`@version`** : c'est le premier champ inconnu rencontré dans le document envoyé, Elasticsearch s'arrête au premier refus. Mais le document reçu par Elasticsearch (visible dans le journal) contient aussi d'autres champs absents du mapping : `@timestamp`, `host.name`, `log.file.path` et `event.original`.

**Lien avec `"dynamic": "strict"` (TP d'introduction, ex. 1.4) :** le mapping de `offres` déclare exactement les 13 champs d'une offre et refuse tout champ supplémentaire (même comportement que le test `champ_inconnu` ou le champ `prime`). Logstash enrichit chaque événement de métadonnées (`@version`, `@timestamp`) et, en mode ECS, l'entrée `file` ajoute `host`, `log.file.path` et `event.original` : ces champs ne sont pas dans le mapping, donc **les 5 000 documents sont rejetés**. Le verrou `strict` joue son rôle : il empêche Logstash de polluer silencieusement l'index avec des champs non prévus.

Remarque : `event.original` se termine par `\r` — le fichier `offres.ndjson`, généré sous Windows, a des fins de ligne CRLF. Le codec `json` ignore ce caractère (espace blanc en fin d'objet), donc sans conséquence sur les données.

### Exercice 1.3 — Corriger

`filter { mutate { remove_field => ["@version", "@timestamp", "host", "log", "event"] } }`. Après `docker compose restart logstash` : plus aucun `Could not index event`, journaux `Pipelines running {count: 2, running_pipelines: [:offres, :web]}`.

| | Avant Logstash | Après correction |
| --- | --- | --- |
| `GET offres/_count` | 5 000 | **5 000** |
| `_version` de `OFF-00002` | 3 | **4** |

**Le nombre de documents a-t-il changé ?** **Non**, toujours 5 000 : chaque offre est envoyée avec `document_id => "%{id}"`, donc l'action `index` **remplace** le document de même `_id` au lieu d'en créer un nouveau.

**Et le `_version` de `OFF-00002` ? Pourquoi ?** Il passe de **3 à 4** : le document a été réécrit une fois de plus (une nouvelle version du même document, pas un doublon). Les tentatives refusées de l'exercice 1.2 n'ont pas incrémenté la version : un document rejeté n'est pas écrit.

**Pourquoi supprimer ces champs plutôt qu'assouplir le mapping ?**
- Ce sont des **métadonnées techniques de transport** (version du format Logstash, heure de lecture, conteneur, chemin du fichier), sans valeur métier pour un moteur de recherche d'offres.
- `event.original` recopie la ligne JSON entière : il **doublerait le stockage** de chaque offre.
- Assouplir (`dynamic: true`) ferait perdre la protection du TP d'introduction : n'importe quel champ inattendu (faute de frappe, `prime`…) serait de nouveau accepté silencieusement, avec un type deviné.
- Le contenu de l'index reste **identique quelle que soit la source** (`ingest.py` ou Logstash) : le pipeline s'adapte au contrat de données, pas l'inverse.

**Pourquoi l'index `offres` doit-il exister avant le premier démarrage de Logstash ?**
Avec `manage_template => false`, Logstash n'installe aucun modèle d'index. Si `offres` n'existait pas, le premier envoi le **créerait automatiquement** (le rôle `logstash_writer` a `create_index`/`auto_configure`) avec un **mapping dynamique** deviné à partir du premier document : `titre`/`description` en `text` standard (pas d'analyseur `french`, pas de sous-champ `brut`), `competences` en `text` + `keyword` générique, `localisation` en objet de deux `float` au lieu de `geo_point` (requêtes `geo_distance` impossibles), et aucun verrou `strict`. Le type d'un champ ne pouvant plus changer ensuite, il faudrait supprimer l'index et tout recharger. On crée donc d'abord l'index avec son mapping explicite (TP d'introduction, ex. 1.4), puis Logstash ne fait qu'y écrire.

### Exercice 1.4 — Relancer

Après un nouveau `docker compose restart logstash` : `GET offres/_count` → **5 000** ; `OFF-00002` → **`_version: 5`** (4 juste avant). Lu juste après le redémarrage, le document était encore en version 4 : la lecture et l'envoi prennent quelques secondes.

`/usr/share/logstash/data/offres_lus.log` (rempli grâce à `file_completed_action => "log"`) :

```
/data/offres.ndjson
/data/offres.ndjson
/data/offres.ndjson
```

**Combien de fois le fichier a-t-il été lu ?** **Trois fois**, une par démarrage du service : exercice 1.2 (documents rejetés), exercice 1.3 (documents indexés, version 3 → 4), exercice 1.4 (version 4 → 5). Avec `sincedb_path => "/dev/null"`, Logstash oublie à chaque démarrage qu'il a déjà lu le fichier et le relit en entier. Le fichier source n'a jamais été supprimé (`log` au lieu de `delete`).

**Avec la sincedb par défaut ?** Logstash enregistrerait dans `path.data` (volume `lsdata`, conservé entre redémarrages) l'identifiant du fichier (inode, périphérique) et la position atteinte. Au redémarrage, il saurait que `offres.ndjson` a été lu jusqu'au bout et **ne le relirait pas** : 0 événement envoyé, `_version` resterait à 4. C'est le comportement voulu en production (ne pas retraiter ce qui l'a déjà été) ; seul un fichier nouveau ou modifié serait lu.

**Si `document_id` n'était pas renseigné ?** Elasticsearch génèrerait un `_id` aléatoire pour chaque événement : chaque lecture **ajouterait** 5 000 nouveaux documents au lieu de remplacer les existants → 10 000 après la 1re lecture réussie (les 5 000 d'origine + 5 000 copies), 15 000 après la 2e, et ainsi de suite. Les comptages et agrégations du TP d'introduction seraient faux. Le `_id` métier rend l'ingestion **idempotente**, exactement comme `ingest.py` (TP d'introduction, ex. 2.2).

## Partie 2 — Superviser et fiabiliser

### Exercice 2.1 — Superviser

API de supervision interrogée sur `http://localhost:9700` (port hôte 9700 → 9600 dans le conteneur, cf. partie 1). `GET /` : Logstash **9.5.4**, `status: green`, écoute sur `0.0.0.0:9600`.

**Combien de pipelines, avec combien de workers ?** **Deux pipelines** : `offres` et `web` (ceux déclarés dans `pipelines.yml`), chacun avec **16 workers**, `batch_size` 125 et `batch_delay` 50 ms. Par défaut, `pipeline.workers` = nombre de cœurs CPU vus par le conteneur (16 sur ce poste).

**`in`, `filtered`, `out` pour `offres` :**

| Compteur | Valeur | Signification |
| --- | --- | --- |
| `in` | 5 000 | Événements produits par l'entrée `file` et poussés dans la file |
| `filtered` | 5 000 | Événements passés par les filtres (`mutate`) |
| `out` | 5 000 | Événements remis aux sorties (`elasticsearch`) |

Ces compteurs sont **cumulés depuis le dernier démarrage** du processus Logstash (ils repartent à 0 à chaque `restart`) : ils correspondent donc à **une seule lecture** de `offres.ndjson` — celle de l'exercice 1.4 — et non aux trois. La sortie `elasticsearch` confirme : `documents.successes: 5000` en **48 requêtes `_bulk`**, toutes en `200`. File d'attente : `memory`.

**Quel plugin consomme le plus de temps ?** La sortie **`elasticsearch`** : `duration_in_millis` ≈ **7 684 ms** sur 8 489 ms au total pour le pipeline, contre **781 ms** pour le filtre `mutate` (l'entrée `file` ne mesure que le temps d'attente pour pousser dans la file : 120 ms). Le coût principal est l'envoi réseau et l'indexation côté Elasticsearch, pas la transformation. Indicateurs de flux : `worker_utilization` ≈ 28 %, `queue_backpressure` quasi nul → le pipeline n'est pas saturé.

### Exercice 2.2 — Isoler les documents rejetés

Mise en œuvre :
- `DEAD_LETTER_QUEUE_ENABLE=true` dans `docker-compose.override.yml` (le kit ne contenait pas la ligne commentée annoncée par l'énoncé : ajoutée), conteneur recréé (`docker compose up -d logstash`) — journal : *Setting 'dead_letter_queue.enable' from environment* ;
- `path => "/data/offres*.ndjson"` dans `offres.conf` (le kit lisait seulement `offres.ndjson`) ;
- `data/offres_test.ndjson` : une ligne, dernière offre avec `"id": "OFF-99999"` et `"prime": 3000`.

Journal Logstash :

```
[WARN ][logstash.outputs.elasticsearch][offres] Events could not be indexed and routing to DLQ
{count: 1, dlq_routed_stats: {400 => {count: 1, sample_event: {action: ["index", {_id: "OFF-99999", _index: "offres"} …
response: {"index" => {"status" => 400, "error" => {"type" => "strict_dynamic_mapping_exception",
"reason" => "[1:65] mapping set to strict, dynamic introduction of [prime] within [_doc] is not allowed"}}}}}}}
```

`ls -lR …/dead_letter_queue` : un dossier par pipeline (`offres`, `web`) ; dans `offres`, segment **`1.log` (2 164 octets)** + `2.log.tmp` (segment courant, 1 octet = en-tête). Juste après le démarrage, seul `1.log.tmp` (1 octet) existait : le document n'est écrit qu'après la lecture, puis le segment est finalisé.

`GET offres/_doc/OFF-99999` → `"found": false` ; `GET offres/_count` → **5 000**.

Relecture avec un Logstash éphémère (`docker compose exec logstash logstash --path.data /tmp/dlq2 -f /data/dlq_relecture.conf`, entrée `dead_letter_queue`, `pipeline_id => "offres"`, `commit_offsets => false`). La configuration est dans un fichier (`data/dlq_relecture.conf`) car PowerShell supprime les guillemets doubles d'une config passée en ligne avec `-e`. Extrait :

```
"id" => "OFF-99999", "prime" => 3000, … (le document complet, tel qu'envoyé)
"@metadata" => {
  "path" => "/data/offres_test.ndjson",
  "dead_letter_queue" => {
    "plugin_type" => "elasticsearch",
    "plugin_id"   => "07fc51ecb808…",
    "entry_time"  => 2026-10-02T12:28:12.641Z,
    "reason"      => "Could not index event to Elasticsearch. status: 400, … strict_dynamic_mapping_exception …
                      mapping set to strict, dynamic introduction of [prime] within [_doc] is not allowed"
  }
}
```

**Le document `OFF-99999` est-il dans l'index ? Où se trouve-t-il ?** **Non** (`found: false`, `_count` inchangé à 5 000). Il est conservé **sur disque, dans la dead letter queue** du pipeline `offres` : `/usr/share/logstash/data/dead_letter_queue/offres/1.log`, dans le volume Docker `lsdata` (il survit donc aux redémarrages du conteneur).

**Quelle raison de refus est enregistrée ?** Dans `[@metadata][dead_letter_queue][reason]` : *Could not index event to Elasticsearch. status: 400 … `strict_dynamic_mapping_exception` … dynamic introduction of [prime] within [_doc] is not allowed*, avec le plugin responsable (`plugin_type: elasticsearch`, `plugin_id`) et l'heure du rejet (`entry_time`). Le fichier source est aussi gardé (`[@metadata][path]`).

**Comparaison avec `raise_on_error=False` dans `ingest.py` :** les deux évitent qu'un document invalide bloque le lot entier. Mais `ingest.py` se contente d'**afficher** l'erreur dans le terminal : une fois le script terminé, le document est perdu (il faudrait le retrouver dans le fichier source). La DLQ apporte en plus :
- la **conservation durable** du document complet, tel qu'il a été envoyé (après les filtres), sur disque ;
- les **métadonnées du rejet** (raison, code, plugin, date) attachées au document ;
- la possibilité de le **rejouer** avec une entrée `dead_letter_queue` dans un pipeline Logstash, sans relire tout le fichier source ;
- une séparation nette : le flux principal continue, les rejets s'accumulent à part et peuvent être supervisés (taille de la DLQ).

**Corriger et réinjecter ce document, en trois étapes :**
1. **Analyser** : relire la DLQ (`commit_offsets => false`, sortie `stdout`) pour lire `[@metadata][dead_letter_queue][reason]` et identifier la cause (ici le champ `prime` inconnu du mapping).
2. **Corriger** : décider de la règle — soit supprimer `prime` (filtre `mutate { remove_field => ["prime"] }`), soit, si la donnée est utile, ajouter `prime` (`integer`) au mapping avec `PUT offres/_mapping` (ajouter un champ est permis, en changer le type ne l'est pas).
3. **Réinjecter** : lancer un pipeline `entrée dead_letter_queue (pipeline_id offres, commit_offsets => true) → filtre de correction → sortie elasticsearch (index offres, document_id "%{id}")`. `commit_offsets => true` marque les événements comme traités pour ne pas les rejouer deux fois ; le `_id` métier garantit qu'un éventuel second passage ne crée pas de doublon. Vérifier ensuite `GET offres/_doc/OFF-99999` et `_count` (5 001).

Le fichier `data/offres_test.ndjson` est ensuite supprimé (il est aussi ignoré par Git).

### Exercice 2.3 — Pourquoi deux pipelines ?

**Sans `pipelines.yml` monté, combien de pipelines ?** **Un seul**, nommé `main` : l'image Docker charge tous les fichiers de `/usr/share/logstash/pipeline/` (`offres.conf` + `web.conf`) et les **concatène** en une seule configuration (deux entrées, tous les filtres, deux sorties).

**Que deviendrait une offre lue dans `offres.ndjson` ?** Dans un pipeline unique, **chaque événement traverse tous les filtres et va vers toutes les sorties** (sauf conditions `if`). L'offre passerait donc par le `grok` des logs d'accès (qui échouerait : tag `_grokparsefailure`), puis serait envoyée **à la fois** dans l'index `offres` et dans le data stream `logs-web-default`, où elle polluerait les logs.

**Et une ligne de log d'accès ?** Symétriquement : elle serait envoyée dans `logs-web-default` **et** dans l'index `offres` — où elle serait rejetée (champs inconnus pour le mapping `strict`, et `document_id => "%{id}"` vaudrait littéralement `%{id}` faute de champ `id` : toutes les lignes viseraient le même `_id`).

**Deux autres avantages à isoler les pipelines :**
1. **Isolation des pannes et des performances** : chaque pipeline a ses propres workers, sa file et sa contre-pression. Si Elasticsearch refuse ou ralentit les écritures d'un flux, l'autre continue ; une erreur de configuration ou un rechargement n'arrête que le pipeline concerné.
2. **Lisibilité et supervision séparées** : chaque `.conf` reste simple (pas de `if [type] == …` partout), les compteurs `in/filtered/out` de l'API sont par flux, et on peut régler chaque pipeline indépendamment (workers, taille des lots, file persistée ou DLQ seulement là où c'est utile).

### Exercice 2.4 — Ne rien perdre (réflexion)

**`docker kill` pendant la lecture d'un gros fichier, file en mémoire : que deviennent les événements lus mais pas encore envoyés ?** Ils sont **perdus**. La file `memory` vit dans la RAM du processus ; un arrêt brutal (SIGKILL, pas d'arrêt propre qui vide la file) efface tous les événements qui avaient été lus mais pas encore acquittés par Elasticsearch. Si la sincedb avait déjà avancé, ces lignes ne seraient même pas relues au redémarrage.

**Quel réglage change ce comportement, et quelle garantie obtient-on ?** `queue.type: persisted` (file persistée sur disque, dans `path.data/queue`, taille bornée par `queue.max_bytes`). Un événement n'est retiré de la file qu'après **acquittement** par la sortie. Au redémarrage, Logstash rejoue tout ce qui n'avait pas été acquitté. Garantie : **« au moins une fois »** (*at-least-once*) — aucune perte, mais un même événement peut être envoyé **deux fois** (s'il avait été indexé juste avant le kill, sans que l'acquittement ait été enregistré).

**Pourquoi le `document_id` de la partie 1 devient-il indispensable ?** Parce que *at-least-once* implique des **renvois**. Avec `document_id => "%{id}"`, un renvoi réécrit le même document (`_version` +1, `_count` inchangé) : l'opération est **idempotente**, on obtient en pratique « exactement une fois » dans l'index. Sans `_id` métier, chaque renvoi créerait un doublon avec un `_id` aléatoire, indétectable après coup.
