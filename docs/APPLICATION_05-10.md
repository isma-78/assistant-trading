# Application du bilan du 05/10/2026 — rapport final

Branche `bilan-05-10`, rien poussé ni déployé, rien en production. 1303
tests passent (1257 avant ce travail + 46 nouveaux), 100% de couverture
maintenue sur `risk_engine`/`capital_manager`/`go_nogo`/`validator`/
`backtest_engine`/`circuit_breaker`/`asset_cell_selection`/
`simulator_fidelity`/`verdict_counter`. `risk_engine.py`, `validator.py`,
`circuit_breaker.py` et `.env` **non modifiés** (vérifié par `git diff`).

Pré-enregistrement de l'étape 4 commité **avant tout calcul** :
`docs/PROTOCOLE_EVOLUTION_05-10.md`, commit `415dbb5` (2026-10-05 20:58:35
+0200).

---

## 1. Résultat de A1 (vérification broker en lecture seule)

**Non exécutée par l'agent.** Le mandat interdisait de créer une session
API ; lire les positions d'un compte Capital.com exige une session
(`POST /session` + `PUT /session`). Le script `scripts/
verify_broker_readonly.py` a été écrit, testé (5 tests, garde-fou démo
vérifié) et commité (`c3acaae`) — **à lancer par Ismaël** sur le VPS
(`venv/bin/python scripts/verify_broker_readonly.py`), étape B7 du runbook.

Aucune position hors base ni sans stop n'a donc pu être confirmée ou
infirmée empiriquement. Ce qui est **établi par la base** (lecture seule,
sans appel broker) :
- **6 ordres de palier "NON annulés"** depuis le 25/09 (H3 : 1, H4 : 5),
  chacun avec un 404 à l'annulation — signe probable d'un remplissage,
  jamais confirmé côté broker. Avec A2 (ci-dessous), ce cas ne se
  reproduira plus ; les 6 occurrences historiques restent à vérifier par
  A1.
- **4 positions du combo H2 retiré** toujours `ouvert` en base depuis 11 à
  27 jours (trades 14877 CHFJPY, 15948 BTCUSD, 15954 ETHUSD, 16329
  GBPUSD), **38,07 € de risque engagé** (approximation depuis
  `trades.risque_eur`), occupant 2 des 5 plafonds de cluster (fx et
  crypto). Traité par A5 (§3).

**Aucun autre constat critique chiffrable sans appel broker.**

## 2. Correctifs A2-A10 et alertes

