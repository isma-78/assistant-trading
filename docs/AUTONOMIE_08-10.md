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
