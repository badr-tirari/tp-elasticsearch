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

## Partie 3 — Transformer les logs d'accès

### Exercice 3.1 — Générer les logs

`python data/generate_access_logs.py` → **20 700 lignes** dans `data/access.log` (fichier ignoré par Git). Logstash a été arrêté avant (`docker compose stop logstash`) pour que le pipeline `web` ne lise pas le fichier avant la mise au point. Premières lignes :

```
203.0.113.123 - - [23/Sep/2026:00:00:39 +0200] "GET /offres/OFF-01468 HTTP/1.1" 200 43686 "https://jobs.example.org/recherche" "Mozilla/5.0 (Linux; Android 15; Pixel 9) …"
198.51.100.126 - - [23/Sep/2026:00:00:42 +0200] "GET /recherche?q=data&ville=Nantes HTTP/1.1" 200 29517 "-" "Mozilla/5.0 (Windows NT 10.0; Win64; x64) …"
192.0.2.209 - - [23/Sep/2026:00:00:48 +0200] "GET /api/offres?ville=Paris&page=5 HTTP/1.1" 200 1668 "-" "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) …"
```

### Exercice 3.2 — Mettre au point le motif

Test du Grok Debugger de Kibana (via son API `POST /api/grokdebugger/simulate`, équivalent du bouton *Simulate*) avec la première ligne et `%{COMBINEDAPACHELOG}` :

```json
{ "clientip": "203.0.113.123", "ident": "-", "auth": "-",
  "timestamp": "23/Sep/2026:00:00:39 +0200",
  "verb": "GET", "request": "/offres/OFF-01468", "httpversion": "1.1",
  "response": "200", "bytes": "43686",
  "referrer": "\"https://jobs.example.org/recherche\"",
  "agent": "\"Mozilla/5.0 (Linux; Android 15; Pixel 9) …\"" }
```

**Quels champs sont extraits ?** Les 11 éléments de la ligne : adresse du client, identité et utilisateur (`-`), date, méthode, URL, version HTTP, code de réponse, taille, page d'origine et navigateur. Le Grok Debugger utilise le jeu de motifs **historique** (noms `clientip`, `verb`, `request`, `response`…). Dans Logstash, le pipeline est en `ecs_compatibility: v8` : le même motif produit les **noms ECS** du tableau de l'énoncé (`source.address`, `http.request.method`, `url.original`, `http.version`, `http.response.status_code`, `http.response.body.bytes`, `http.request.referrer`, `user_agent.original`) — c'est ce que vérifie l'exercice 3.4.

**Sous quel type apparaît `http.response.status_code` ?** Dans le Grok Debugger : une **chaîne** (`"200"`) — grok extrait du texte et le motif historique ne convertit pas. Dans la version ECS du motif utilisée par Logstash, le code est capturé avec conversion `:int` (`%{INT:[http][response][status_code]:int}`) : il arrive en **entier**, et le modèle `logs-*-*` le mappe en `long` (vérifié en 3.4).

**Pourquoi `timestamp` doit-il encore être traité ?** grok ne fait que **découper du texte** : `timestamp` est une chaîne `"23/Sep/2026:00:00:39 +0200"`, pas une date. `@timestamp` resterait l'heure de **lecture** par Logstash (toutes les lignes datées du 02/10 à la même minute), ce qui rendrait les histogrammes et la recherche par période de la partie 4 inutilisables. Le filtre `date` (format `dd/MMM/yyyy:HH:mm:ss Z`, `locale => "en"` pour `Sep`) convertit cette chaîne en vraie date dans `@timestamp`, en tenant compte du fuseau `+0200`, puis le champ texte est supprimé.

**Motif qui extrait `OFF-01468` de `/offres/OFF-01468/postuler` :**

```
pattern_definitions => { "OFFRE_ID" => "OFF-[0-9]{5}" }
match => { "[url][original]" => "^/offres/%{OFFRE_ID:[labels][offre_id]}" }
```

Test dans le Grok Debugger (motif personnalisé `OFFRE_ID OFF-[0-9]{5}`, pattern `^/offres/%{OFFRE_ID:labels.offre_id}`) → `{"labels": {"offre_id": "OFF-01468"}}`. Le motif est ancré au début (`^`) et ne décrit pas la fin de l'URL : il fonctionne pour `/offres/OFF-01468` comme pour `/offres/OFF-01468/postuler`. Le champ `labels` est le champ ECS prévu pour des étiquettes personnalisées (mappé en `keyword`).

