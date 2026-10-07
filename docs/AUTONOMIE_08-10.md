# Journal d'autonomie — mandat du 08/10/2026, Partie 1

Consigne les lectures prudentes prises en cas d'ambiguïté, comme demandé
par le mandat. `docs/PROTOCOLE_AUTONOME_08-10.md` fait foi et n'est
jamais modifié par ce journal.

## Étape 1 — scripts/rafraichir_historique.py

- **27 combinaisons confirmées par code, pas supposées** : H1 (`trend_
  executor.HYPOTHESIS_ASSETS`, 9 actifs) × HOUR fixe ; H3/H4
  (`HYPOTHESIS3_ASSETS`/`HYPOTHESIS4_ASSETS`, 9 actifs chacun) ×
  résolution lue via `hypothesis_params.get_resolution_override(db_path,
  "H3_v2"/"H4_v2", "entree", "HOUR")` sur `data/snapshots/prod_07-10.db`
  — **aucun override actif trouvé** (`rule_changes` vide pour ces
  variables), repli codé HOUR confirmé, pas supposé ; H2
  (`HYPOTHESIS2_ASSETS`, 9 actifs) × HOUR + confirmation croisée
  HOUR_4/DAY (`H2_EXTRA_RESOLUTIONS`, valeur de code reprise de
  `hypothesis2_executor.py`, pas un override DB, pas une constante
  exportée par ce module à ce jour). Union = 27 (actif, résolution),
  qui coïncide exactement avec les 27 fichiers déjà présents dans
  `data/historical/` (9 actifs × {HOUR, HOUR_4, DAY}).
- **CHFJPY absent de `data/instrument_specs.json`** (whitelist étendue
  le 28/08/2026, après la dernière extraction `discover_instruments.py`
  du 16/08/2026, jamais relancée depuis). Lecture prudente retenue :
  repli du tick CHFJPY sur celui d'USDJPY (même convention de cotation
  JPY, voir commentaire `src/asset_whitelist.py`), plutôt qu'une valeur
  inventée ou un blocage total du rafraîchissement de cet actif.