| # | Fait | Tests | Commit |
|---|---|---|---|
| A1 | script écrit, **non lancé** (voir §1) | 5 | `c3acaae` |
| A2 | oui — ordre non annulable (404) recherché côté broker, enregistré comme jambe ou laissé en attente, jamais hors base | 5 | `bc1a432` |
| A3 | oui — motif `limite_refusee` dédié + trace bid/ask/horodatage (table `logs`) | 4 | `d52d118` |
| A4 | oui — `sys.path` corrigé (le cron échouait chaque nuit depuis le 30/08) + `scripts/backfill_financing.py` (rattrapage idempotent) | 4 | `c76a644` |
| A6 | oui — étiquette `exit_type` H5/L5 = `trailing_pur` (sources `_v2` absentes du mapping) + script de correction des trades déjà en base | 2 | `a8db159` |
| A7 | oui — journaux horodatés UTC (8 process) + watchdog compatible `python -u` (découverte en cours de route : l'ancien motif `pgrep` aurait déclaré morts les 6 exécuteurs après un redémarrage planifié) | 9 (dont 1 corrigé) | `fe7602a` |
| A8 | oui — `backtest_engine.replay_hypothesis(stop_update_filter=...)`, modèle de refus par actif (taux mesurés E1) + plafond de cluster en post-traitement portefeuille ; rapport avant/après exécuté | 11 | `38b30f4` |
| A9 | oui — CHFJPY exclue du compteur H2-H5, compteur factorisé (`src/verdict_counter.py`), "réconcilié" = prix broker sur chaque sortie | 3 | `00dc646` |
| A10 | oui — trace dédiée des `stop_refuse` (seuil divulgué, côté, distance demandée), aucun élargissement, aucun réessai supplémentaire | 2 | `03c59b4` |
| Alertes jalons (décision 7) | oui — Telegram à n=30/40/53 réconciliés avec E[R] du moment, **aucune action automatique**, un jalon annoncé une seule fois par époque | 3 | `e1d1eb6` |

Tous les correctifs sont verts (aucun rouge ni annulé). Aucun n'a changé
une logique d'entrée/sortie, un plafond ou `risk_engine`/`validator`/
`circuit_breaker`.

**Écart au mandat A8, signalé** : "l'écart backtest/démo avant et après"
a été mesuré sur la fenêtre 03/09→25/09 (configurations pré-E1/pré-E2, les
seules couvertes par `data/historical/` téléchargé le 25/09) — pas sur
E1/E2 elles-mêmes (trop peu de jours de bougies HOUR disponibles
localement depuis leur déploiement). Résultats (`scripts/
_a8_fidelity_report.py`, `data/snapshots/a8_report.txt`) :
- Appariement des signaux live/backtest : quasiment inchangé par le
  modèle de refus (ex. H1 85%→80%, H4 54%→54%) — le refus prolonge un
  trade mais ne change pas le fait qu'un signal existait.
- H5, seule hypothèse comparable en R sur cette fenêtre (sortie conforme
  avant E2) : écart R live−backtest sur 13 paires, moyenne **-0,587 sans**
  modèle de refus → **-0,896 avec** — le modèle de refus RAPPROCHE le
  backtest du live (l'écart absolu moyen reste 1,284, inchangé : le
  modèle de refus affecte le signe/la moyenne de l'écart, pas sa
  dispersion).
- Plafond de cluster : 5-20% des trades backtest bloqués (portefeuille
  des 5 hypothèses rejouées ensemble) contre **26-49% des épisodes de
  signal live** bloqués à 100%. Écart réel et large, documenté §4 (§3.2
  de ce rapport).

## 3. A5 et état de H2

**A5 non exécutée** (conditionnée à A1, qui n'a pas pu tourner). Le
script `scripts/close_retired_combo_positions.py` est écrit, testé (1
test), commité (`a34d710`), durci pour qu'un refus "marché fermé" sur un
trade n'interrompe pas les suivants (`0067ab6`) — **à lancer par Ismaël**,
étape B8 du runbook (simulation d'abord, puis `--apply`, BTC/ETH
réalisables samedi, CHFJPY/GBPUSD à reporter au lundi marché ouvert).

**État de H2 : ACTIVE, aucune suspension.** Décision 1 d'Ismaël : la
proposition D1 du bilan (suspendre l'exécuteur) est **annulée**. La
clause de suspension du 02/09 n'est pas remplie (forward H2 n<30,
toujours vrai au 05/10 : n=6 réconciliés en E1, voir `docs/DECISIONS.md`
02/09 et 05/10) — elle reste **non tranchée**, pas "levée" comme les
entrées du 25/09 l'affirmaient (corrigées sur place).

**Taux de blocage par le plafond de cluster, avant/après** (estimation
sur les données disponibles, sans A5 réellement exécutée) :
- **Avant** (positions du combo comptées) : 46 épisodes de signal H1/H3/
  H4/H5 bloqués à 100% par le plafond depuis E1 (contre 39 exécutés,
  bilan du 05/10 §2.4), dont 71-83% des positions bloquantes sont des
  positions H2.
- **Après** (simulation : les 4 positions du combo retirées du calcul
  d'engagement du cluster, `risque_eur` ≈ 20,2€ chacune, re-testées contre
  le plafond de 50€/cluster) : sur les mêmes épisodes, **15/16 H1**,
  **16/16 H3**, **9/10 H4**, **7/9 H5** et **1/2 H2** seraient passés sans
  les 4 positions du combo — soit la quasi-totalité des rejets récents
  s'expliquent par CES 4 positions précises, pas par H2 en général (H2 E1
  elle-même n'a provoqué qu'1 blocage sur 2 observé). **Après A5 réel,
  refaire cette mesure sur des données fraîches** (celle-ci est une
  simulation, pas une observation post-clôture).

## 4. Évolution par l'historique (étape 4)

### 4.1 Matrice hypothèse × actif, découverte 2019-01-01 → 2022-12-31

Configuration réellement déployée (HOUR natif, H2 + HOUR_4/DAY grille par
défaut jamais le combo retiré, H5 + DAY avec filtre TSMOM et trailing
Donchian), coûts §2.6, **avec** modèle de refus de resserrement (A8,
version PRINCIPALE — la version sans modèle est en information dans
`data/snapshots/tables.txt`, écarts mineurs sur les statuts). n_min=100,
contraction n/(n+100) vers l'espérance de l'hypothèse. "non class." =
n<100 ou CHFJPY (exclue de H2-H5 par pré-enregistrement).

Format par cellule : `n / espérance brute / contractée / statut`.

| Hyp. (n total, E[R]) | GOLD | US100 | US30 | EURUSD | GBPUSD | USDJPY | BTCUSD | ETHUSD | CHFJPY |
|---|---|---|---|---|---|---|---|---|---|
| H1 (3122, −0,194) | 326/−0,135/−0,149/non ret. | 353/+0,020/−0,028/non ret. | 335/−0,063/−0,093/non ret. | 339/−0,130/−0,145/non ret. | 333/−0,030/−0,068/non ret. | 352/−0,008/−0,049/non ret. | 375/−0,502/−0,437/non ret. | 391/−0,547/−0,475/non ret. | 318/−0,279/−0,259/non ret. |
| H2 (3173, −0,325) | 286/−0,255/−0,273/non ret. | 467/−0,183/−0,208/non ret. | 326/−0,047/−0,112/non ret. | 506/−0,365/−0,358/non ret. | 455/−0,183/−0,208/non ret. | 338/−0,108/−0,157/non ret. | 254/−0,413/−0,388/non ret. | 541/−0,827/−0,749/non ret. | 0/—/—/non class. |
| H3 (2236, −0,280) | 208/−0,143/−0,188/non ret. | 230/−0,092/−0,149/non ret. | 225/−0,037/−0,112/non ret. | 267/−0,225/−0,240/non ret. | 266/−0,210/−0,229/non ret. | 227/−0,166/−0,201/non ret. | 288/−0,525/−0,462/non ret. | 295/−0,594/−0,515/non ret. | 230/—/—/non class. |
| H4 (1671, −0,113) | 152/−0,089/−0,099/non ret. | 198/−0,007/−0,042/non ret. | 176/+0,008/−0,036/non ret. | 189/−0,065/−0,081/non ret. | 175/−0,202/−0,169/non ret. | 197/+0,080/+0,015/non ret. | 180/−0,231/−0,189/non ret. | 228/−0,329/−0,263/non ret. | 176/—/—/non class. |
| H5 (1189, −0,012) | 131/+0,435/+0,242/non ret. | 118/+0,516/+0,274/**retenue** | 122/+0,024/+0,008/**retenue** | 136/+0,087/+0,045/**retenue** | 118/+0,157/+0,079/non ret. | 148/+0,454/+0,266/non ret. | 207/−0,311/−0,214/non ret. | 209/−0,802/−0,547/non ret. | 0/—/—/non class. |

**Lecture honnête** : H1, H2, H3 n'ont **aucune** cellule retenue (toute
espérance contractée est négative ou instable entre 2019-2020 et
2021-2022). H4 a 0 cellule retenue. Seule H5 a 3 cellules retenues
(US100, US30, EURUSD) — et déjà avec des demi-périodes qui divergent
fortement en magnitude (ex. GOLD : +1,03 en 19-20, −0,15 en 21-22,
contraction positive mais instabilité réelle).

### 4.2 Walk-forward (test de la procédure)

| Hyp. | D(a) 2021 | D(b) 2022 | D poolé | borne basse m=5 | n test retenues/non retenues | Validée |
|---|---|---|---|---|---|---|
| H1 | n/a | n/a | n/a | n/a | 0/1473 | **NON** (aucune cellule retenue dans aucun fold) |
| H2 | n/a | n/a | n/a | n/a | 0/1769 | **NON** (idem) |
| H3 | n/a | n/a | n/a | n/a | 0/966 | **NON** (idem) |
| H4 | n/a | **−0,299** | −0,294 | **−0,660** | 47/419 | **NON** (D(b) négatif : la cellule retenue en fold b, US30, performe MOINS bien que les non retenues sur 2022 — le signe s'inverse) |
| H5 | n/a | n/a | n/a | n/a | 0/215 | **NON** (aucune cellule retenue dans le fold a — historique <100 par demi-période) |

**Aucune hypothèse ne valide la procédure.** La sélection par actif est du
bruit pour les 5 hypothèses sur cette fenêtre, tel que pré-enregistré :
ce résultat n'est "ni enjolivé ni minimisé".

### 4.3 Décision par hypothèse (application) et candidates

| Hyp. | Décision | Compteur perdu |
|---|---|---|
| H1 | **Étiquetage seul**, aucune restriction, époque `E1` inchangée | 0 |
| H2 | **Étiquetage seul**, aucune restriction, époque `E1` inchangée (H2 reste ACTIVE, décision 1) | 0 |
| H3 | **Étiquetage seul**, aucune restriction, époque `E1` inchangée | 0 |
| H4 | **Étiquetage seul**, aucune restriction, époque `E1` inchangée | 0 |
| H5 | **Étiquetage seul**, aucune restriction, époque `E2` inchangée (3 cellules étiquetées "retenue" pour information, AUCUN trading retiré) | 0 |

Étiquettes dans `src/asset_cell_labels.py` (commit `8561a6b`), lues par
**aucun exécuteur** — `PROCEDURE_VALIDATED` est `False` pour les 5,
`restricted_assets()` renvoie toujours `()`. **Aucune hypothèse n'est
arrêtée, aucune liste v3, aucune nouvelle époque.**

**Candidates de l'étape 4.3** (`docs/EVOLUTIONS_CANDIDATES.md`, horodaté
2026-10-05, commit `6db22aa`), une par hypothèse sauf H5, toutes NON
appliquées, jugées seulement sur trades postérieurs à leur horodatage :
- H1 — entrée sur reprise après croisement ADX (filtre l'épuisement) ;
  budget 3/5→4/5 ; gate n≥40, MDE 0,61R, brut_min 0,24R.
- H2 — signal-événement (transition non-aligné→aligné, pas l'état
  persistant) ; budget 4/5→5/5 (plafond, incompatible avec une future
  liste v3) ; gate n≥53, MDE 0,53R, brut_min 0,13R.
- H3 — pas de pullback en expansion de volatilité ; budget 3/5→4/5 ;
  gate n≥40, MDE 0,61R, brut_min 0,13R.
- H4 — filtre de force de tendance (ADX sous seuil) ; budget 3/5→4/5 ;
  gate n≥40, MDE 0,61R, brut_min 0,10R ; **ne pas empiler avant verdict
  E1** (règle du 25/09).
- H5 — aucune candidate (E2 a seulement 7 trades ; empiler inattribuable).

**Lecture honnête du gate** : aucun effet attendu documenté n'approche le
MDE (0,53-0,61R pour H1-H4). À ces jalons, un résultat non significatif
sera **indémontrable sur cette fenêtre**, jamais "invalidé".

## 5. Ce qui est prêt pour samedi 10/10, et ce qu'Ismaël doit faire lui-même

**Prêt (code testé, sur la branche `bilan-05-10`)** :
- Les 9 correctifs A2-A10 + alertes de jalons, 1303 tests verts, 100% sur
  les modules critiques.
- `docs/RUNBOOK_REDEPLOIEMENT_10-10.md` : procédure complète PowerShell →
  VPS, commandes exactes, résultats attendus, retour arrière.
- Aucune liste v3 à activer (étape 4 n'a validé aucune hypothèse).

**À faire par Ismaël, dans cet ordre (détail dans le runbook)** :
1. `scripts/verify_broker_readonly.py` (A1) — sur le VPS, avant tout le
   reste.
2. Selon le résultat : `scripts/close_retired_combo_positions.py` (A5,
   simulation puis `--apply` si souhaité) — BTC/ETH samedi, CHFJPY/GBPUSD
   lundi marché ouvert.
3. Fusion `bilan-05-10` → `main`, push, `git pull` sur le VPS, tests.
4. `scripts/fix_exit_type_labels.py --apply` (A6).
5. Redémarrage des 6 exécuteurs (`scripts/restart_process.sh all`), H2
   compris.
6. `scripts/capture_financing.py` + `scripts/backfill_financing.py
   --from 2026-08-30` (A4).
7. Installer le cron des alertes de jalons (décision 7).
8. Vérifications post-redémarrage (journaux horodatés, watchdog, coupe-
   circuits, plafonds, compteurs).
9. Décision 7 (règle de collecte forward H5, échéance **09-10/10/2026**,
   donc CETTE semaine) — à trancher par Ismaël, indépendamment de ce
   redéploiement ; rien n'est changé automatiquement.

## 6. Ce que je n'ai pas pu faire ou vérifier, et pourquoi

1. **A1 (vérification broker réelle)** : non exécutée — le mandat
   interdisait de créer une session API. Conséquence en cascade : A5
   (clôture du combo) non exécutée non plus, l'estimation "avant/après"
   du plafond de cluster (§3) reste une **simulation sur les données
   actuelles**, pas une mesure post-clôture.
2. **A8 "avant/après" sur E1/E2 elles-mêmes** : mesuré sur la fenêtre
   03/09→25/09 (pré-E1/pré-E2) à la place, faute d'historique HOUR local
   couvrant E1/E2 au-delà de quelques jours — `data/historical/` a été
   copié depuis le VPS une seule fois (lecture seule) le 05/10, ancré à
   cette date.
3. **Taux de refus de resserrement par actif (modèle A8)** : mesurés sur
   E1 seulement (toutes hypothèses poolées), pas par hypothèse — les
   échantillons par (hypothèse, actif) étaient trop petits (parfois n=1)
   pour un taux propre à chacun. US30/GBPUSD/CHFJPY (n<10) utilisent le
   taux global (0,197), pas un taux spécifique.
4. **Walk-forward H4/H5** : les bornes bootstrap par blocs calendaires
   (semaine ISO) peuvent être larges quand peu de semaines distinctes
   existent dans un groupe — non recalculé avec un découpage plus fin que
   celui pré-enregistré (aucune latitude laissée par le protocole commité
   avant les calculs).
5. **Rétrospective causale (étape 4.3)** : classification des pertes
   "stop initial < 6h" vs "≥ 6h" est une heuristique de durée, pas une
   relecture trade par trade de la cause de marché — suffisante pour
   motiver une justification théorique, pas une preuve.
6. **Historique Capital.com M15/HOUR_4/DAY pour la fenêtre 2023-2024.06** :
   jamais chargé, jamais regardé (fenêtre scellée, vérifié par assertion
   dans `scripts/_evolution_cells_05-10.py`, ligne `assert all(START <=
   b.time_utc < END ...)`).
7. **Position Station X** : hors périmètre de ce mandat (H1-H5
   uniquement), non retouchée.
8. **Tests manuels sur un compte démo séparé** pour A2 (confirmation
   qu'un ordre "introuvable" côté broker correspond bien à un trade
   annulé et non à un délai de propagation Capital.com) : non faits — le
   correctif est couvert par 5 tests unitaires avec client mocké, pas par
   un test d'intégration réel (règle CLAUDE.md : jamais d'ordre manuel sur
   le compte de production).