### Exercice 3.3 — `web.conf`

Le `web.conf` fourni par le kit était déjà rempli (le commit du formateur n'a ajouté les `TODO` que dans `offres.conf`) ; chaque bloc est documenté par le `TODO` correspondant :

| TODO | Filtre / réglage | Rôle |
| --- | --- | --- |
| 1 | `grok { match => { "message" => "%{COMBINEDAPACHELOG}" } }` | Découpe la ligne en champs ECS |
| 2 | `date { match => ["timestamp", "dd/MMM/yyyy:HH:mm:ss Z"] locale => "en" remove_field => ["timestamp"] }` | `@timestamp` = heure de la requête |
| 3 | `useragent { source => "[user_agent][original]" }` | Navigateur, OS, appareil |
| 4 | `if [url][original] =~ /^\/offres\/OFF-/ { grok { pattern_definitions => { "OFFRE_ID" => "OFF-[0-9]{5}" } … } }` | `labels.offre_id` |
| 5 | `if "_grokparsefailure" not in [tags] { mutate { remove_field => ["[event][original]"] } }` | Ligne brute gardée seulement en cas d'échec |
| 6 | `data_stream => "true"`, `data_stream_type => "logs"`, `data_stream_dataset => "web"`, `data_stream_namespace => "default"` | Écriture dans `logs-web-default` |

`--config.test_and_exit` → **`Config Validation Result: OK`**. Démarrage : `docker compose up -d logstash` ; la lecture des 20 700 lignes prend quelques secondes.

### Exercice 3.4 — Vérifier le data stream

| Vérification | Résultat |
| --- | --- |
| `GET logs-web-default/_count` | **20 700** documents |
| `_count` avec `term tags: _grokparsefailure` | **0** échec |
| Backing index (`GET _data_stream/logs-web-default`) | `.ds-logs-web-default-2026.10.02-000001` |
| Premier événement (tri `@timestamp` asc) | `"@timestamp": "2026-09-22T22:00:39.000Z"` |
| `_mapping/field/http.response.status_code` | `"type": "long"` |
| `index.mode` | **`logsdb`** |

**Combien de documents et d'échecs de grok ?** **20 700** documents (une par ligne du fichier) et **0** document portant le tag `_grokparsefailure` : toutes les lignes respectent le format *combined*.

**Nom de l'index caché et signification :** `.ds-logs-web-default-2026.10.02-000001`
- `.ds-` : préfixe des *backing indices* de data stream ; le point les rend **cachés** (non visibles avec `*`) ;
- `logs-web-default` : nom du data stream = **type** `logs`, **dataset** `web`, **namespace** `default` ;
- `2026.10.02` : **date de création** de cet index (jour de l'ingestion — et non la date des événements, du 23 au 29/09) ;
- `000001` : **numéro de génération**, incrémenté à chaque *rollover* (nouvel index d'écriture créé par la politique ILM `logs` selon taille ou âge).

Autres informations de `GET _data_stream` : modèle `logs` (*default logs template installed by x-pack*), politique de cycle de vie `logs` gérée par ILM, champ temporel `@timestamp`. Statut **YELLOW** : le modèle `logs` prévoit 1 réplique, impossible à placer sur un cluster à un seul nœud (même situation que l'index `essai` du TP d'introduction).

**Le premier événement est-il daté du 23/09/2026 à 00:00:39 (+02:00) ?** **Oui** : `@timestamp` vaut `2026-09-22T22:00:39.000Z`, c'est-à-dire 23/09/2026 00:00:39 heure de Paris (UTC+2), soit 22:00:39 UTC la veille. Le filtre `date` a pris en compte le fuseau `+0200` de la ligne ; Elasticsearch stocke toujours en UTC et Kibana réaffiche dans le fuseau du navigateur. Sans le filtre `date`, ce serait le 02/10/2026 (heure de lecture).

Document obtenu (extrait) : `source.address: 203.0.113.123`, `http.request.method: GET`, `url.original: /offres/OFF-01468`, `http.version: 1.1`, `http.response.status_code: 200`, `http.response.body.bytes: 43686`, `http.request.referrer: https://jobs.example.org/recherche`, `user_agent.name: Chrome Mobile`, `user_agent.os.full: Android 15`, `user_agent.device.name: Pixel 9`, `labels.offre_id: OFF-01468`, `data_stream.{type,dataset,namespace}`. Le champ `message` (la ligne brute) est conservé ; il se termine par `\r` car `access.log`, généré sous Windows, a des fins de ligne CRLF — sans effet sur l'analyse.

**Quel type pour `http.response.status_code`, et pourquoi est-ce important ?** **`long`** (numérique) : grok l'a converti en entier et le modèle ECS le type en nombre. On peut donc faire des **requêtes par intervalle** (`http.response.status_code >= 500`), des agrégations `range` (2xx / 4xx / 5xx), trier et calculer — indispensable pour l'enquête de la partie 4 (incident en 503, rafale de 404). En `keyword`, `>= 500` serait une comparaison de texte.

**Quel `index.mode` ?** **`logsdb`** : mode de stockage optimisé pour les logs (activé par défaut pour `logs-*-*` depuis la 9.0). Les documents sont triés par hôte et date, compressés plus fortement, et `_source` est reconstruit à partir des colonnes (*synthetic source*) : jusqu'à environ 2 à 3 fois moins de disque qu'un index standard.

### Exercice 3.5 — Rejouer sans doublon ?

Après `docker compose restart logstash` et la fin de la lecture (`web_lus.log` contient deux fois `/data/access.log`) : `GET logs-web-default/_count` → **41 400** (relevé intermédiaire pendant la relecture : 25 075).

**Que constate-t-on ?** Le nombre de documents a **doublé** (2 × 20 700) : chaque ligne de log est maintenant présente **deux fois**. Avec `sincedb_path => "/dev/null"`, le fichier est relu en entier au redémarrage, et chaque ligne est indexée comme un nouveau document avec un `_id` généré aléatoirement par Elasticsearch. Toutes les statistiques de la partie 4 (nombre de requêtes, d'erreurs, de visiteurs) seraient faussées d'un facteur 2.

**Pourquoi le problème ne se posait-il pas pour `offres` ?** Le pipeline `offres` fixe `document_id => "%{id}"` avec l'action `index` : une relecture **remplace** chaque offre (même `_id`, `_version` +1, `_count` inchangé à 5 000 — vérifié aux exercices 1.3 et 1.4). Le pipeline `web` ne donne pas d'`_id` : rien ne permet à Elasticsearch de reconnaître une ligne déjà reçue.

**Peut-on mettre à jour ou remplacer un document dans un data stream ?** **Pas par l'écriture normale.** Un data stream est conçu pour des données en **ajout seul** (*append-only*) : il n'accepte que l'action `create` ; une requête `index` qui viserait un `_id` existant, ou un `_update`, est refusée sur le data stream. Pour corriger exceptionnellement des données, il faut passer par les API `_update_by_query` / `_delete_by_query`, ou adresser directement le *backing index* (`.ds-logs-web-default-…`) avec `if_seq_no`/`if_primary_term`. Le modèle normal est : un événement de log s'écrit une fois et ne change plus.

**Deux solutions pour rejouer sans doublon :**
1. **Garder la mémoire de lecture (sincedb).** Supprimer `sincedb_path => "/dev/null"` (ou le pointer vers un fichier du volume persistant, par exemple `/usr/share/logstash/data/sincedb_web`). Logstash enregistre alors que `access.log` a été lu en entier et ne le relit pas au redémarrage ; seules les lignes **nouvelles** sont lues (en mode `tail`). Limite : ne protège pas si on supprime la sincedb, si le fichier est recopié (nouvel inode) ou si on veut volontairement rejouer.
2. **Calculer un `_id` à partir du contenu de la ligne (filtre `fingerprint`)**, ce qui rend l'ingestion **idempotente** comme pour `offres` :
   ```
   filter {
     fingerprint {
       source => ["message"]
       target => "[@metadata][fingerprint]"
       method => "SHA256"
     }
   }
   output {
     elasticsearch {
       … data_stream options …
       document_id => "%{[@metadata][fingerprint]}"
       action => "create"
     }
   }
   ```
   Deux lignes identiques donnent la même empreinte, donc le même `_id`. Au rejeu, l'action `create` (la seule autorisée sur un data stream) échoue avec un `409 version_conflict` pour les documents déjà présents : ils ne sont pas dupliqués, et Logstash ne les réessaie pas. L'empreinte est rangée dans `@metadata` pour ne pas être indexée. Limite : deux requêtes réellement distinctes mais strictement identiques (même IP, même seconde, même URL, même navigateur) seraient fusionnées — on peut ajouter `log.file.path` et un numéro de ligne à la source de l'empreinte si nécessaire.

Remise à zéro avant la partie 4 : `docker compose stop logstash`, `DELETE _data_stream/logs-web-default`, `docker compose up -d logstash` → de nouveau 20 700 documents.

## Partie 4 — Enquête dans Kibana

Data view **Logs web** (`logs-web-*`, champ temporel `@timestamp`), période absolue du 23/09/2026 au 30/09/2026. Requêtes KQL et ES|QL dans `requetes/enquete.txt` ; elles sont exécutées en une fois par `outils/enquete.py` (client Python, `es.esql.query`). **Toutes les heures ci-dessous sont en heure de Paris (UTC+2)** : les requêtes ES|QL ajoutent `EVAL t = @timestamp + 2 hours`, puisque ES|QL calcule en UTC.

### Exercice 4.1 — Vue d'ensemble

Période couverte : du **23/09/2026 00:00:39** au **29/09/2026 23:59:41**, **20 700 requêtes**.

| Code HTTP | Requêtes | Part | Signification |
| --- | --- | --- | --- |
| 200 | 17 805 | 86,0 % | Succès |
| 201 | 1 492 | 7,2 % | Création — les candidatures (`POST …/postuler`) |
| 404 | 508 | 2,5 % | Ressource introuvable |
| 304 | 488 | 2,4 % | Non modifié — fichier statique servi depuis le cache du navigateur |
| 503 | 402 | 1,9 % | Service indisponible |
| 500 | 5 | 0,02 % | Erreur interne |

| Méthode | Requêtes | Part |
| --- | --- | --- |
| GET | 19 208 | 92,8 % |
| POST | 1 492 | 7,2 % (= exactement les 1 492 réponses 201 : toutes les candidatures ont réussi) |

| Jour | 23/09 | 24/09 | 25/09 | 26/09 | 27/09 | 28/09 | 29/09 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Requêtes | 2 832 | 2 884 | 2 843 | **3 122** | 2 903 | **3 274** | 2 842 |

**Volume moyen : 20 700 / 7 ≈ 2 957 requêtes par jour** (≈ 123 par heure). Le trafic normal est très régulier (≈ 2 830 à 2 900 par jour) ; les deux jours au-dessus de la moyenne sont ceux des anomalies : le **26/09** (+300 requêtes du robot, ex. 4.3) et le **28/09** (+400 requêtes pendant l'incident, ex. 4.2).

### Exercice 4.2 — L'incident

**1. Jour et créneau précis.** Erreurs serveur (`>= 500`) par heure : **402 sur la seule heure du 28/09/2026 14:00–15:00**, contre au plus 1 sur toutes les autres heures de la semaine. Par tranches de 5 minutes, les 503 occupent exactement les 9 tranches de **14:00 à 14:45** (53, 41, 42, 35, 44, 42, 50, 47, 48), et aucune avant ni après. Bornes exactes : première 503 à **14:00:08**, dernière à **14:44:56**.
→ **Incident le lundi 28/09/2026, de 14:00 à 14:45 (heure de Paris).**

**2. URL touchées et épargnées.** Les **402 réponses 503 concernent toutes `/api/offres`** (l'API de recherche paginée, `?ville=…&page=…`). Pendant le même créneau, les autres pages ont fonctionné normalement :

| Chemin (14:00–14:45) | Requêtes | Erreurs 5xx |
| --- | --- | --- |
| `/api/offres` | 403 | **402** |
| `/offres/OFF-…` (fiches d'offre) | 34 | 0 |
| `/recherche` | 17 | 0 |
| `/` (accueil) | 12 | 0 |
| `/offres/…/postuler` (candidatures) | 7 | 0 |
| `/static/app.js` | 5 | 0 |

Le site lui-même est resté disponible : seul le **service d'API** était défaillant (backend de l'API, sa base ou une dépendance), pas le serveur web.

**3. Nombre d'erreurs et durée.** **402 réponses 503** en **≈ 45 minutes** (14:00:08 → 14:44:56), soit environ 9 erreurs par minute ; **99,75 %** des appels à l'API ont échoué pendant le créneau (402 sur 403, un seul 200 à 14:20). Les 5 erreurs **500** de la semaine sont sans rapport : isolées (25/09 23:41, 26/09 00:20, 28/09 02:06 et 20:12, 29/09 08:15), une à la fois, aussi sur `/api/offres` — un bruit de fond d'environ 0,02 %.

**4. Comportement des clients.** Le volume sur `/api/offres` a **explosé** :

| `/api/offres`, créneau 14:00–14:45 | 23/09 | 24/09 | 25/09 | 26/09 | 27/09 | **28/09** | 29/09 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Requêtes | 11 | 5 | 11 | 15 | 4 | **403** | 9 |

Par tranches de 5 minutes le 28/09 : 1 à 3 requêtes avant 14:00, **35 à 53 pendant l'incident**, de nouveau 1 à 3 après 14:45 — **≈ 40 fois le trafic habituel** de l'API, alors que les autres pages gardent leur volume normal. **Explication :** les clients de l'API (le front-end JavaScript du site, des applications partenaires) **réessaient automatiquement** quand ils reçoivent un 503 (et les utilisateurs rechargent la page). Ces réessais ne demandent quasiment que la première page (`page=1`) et arrivent de nombreuses adresses différentes. C'est un effet d'**amplification** (*retry storm*) : la panne génère elle-même une surcharge qui peut retarder le rétablissement. Pistes : réessais avec délai exponentiel et gigue (*exponential backoff*), en-tête `Retry-After` sur les 503, disjoncteur (*circuit breaker*) côté client.

**Rapport d'incident (synthèse pour l'équipe d'exploitation).** Le 28/09/2026 de 14:00 à 14:45, l'API `/api/offres` a répondu « 503 Service indisponible » à 402 requêtes sur 403 ; le reste du site (accueil, recherche, fiches, candidatures) n'a pas été affecté. Impact : la liste d'offres chargée par l'API était indisponible pendant 45 minutes ; le volume d'appels a été multiplié par environ 40 par les réessais des clients. Retour à la normale à 14:45 sans erreur résiduelle. Actions proposées : identifier la cause côté backend de l'API (journaux applicatifs, base de données à 14:00), limiter les réessais côté client, alerter sur le taux de 5xx (cf. partie 5).

### Exercice 4.3 — L'activité suspecte

**1. Adresse IP.** 404 par adresse source : **`203.0.113.66` → 300 réponses 404**, puis au plus 3 par adresse pour toutes les autres.

**2. Moment et durée.** Les 404 par minute montrent un bloc de **60 requêtes/minute pendant 5 minutes**, le **samedi 26/09/2026 de 03:12:00 à 03:16:59** (heure de Paris) : 300 requêtes en 300 secondes, soit **une requête par seconde, de façon parfaitement régulière**, en pleine nuit.

**3. URL demandées : que cherchait ce robot ?** Six chemins, demandés en boucle (43 à 59 fois chacun) :

| URL | Ce qui est recherché |
| --- | --- |
| `/admin` | Interface d'administration |
| `/.git/config` | Dépôt Git exposé → code source, adresses de dépôts, parfois des identifiants |
| `/.env` | Fichier de variables d'environnement → **mots de passe, clés d'API** |
| `/phpmyadmin/` | Console d'administration de base de données MySQL |
| `/server-status` | Page d'état Apache → URL internes, adresses des clients |
| `/wp-login.php` | Page de connexion WordPress (attaque par force brute) |

C'est un **scan de vulnérabilités automatisé** : il ne cherche pas une page du site, il teste des fichiers sensibles et des interfaces d'administration couramment exposés par erreur. Toutes les réponses sont des 404 : **aucune de ces ressources n'existe**, le scan n'a rien trouvé.

**4. `user_agent.original` : comment le distinguer d'un navigateur ?** Les 300 requêtes portent `Mozilla/5.0 zgrab/0.x`. **zgrab** est un outil de scan réseau (projet ZMap). Indices qui le distinguent d'un navigateur :
- le filtre `useragent` ne reconnaît ni navigateur, ni système, ni appareil (`user_agent.name: Other`, `os.name: Other`, `device.name: Other`), alors qu'un vrai navigateur donne `Chrome` / `Windows`, `Mobile Safari` / `iOS`… ;
- la chaîne est courte et ne contient ni moteur de rendu (`AppleWebKit`, `Gecko`) ni système ;
- comportement non humain : une requête par seconde exactement, aucune page référente (`-`), aucun fichier statique ni page normale chargés, 100 % de 404.

Remarque : la même adresse `203.0.113.66` apparaît aussi dans **27 requêtes ordinaires** (pages d'offres, recherches, API) réparties sur la semaine, avec des navigateurs classiques. Une adresse IP peut être partagée (NAT d'entreprise, opérateur mobile, proxy) : bloquer l'IP aurait aussi bloqué ces visiteurs. Mieux vaut bloquer selon le comportement (rafale de 404, chemins sensibles) ou l'agent `zgrab`, et limiter le débit (*rate limiting*, fail2ban, WAF).

**Les autres 404 (208) : d'où viennent-elles, sont-elles inquiétantes ?** Les 208 autres 404 concernent **toutes des fiches d'offre** `/offres/OFF-09xxx` (par exemple `OFF-09938`, `OFF-09776`) : des identifiants **qui n'existent pas** (l'index ne contient que `OFF-00001` à `OFF-05000`). Elles viennent de **172 adresses différentes** (au plus 3 par adresse), sont réparties sur toute la semaine, avec de vrais navigateurs, et ont pour page d'origine `https://jobs.example.org/recherche`. Ce sont des **visiteurs normaux qui suivent des liens morts** — offres expirées ou supprimées encore affichées dans les résultats de recherche (ou dans des favoris, des moteurs de recherche externes). **Pas inquiétant pour la sécurité**, mais c'est un défaut fonctionnel (≈ 1 % des consultations d'offres aboutissent à une erreur) : retirer les offres expirées de la recherche, ou renvoyer un `410 Gone` / une redirection vers des offres similaires.

### Exercice 4.4 — Les offres les plus consultées

Requêtes `GET` en `200` avec un `labels.offre_id`, regroupées par offre (ES|QL), puis détails récupérés **en une seule requête** sur l'index `offres` (`query: { ids: { values: [ … 10 identifiants … ] } }`) :

| Rang | Offre | Vues | Titre | Ville | Contrat |
| --- | --- | --- | --- | --- | --- |
| 1 | OFF-04662 | 8 | Développeur Front-end Senior | Bordeaux | Freelance |
| 2 | OFF-01153 | 7 | Développeur Java Confirmé | Toulouse | Freelance |
| 3 | OFF-03141 | 7 | Développeur Python Confirmé | Bordeaux | CDI |
| 4 | OFF-00289 | 6 | Data Scientist Lead | Lyon | CDI |
| 5 | OFF-00901 | 6 | Développeur Java Junior | Paris | CDI |
| 6 | OFF-01275 | 6 | Administrateur Bases de Données Lead | Paris | CDI |
| 7 | OFF-01660 | 6 | Architecte Cloud Senior | Lyon | CDI |
| 8 | OFF-02899 | 6 | Data Engineer (Alternance) | Lyon | Alternance |
| 9 | OFF-03126 | 6 | Administrateur Bases de Données Junior | Lyon | CDI |
| 10 | OFF-03145 | 6 | Data Engineer Lead | Montpellier | CDI |

Plusieurs offres sont à égalité à 6 vues (départage par identifiant). Les écarts sont faibles : avec environ 7 000 consultations réparties sur 5 000 offres, chaque offre est vue 1 à 2 fois en moyenne ; aucune offre ne se détache nettement (trafic simulé tiré au hasard). C'est une **jointure applicative** : Elasticsearch ne joint pas deux index, on enchaîne deux requêtes (logs → identifiants → offres). ES|QL propose aussi `LOOKUP JOIN` sur un index en mode `lookup`.

### Exercice 4.5 — Le public

| Système (`user_agent.os.name`) | Requêtes | Part |
| --- | --- | --- |
| Mac OS X | 4 150 | 20,0 % |
| iOS | 4 099 | 19,8 % |
| Windows | 4 058 | 19,6 % |
| Android | 4 056 | 19,6 % |
| Linux | 4 037 | 19,5 % |
| Other (robot zgrab) | 300 | 1,4 % |

**Part du trafic mobile :** iOS + Android = **8 155 requêtes sur 20 700, soit ≈ 39,4 %** (les appareils identifiés : iPhone 4 099, Pixel 9 4 056). Le reste (≈ 60,6 %) vient d'ordinateurs, plus les 300 requêtes du robot.

**Trois navigateurs les plus utilisés (`user_agent.name`) :** **Safari (4 150)**, **Mobile Safari (4 099)**, **Chrome (4 058)** — suivis de Chrome Mobile (4 056) et Firefox (4 037). Le filtre `useragent` distingue les versions de bureau et mobiles : en regroupant par famille, **Safari (bureau + mobile) totalise 8 249 requêtes** et **Chrome (bureau + mobile) 8 114**, loin devant Firefox (4 037). La répartition presque uniforme (≈ 20 % chacun) est un effet du générateur, qui tire le navigateur au hasard parmi cinq.