- **Chevauchement d'intégrité (§2 du protocole) mesuré sur `closePrice.
  bid/ask`** (écart ask−bid), jamais fusionné dans le fichier (les
  bougies de la fenêtre de chevauchement servent UNIQUEMENT à la
  comparaison, jamais réécrites) — lecture jugée la plus fidèle à
  « fusion append-only », une bougie déjà locale n'étant jamais
  remplacée même si le broker renvoie une valeur légèrement différente
  pour le même horodatage.
- **Bug réel trouvé et corrigé pendant la construction des tests**
  (avant tout appel broker réel) : la garde "rien avant 2019-01-01" /
  "fenêtre scellée" vérifiait initialement la liste FUSIONNÉE entière
  (`existing + nouvelles bougies`), qui contient légitimement des
  bougies antérieures à 2019 dans les fichiers déjà présents (données
  brutes non filtrées, voir CLAUDE.md "ne jamais utiliser de bougies
  antérieures au 2019-01-01" — une contrainte d'ANALYSE, pas une
  garantie sur les fichiers bruts eux-mêmes). Le premier essai contre
  `data/historical/BTCUSD_DAY.json` réel aurait levé une
  `AssertionError` dès le premier appel. Corrigé : les deux assertions
  (2019-01-01, fenêtre scellée) ne portent plus que sur les bougies
  **nouvellement ajoutées** (`_new_rows`), jamais sur l'historique déjà
  présent.
- **Second bug trouvé pendant les tests** : `historical_dir` comme
  valeur par défaut d'argument (`historical_dir: Path = HISTORICAL_DIR`)
  se fige à la définition de la fonction — un `monkeypatch` du module
  après import n'a aucun effet sur cette valeur déjà liée. `main()`
  aurait silencieusement lu/écrit dans le VRAI `data/historical/` du
  projet pendant les tests. Corrigé : `historical_dir` résolu dans le
  corps de `main()`, jamais comme défaut d'argument figé, et transmis
  explicitement à `refresh_combination`.
- Tests unitaires : 31 tests, broker entièrement mocké (`FakeClient`,
  aucun réseau), **100% de couverture de ligne** vérifiée
  (`coverage run --include="*rafraichir_historique.py"`). Une seule
  ligne exclue par `pragma: no cover` : l'instanciation du vrai
  `CapitalClient` (jamais exercée par des tests qui mockent le broker
  par construction).

## Étape 2 — exécution réelle

27/27 combinaisons `ok`, 0 abandon d'intégrité, 0 429, 27/200 appels.
Rien à signaler.

## Étape 3 — scripts/_fidelite_h1h4_08-10.py

- Suit le patron de `_fidelite_h1h4_07-10.py` (jamais réutilisé ni
  agrégé avec cette mesure — séries distinctes), avec la fenêtre et la
  règle CHFJPY du nouveau protocole (§1).
- **Pas d'accès VPS dans cette Partie 1** : le live est lu sur
  `data/snapshots/prod_07-10.db`, l'instantané local le plus récent
  disponible (relevé 06/10/2026 ~19:51, pas la base de production
  actuelle du VPS qui a continué à tourner depuis). Conséquence
  documentée en §6 du rapport final : les n rapportés ici sont une
  borne basse à la date de l'instantané, pas à la date de ce mandat.
- **Aucune modélisation** d'une cause ≥20% (contrairement au protocole
  du 07/10 qui l'autorisait à l'étape 2 suivante) — ce mandat se limite
  à mesurer et proposer à un mandat séparé, conformément à l'invariant
  "aucune modification du simulateur" du protocole du 08/10.
- Coûts/financement (second volet de l'étape 3) mesurés par requête SQL
  directe sur `trade_causal_decomposition`/`financing_transactions`
  (pas un nouveau module) — couverture quasi complète pour
  entrée/sortie (37/37 et 34/37 sur les 37 trades éligibles poolés),
  **0/37 pour le financement** : les 12 lignes connues de
  `financing_transactions` datent toutes du 28-29/08/2026, avant le
  plancher de population (25/09 19:35) — cohérent avec le constat du
  bilan du 05/10 (A4 non corrigé, cron en échec quotidien). Pas de
  rattrapage tenté (hors périmètre, "ne comble pas").

## Étape 4 — scripts/_execution_metrics_08-10.py

- Taux de refus FINAL de resserrement de stop : non remesurable sur
  cette population. Lu le code avant de tenter un calcul
  (`src/executor.py`, fonction de mise à jour du stop) : un refus de
  resserrement n'est journalisé que par log applicatif dans les
  fichiers du VPS, jamais dans la base ni dans la table `logs` (vide
  sur cet instantané — la trace dédiée en base reste une proposition
  non déployée). Aucun accès VPS dans cette Partie 1 : les seuls
  chiffres disponibles restent ceux de `docs/BILAN_05-10.md` §2.3
  (mesurés depuis les fichiers de log pendant une session avec accès
  VPS), repris tels quels dans le rapport final, jamais présentés comme
  remesurés ici.
- Le motif dédié `limite_refusee` (A3) est déjà déployé dans le code
  (`executor._classify_placement_failure`) — mais 0 occurrence sur
  cette population : les 76 échecs de placement poolés sont tous
  `autre_echec_placement`. Le motif dominant du 05/10 (limit.price) ne
  semble donc plus être la cause principale sur cette fenêtre ; cause
  exacte non disambiguée sans accès aux logs VPS.
- Délai de remplissage non mesurable au sens voulu avec le schéma
  actuel : `trades` n'a pas de colonne distincte pour l'instant de
  placement. L'approximation tentée (ouverture moins snapshot de
  marché du signal) rend systématiquement ~0s sur les 45 trades remplis
  — signe que la colonne d'ouverture est fixée au moment de la création
  de la ligne, pas à la confirmation réelle de remplissage. Rapporté
  comme non mesurable, jamais comme un délai nul.
- Écart de spread signal vs référence horaire : jamais poolé en valeur
  brute entre actifs (échelles de prix incomparables). Corrigé avant
  publication du résultat : rapporté par actif uniquement.
- Seule métrique poolée atteignant n>=30 : le taux de remplissage
  poolé (n=53, 84,9%). Toutes les autres cellules (hypothèse, actif)
  restent sous le seuil, rapportées comme observation brute, jamais
  comme un verdict.
- `brut_min` par hypothèse : MDE0 réutilisé tel quel du jalon n=30,
  sigma proche de 1R de `docs/BILAN_05-10.md` paragraphe 4 (0,69 R)
  pour les 4 hypothèses, aucune valeur recalculée.
- E1/E3/E4 non définis ailleurs dans le dépôt (recherche explicite
  avant d'écrire quoi que ce soit : seul E2 est défini dans le
  protocole §4). Lecture prudente retenue : E1/E3/E4 traités comme des
  étiquettes de protocole pour les 3 frictions non-E2 les plus
  coûteuses identifiées par cette étape, jamais comme des mécanismes
  préexistants mal cherchés. Détail dans le rapport final.

## Reprise du 08/10/2026 — état du nettoyage, checklist, déploiement

- **Règle des 120 minutes ajoutée** à `scripts/nettoyage_broker_08-10.py`
  (`ORPHAN_MIN_AGE_MINUTES=120`, `position_age_minutes`/`is_confirmed_
  orphan`, 12 tests nouveaux) : une position (b)/(d) hors base n'est
  fermée que si son âge dépasse 120 minutes — cas réel observé (H2
  US100, 81 minutes au moment de la lecture) correctement écarté de
  toute action.
- `--apply` tenté une fois comme demandé, refusé par le classificateur
  de sécurité du harnais — jamais contourné, marqué « à lancer par
  Ismaël » avec la commande exacte dans `docs/CHECKLIST_10-10_UNIQUE.md`.
- `docs/CHECKLIST_10-10_UNIQUE.md` : le mandat supposait ce fichier
  absent ; il existait déjà (créé Partie 4, section shadow_couples
  seule). Réécrit en entier avec la séquence complète des 9 étapes,
  en conservant/renumérotant la section déjà écrite (étape 7).
