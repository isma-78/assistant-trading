# Bilan complet du système — H1 à H5, depuis le lancement (audit du 24/09/2026)

**Méthode** : lecture directe de la base de production (copie en lecture
seule tirée du VPS le 24/09/2026 18h01 UTC, `data/assistant_trading.db`,
18,7 Mo, 12 621 lignes `trades` tous types confondus), du code déployé
(`src/hypothesis{1..5}_strategy_v2.py`, `src/executor.py`), de l'historique
Git (local et VPS, identiques, `HEAD=8b99d9e`), et de `docs/DECISIONS.md`/
`docs/HYPOTHESES.md`/`CLAUDE.md` ainsi que des documents de travail non
committés (`docs/Prompt_*.md`, `docs/Positions_Fantomes_28-08.md`,
`docs/Portee_Bug_Confirms_28-08.md`, etc., 27/08→01/09/2026). Aucun appel
réseau au broker Capital.com n'a été fait pendant cet audit (risque de
collision de session avec les process VPS) — tout chiffre financier
ci-dessous est recalculé directement depuis les tables `trades`,
`signals`, `risk_decisions`, `circuit_breaker_events`, `envelopes`,
`rule_changes` et `trade_causal_decomposition`, jamais recopié d'un
document antérieur sans revérification.

---

## 0. Vérification préalable

- **8 process autonomes**, tous `up` au 24/09/2026 18h00 UTC (VPS,
  `pgrep`/`tmux ls`) : `telegram_listener`, `control_bot`, `executor`
  (Station X), `trend_executor` (H1), `hypothesis{2,3,4,5}_executor`.
- **Code VPS = code local**, `HEAD=8b99d9e` (04/09/2026 07h08) sur les
  deux copies, aucune divergence de commit.
- **Trou de documentation de 20 jours** : la dernière entrée de
  `docs/DECISIONS.md` date du 04/09/2026 05h06 UTC ("toujours rien,
  10h15 sans déclenchement de coupe-circuit"). Le système continue de
  trader sans interruption depuis, mais **personne ne l'a revérifié
  entre le 04/09 et aujourd'hui** — c'est exactement la fenêtre où les
  constats de la section 1 se sont accumulés en silence.
- **Écart pré-registration/code déployé le plus important trouvé** :
  détaillé en section 2, hypothèse H2 — le combo tournant en direct est
  un combo que la propre recherche du projet a qualifié de **« mort »**
  par écrit (`docs/Prompt_Recalibration_01-09.md`, ligne 43 : *« NE
  REJOUE PAS le combo EMA=20/RSI=55/N_TF=3/SCORE=1.0 : choisi sur
  données contaminées, mort »*) puis reconfirmé négatif (24/24 combos)
  le 02/09/2026 — et qui tourne pourtant, inchangé, depuis.
- Pas de doute sur le statut *actif/clos/suspendu* d'aucune hypothèse
  nécessitant de m'arrêter : les 5 statuts sont documentés sans
  ambiguïté (section 2) — mais un **écart entre le verdict de recherche
  et la configuration réellement déployée** existe pour H2, signalé
  avant tout traitement de ses résultats comme fiables (aucun résultat
  fiable n'existe de toute façon pour la génération actuelle, voir
  section 1).

---

## 1. Résumé chiffré

| | Ancienne génération (16→28/08, remplacée) | Génération actuelle « _v2 » (déployée 30/08, en cours) |
|---|---|---|
| Tentatives de trade (hors backtest) | 363 | 1 864 |
| **Trades clos et chiffrés** (`statut='ferme'`) | **54** | **0** |
| Positions ouvertes actuellement | 0 | 10 (dont 1 depuis 16 jours) |
| Positions « fantômes » (disparues, jamais chiffrées) | 21 | 105 |
| Ordres annulés (jamais remplis) | 288 (79 %) | 1 749 (94 %) |
| Signaux générés (hors backtest) | — | **17 355** au total sur la période 16/08→24/09 |
| Drift de capital démo mesuré | **≈ −91 €** (sur ~40 enveloppes de 500 €) | **0,00 €**, sur les 45 enveloppes actives, confirmé indépendamment sur 3 tables (`trades.r_multiple_total` = NULL à 100 %, `trades.statut` jamais `'ferme'`, `envelopes.capital_courant` = `capital_initial` à la décimale près depuis leur création) |

**R total mesurable, toutes hypothèses confondues** : uniquement sur les
50 trades propres de l'ancienne génération (54 moins 4 exclus pour
contamination connue, voir §4) : **−6,88R au total, moyenne −0,138R/trade,
taux de réussite 44 %, profit factor 0,66, n=50**. Ce chiffre décrit une
configuration **retirée du service le 30/08/2026** — il ne dit rien sur
la génération actuellement déployée, pour laquelle **aucun trade n'a de
résultat chiffré à ce jour**, quelle que soit l'hypothèse.

Tout n ci-dessus, par hypothèse et par actif, est très en-dessous du
seuil de lisibilité du projet (n≥30) — voir le détail par hypothèse en
section 2. **Aucun chiffre de cette section ne doit être lu comme un
verdict sur l'edge d'une hypothèse.**

---

## 2. Statut de fiabilité par hypothèse

Pour chacune, deux verdicts distincts et **volontairement non fusionnés** :
le verdict **recherche** (backtest 2019-2022, avec correction de biais et
gate de puissance, déjà tranché et documenté avant le déploiement du
30/08/2026) et le verdict **forward/direct** (ce que le compte démo a
réellement produit depuis). Les cinq configurations actuellement
déployées le sont **pour la seule collecte forward**, jamais parce
qu'une des cinq porterait un edge confirmé et valide aujourd'hui — règle
pré-enregistrée le 29/08/2026 (*« H1/H3/H4/H5 restent closes côté
recherche mais NE SONT PAS retirées du démo »*, `docs/DECISIONS.md`
ligne 984).

### H1 — régime ADX + pente MA, stop ATR (`hypothesis1_strategy_v2.py`, source `hypothesis_v2`)
- **Variables ajustées** (budget 3/5) : `MA_PERIOD`, `ADX_THRESHOLD`,
  `K_ATR`. Tourne aux valeurs par défaut du code (200/25,0/2,0) —
  **jamais calibrée**, aucune ligne dans `rule_changes`.
- **Verdict recherche** : CLOS, négatif et bien-powered — 36/48 combos
  valides (12 exclus par un bug de fenêtre de lookback, sans incidence
  sur le verdict), TOUS négatifs (−0,108R à −0,259R). Meilleur survivant
  à **−6,0 erreurs-types de zéro**, borne haute à 95 % encore à −0,08R.
  Cohérent avec un test d'information préalable déjà négatif (ADX non
  discernable du R réalisé). (`docs/DECISIONS.md`, 29/08/2026 « suite
  11 ».)
- **Forward/direct** : 411 tentatives depuis le 30/08, **0 clos**, 28
  fantômes, 383 annulés, 0 ouvert. Aucune donnée exploitable.
- **Fiabilité** : verdict recherche fiable et définitif (négatif) ;
  aucune donnée forward disponible pour le contredire ou le nuancer.

### H2 — confluence EMA/Ichimoku(9/26/52)/RSI(14) sur 3 unités de temps (`hypothesis2_strategy_v2.py`, source `hypothesis2_v2`)
- **Variables ajustées** (budget 4/5) : `EMA_PERIOD`, `RSI_THRESHOLD`,
  `N_TF`, `SCORE_THRESHOLD`.
- **Chronologie complète, avec écart** :
  1. 29/08 23h16 — premier backtest : *« CONFIRMÉ POSITIF »*, +0,2431R
     confirmé hors-échantillon (2023-2024.06, n=389, borne basse
     bootstrap +0,1394R). Combo retenu : `EMA=20/RSI=55/N_TF=3/SCORE=1,0`.
  2. **30/08 03h45** — déploiement démo sur ce combo (`012d0d4`), et
     paramètres persistés dans `rule_changes` (seule hypothèse des 5 à
     avoir une ligne `applique`).
  3. **30/08 08h59, moins de 5 heures plus tard** — bug réel de
     lookahead trouvé dans la confluence multi-timeframe (H4/DAY lues
     avant clôture de la bougie) : *« confirmation H2 INVALIDE »*, la
     fenêtre 2023-2024.06 est brûlée définitivement.
  4. 01-02/09 — recalibration complète sur le moteur corrigé, avec
     consigne écrite explicite (`docs/Prompt_Recalibration_01-09.md`,
     ligne 43) : *« NE REJOUE PAS le combo EMA=20/RSI=55/N_TF=3/
     SCORE=1,0 : choisi sur données contaminées, mort »*.
  5. **02/09 19h51** — recalibration terminée : **24 combos sur 24
     négatifs** sur TRAIN 2019-2020 (−0,049R à −0,271R, n=549 à 1750
     par combo). Court-circuit : *« H2 CLOSE côté recherche, verdict
     NÉGATIF »*.
  6. **03/09 16h59 — redémarrage supervisé de `hypothesis2_executor`**,
     confirmé dans les logs : *« EMA_PERIOD=20, RSI_THRESHOLD=55,0,
     N_TF=3, SCORE_THRESHOLD=1,0 — identiques à avant, aucune dérive »*.
- **Écart signalé (invariant #5)** : le combo qui tourne en direct
  aujourd'hui (24/09) est **exactement** le combo déclaré mort par écrit
  le 01/09 et confirmé négatif le 02/09 — jamais révisé depuis. Ce n'est
  pas un oubli de correctif technique : `rule_changes` n'a aucune ligne
  de révocation, et rien dans `docs/DECISIONS.md` ne documente une
  décision explicite de le laisser ainsi en connaissance de cause après
  le verdict du 02/09 (la décision du 29/08 de « garder les hypothèses
  closes en démo » a été prise **avant** ce verdict, sur la base du
  combo alors jugé positif).
- **Forward/direct** : 94 tentatives depuis le 30/08, **0 clos**, 18
  fantômes, **10 ouvertes** (dont USDJPY/CHFJPY/BTCUSD/ETHUSD/US100/
  US30/EURUSD/GOLD/GBPUSD récentes + **1 position CHFJPY ouverte en
  continu depuis le 08/09, soit 16 jours**), 68 annulés.
  `summarize_h2_forward()` (mécanisme dédié du projet) donnait déjà
  **n=0** au 03/09 ; il est resté à 0 depuis, aucun trade H2 ne s'étant
  jamais clos proprement.
- **Fiabilité** : verdict recherche fiable (négatif, deux fois confirmé
  y compris après correction du bug), configuration déployée **non
  conforme au dernier verdict de recherche du projet lui-même**.

### H3 — pullback en tendance, régime structurel réutilisé (`hypothesis3_strategy_v2.py`, source `hypothesis3_v2`)
- **Variables ajustées** (budget 3/5) : `RETRACEMENT_RATIO`,
  `CONFIRMATION_BARS`, `STOP_BUFFER_ATR`. Valeurs par défaut,
  **jamais calibrée**.
- **Verdict recherche** : CLOS négatif — 18/18 combos qualifiés
  (n=609 à 3159), **tous négatifs** (−0,150R à −0,282R), court-circuit
  avant CV imbriquée.
- **Biais de sélection reconnu par construction, dans le code même**
  (docstring `hypothesis3_strategy_v2.py`) : la logique élimine par
  définition les mouvements sans pullback, statistiquement les plus
  profitables — un garde-fou de double-espérance (par signal détecté
  ET par trade exécuté) était prévu « au moment de la calibration »,
  jamais construit puisque la calibration n'a jamais eu lieu.
- **Forward/direct** : 149 tentatives, **0 clos**, 20 fantômes, 1
  ouverte (GOLD depuis le 11/09, 13 jours), 128 annulés.
- **Fiabilité** : verdict recherche fiable (négatif). Aucune donnée
  forward.

### H4 — divergence prix/RSI(14) + OBV sur pivots fractals (`hypothesis4_strategy_v2.py`, source `hypothesis4_v2`)
- **Variables ajustées** (budget 3/5) : `PIVOT_FRACTAL_N`,
  `MAX_PIVOT_DISTANCE_BARS`, `STOP_ATR_MULT`. Valeurs par défaut,
  **jamais calibrée**.
- **Verdict recherche** : CLOS négatif — 27/27 combos qualifiés
  (n=1114 à 3887), **tous négatifs** (−0,108R à −0,215R). Garde-fou OBV
  du pré-enregistrement sans objet (aucun candidat n'a qualifié).
- **Forward/direct** : 167 tentatives, **0 clos**, 20 fantômes, 1
  ouverte (GOLD depuis le 16/09, 8 jours), 146 annulés.
- **Fiabilité** : verdict recherche fiable (négatif). Aucune donnée
  forward.

### H5 — compression Bollinger(20,2σ) → expansion, sortie 100 % trailing (`hypothesis5_strategy_v2.py`, source `hypothesis5_v2`)
- **Variables ajustées** (budget 3/5) : `COMPRESSION_PERCENTILE`,
  `COMPRESSION_DURATION`, `STOP_BUFFER_PCT`. Valeurs par défaut,
  **calibration jamais même lancée**.
- **Verdict recherche** : CLOS au test d'information préalable —
  corrélation sens de cassure / sens du mouvement à 20 bougies non
  discernable de zéro (r=−0,0783, n=447, seuil de discernabilité
  0,0949). Calibration abandonnée sans tentative de grille, conformément
  à la clause pré-enregistrée pour ce cas.
- **Forward/direct** : **1 043 tentatives, le plus gros volume des cinq**
  (concentré GOLD/BTCUSD/ETHUSD), **0 clos**, 19 fantômes, 0 ouverte,
  **1 024 annulés (98,2 % d'échec)** — dominé par `stop_refuse` (619) et
  `autre_echec_placement` (399), eux-mêmes concentrés sur GOLD (561 des
  1 024, 55 %).
- **Fiabilité** : verdict recherche fiable (non concluant, pas négatif
  au sens statistique — simplement aucun signal détecté). Aucune donnée
  forward ; le taux d'échec de placement est si élevé qu'il masquerait
  de toute façon tout signal forward éventuel.

---

## 3. Cause principale, par hypothèse — pourquoi ce résultat, avec des faits

Pour les cinq hypothèses, **la cause du « résultat » actuel n'est pas la
qualité du signal ni la logique de filtre** — c'est un problème
d'exécution en amont de toute question de edge :

- **La panne dominante est structurelle, pas spécifique à une
  hypothèse** : 91,5 % des 2 227 tentatives de trade vivantes de tout le
  projet (hors backtest) ne se sont jamais transformées en position
  chiffrée, avant même de considérer si le signal d'entrée était bon.
  Le taux d'échec existait déjà avant la refonte (79 % de tentatives
  annulées sur l'ancienne génération, 16-28/08) — la refonte du 30/08 a
  **multiplié le volume par ~5** (passage à 9 actifs × 5 hypothèses +
  Station X, contre un déploiement plus restreint avant), ce qui a
  multiplié d'autant l'impact absolu d'un problème déjà présent, sans
  que sa cause ait été réinvestiguée à cette échelle.
- **Asymétrie par actif nette et déjà repérable dans les données** :
  l'échec de placement (`stop_refuse`) est concentré sur GOLD, BTCUSD et
  ETHUSD — cohérent avec un historique déjà documenté par le projet
  (exigence de stop minimum ~1 % sur GOLD, stop garanti obligatoire sur
  les cryptos) qui n'a apparemment pas été reconcilié avec les distances
  de stop, plus resserrées, des nouvelles logiques L1-L5 (stop ATR
  multiplicatif au lieu du stop fixe des anciennes versions).
- **Coût d'exécution réel : non significatif dans les rares données où
  il est mesuré.** `trade_causal_decomposition` (54 lignes, ancienne
  génération) montre un coût d'entrée moyen de −0,0005R à −0,0036R selon
  la source — négligeable face aux amplitudes de ±1 à ±2R observées.
  Sur cet échantillon (n petit), le coût d'exécution n'explique aucun
  signe de résultat.
- **Pertes concentrées ou diffuses ?** Sur les 50 trades propres de
  l'ancienne génération, aucune concentration extrême : la distribution
  va de −1,0R (stop initial, motif le plus fréquent) à +1,6R
  (`take_profit_fixe`, H4), sans valeur aberrante isolée dominant le
  total — érosion plutôt diffuse que choc ponctuel, pour ce qui a pu
  être mesuré.
- **Asymétrie temporelle/session** : non exploitable statistiquement
  (n trop petit partout), mais le projet a déjà documenté et codé un
  filtre d'heures chères pour H1/H3/H4/H5 (spread réel x3-x7 aux heures
  20-22h UTC, `docs/DECISIONS.md` 29/08) — déjà agi, pas un gisement
  inexploité.
- **Incidents techniques ayant concrètement empêché des trades** :
  chiffrés séparément en section 4 — leur impact ne doit jamais
  contaminer le verdict ci-dessus (négatif, ou non concluant pour H5),
  qui repose sur les backtests 2019-2022/2023-2024, **antérieurs** aux
  incidents d'exploitation du déploiement actuel.

---

## 4. Incidents techniques — chiffrés séparément du verdict de chaque hypothèse

1. **Coupe-circuit global `api_errors` : 64h48 de blocage total des
   entrées, toutes hypothèses et Station X confondues**, sur les ~25
   jours de vie de la génération actuelle (11 % du temps).
   - Déclenchement 1 : 31/08 18h24 UTC → levé 03/09 05h06 UTC (58h42),
     cause = tempête de réauthentification de session Capital.com,
     corrigée le 01/09 (`9024fff`).
   - Déclenchement 2 : 03/09 12h44 UTC → levé 03/09 18h51 UTC (6h06),
     cause distincte = `ReadTimeout` réseau sur la sonde de connectivité
     de Station X. Deux correctifs de plomberie appliqués (retry
     manquant sur `manage_open_trades`, notification Telegram
     silencieusement perdue) ; **la cause de fond (latence/contention du
     compte démo Capital.com partagé par 8 process) n'a jamais été
     traitée**, explicitement laissée hors mandat le 03/09 — aucune
     trace d'un chantier dédié depuis.
   - Le projet a lui-même déjà noté que cette fenêtre invalide une
     lecture naïve du forward H2 comme continu (`docs/DECISIONS.md`,
     03/09) — s'applique de la même façon à H1/H3/H4/H5.
2. **Positions fantômes : 126 au total** (21 ancienne génération, 105
   génération actuelle) — positions disparues du broker sans qu'aucune
   clôture n'ait été détectée par le système. **Mécanisme délibéré et
   documenté** (`GHOST_TRADE_STATUS`, `docs/Positions_Fantomes_28-08.md`) :
   aucun prix n'est jamais imputé, aucune statistique n'est jamais
   fabriquée (règle « libérer le créneau, ne pas sauver la donnée ») —
   décision saine et conforme à l'invariant #2. **Ce qui n'est pas
   expliqué** : la fréquence a été multipliée par plus de 10 depuis la
   refonte du 30/08 (105 en 25 jours contre les 5+2 trouvés et corrigés
   les 21 et 28/08 sur la génération précédente) sans qu'une nouvelle
   investigation de cause n'ait eu lieu à cette échelle. Durée de
   détention avant détection : de 4 minutes à 500 heures (21 jours),
   médiane ~15h, répartie sur toute la période — pas un pic isolé.
3. **Échec de placement d'ordre : 2 037 tentatives sur 2 227 (91,5 %)
   n'ont jamais abouti à une position**, dominé par `stop_refuse` et
   `autre_echec_placement`, concentré sur GOLD/BTCUSD/ETHUSD (voir §3).
   Non investigué à ce jour au niveau code (pas de commit ni d'entrée
   dédiée trouvée après le 30/08 sur ce sujet précis, distinct du
   coupe-circuit et des fantômes).
4. **Script de fidélité simulateur cassé depuis le 29/08**
   (`scripts/_compare_live_vs_backtest_window.py`,
   `ModuleNotFoundError: No module named 'src.hypothesis2_strategy'` —
   module renommé par la refonte) : le test de fidélité live/backtest
   du projet est bloqué à 39 paires sans verdict depuis le 30/08/2026,
   jamais réparé. Aucune vérification de fraîcheur du simulateur n'a pu
   avoir lieu depuis.
5. **Bug `/confirms` (28/08, déjà corrigé)** : un `dealReference` de
   clôture non unique pouvait renvoyer la confirmation périmée de
   l'ouverture, produisant une ligne de coût absurde
   (`trade_causal_decomposition.cout_sortie=1,029`). Corrigé par un
   garde-fou arithmétique en code (colonne `invalide`, 1 ligne
   actuellement marquée ainsi) — mentionné pour mémoire, déjà traité
   correctement, sans impact sur les chiffres de ce rapport (la ligne
   invalide est exclue).
6. **Incident historique déjà corrigé et déjà exclu de mes calculs** :
   4 positions ETHUSD/H3 simultanées le 21/08/2026 (fenêtre de course
   d'un garde-fou anti-doublon, corrigée le 25/08) ont gonflé le total
   R de H3/ETHUSD de +8,78R. Exclues via `trades.anomalie_technique`
   (mécanisme déjà en place) — le §2/§4 ci-dessus reflète déjà cette
   exclusion.

---

## 5. Opportunités manquées — analyse rétrospective, hors échantillon de confirmation

**Avertissement explicite** : tout ce qui suit est exploratoire, ne
constitue une preuve pour aucune hypothèse, et ne doit jamais servir à
valider ou invalider un réglage sans repasser par un cycle complet
découverte→confirmation.

- Le volume massif d'échecs d'exécution (§3-4) rend la quasi-totalité
  du « signal non tradé » de la génération actuelle un problème de
  **plomberie**, pas de filtre stratégique trop strict — peu de valeur à
  simuler leur exécution contrefactuelle avant d'avoir corrigé la
  plomberie : on mesurerait un bug, pas un biais de filtre (le
  raisonnement même que le projet a déjà tenu pour la Mesure A,
  `docs/Positions_Fantomes_28-08.md`, §3).
- **Station X** (source discrétionnaire humaine) : sur 170 signaux
  vivants, 58 approuvés / 112 rejetés. L'écrasante majorité des rejets
  (hors coupe-circuit) est un motif unique — *« Prix courant trop
  éloigné du signal »* (péremption §2.8, tolérance = 50 % de la
  distance de stop) — cohérent avec le design (délai de traitement
  humain→machine), **pas** un filtre de confiance trop strict. Aucun
  biais de filtre détectable dans cet échantillon (trop petit, n=112
  rejets, pour distinguer un vrai biais d'un hasard).
- Les rejets `cluster_exposure_cap` et `circuit_breaker_blocked` (v2)
  sont des refus de protection déjà validés par construction (§2.3,
  §2.7) — les assouplir reviendrait à rouvrir ces règles elles-mêmes,
  hors périmètre d'un ajustement d'hypothèse.
- Aucun trade pris qui, selon la logique affichée de son hypothèse,
  n'aurait pas dû l'être, n'a été trouvé dans cet audit — le seul
  incident logique connu (positions H3/ETHUSD dupliquées) est déjà
  documenté comme bug, pas comme piste d'ajustement (§4, point 6).
- **Recommandation méthodologique, pas une exécution faite ici** : une
  simulation contrefactuelle rigoureuse des signaux rejetés par filtre
  de stratégie (par opposition à incident technique) nécessiterait de
  rejouer `backtest_engine.replay_hypothesis` /
  `scripts/evaluate_hypothesis_candidates.py` — déjà construits et
  validés par le projet — sur les fenêtres concernées. Volontairement
  **non lancé dans cet audit** (calcul lourd, périmètre et fenêtre à
  valider avec Ismaël d'abord, et sans intérêt tant que la plomberie
  d'exécution n'est pas réparée).

---

## 6. Décisions nécessitant une validation explicite d'Ismaël

1. **H2 tourne sur un combo que la recherche du projet a qualifié de
   « mort » par écrit** (01/09) et confirmé négatif (24/24, 02/09),
   jamais révisé depuis son redémarrage du 03/09. Trois options
   possibles, aucune tranchée ici : revenir aux valeurs par défaut de
   grille (comme H1/H3/H4/H5), arrêter H2 spécifiquement, ou assumer
   consciemment de le laisser tourner pour la seule collecte forward
   (déjà la règle documentée pour les 4 autres) — mais **la décision
   doit être explicite et datée**, pas un vestige du 29/08 jamais
   révisé. C'est un changement de configuration de risque/exécution en
   direct : invariant #4, validation d'Ismaël requise avant tout
   changement.
2. **Autoriser ou non la réparation du pipeline de réconciliation des
   positions** (0 % de trades chiffrés sur la génération actuelle,
   toutes hypothèses confondues, depuis 25 jours) : c'est un correctif
   d'exécution partagé par les 6 process, classé (a) bug à corriger
   sans attendre — mais son déploiement touche du code partagé par tout
   le programme en direct, donc à valider avant action, conformément à
   la pratique déjà en place dans le projet pour tout déploiement.
3. **Autoriser ou non l'investigation de la distance de stop** sur
   GOLD/BTCUSD/ETHUSD (cause probable de 55-90 % des échecs de
   placement selon l'actif, §3-4) : piste concrète, mais touche une
   grandeur adjacente au risque (distance de stop) — invariant #4,
   validation d'Ismaël requise avant toute investigation touchant ce
   paramètre, même à titre diagnostique.
4. **Position CHFJPY (`hypothesis2_v2`) ouverte en continu depuis 16
   jours** (08/09), GOLD (`hypothesis3_v2`) depuis 13 jours, GOLD
   (`hypothesis4_v2`) depuis 8 jours : vérification opérationnelle de
   leur état réel côté broker recommandée (pas une correction de code),
   étant donné le contexte de fiabilité de reconciliation déjà mis en
   doute par ailleurs.
5. **Poursuite ou pause du programme en l'état** : les 5 hypothèses
   tournent depuis 25 jours sans produire une seule donnée forward
   exploitable, avec un taux d'échec d'exécution de 91,5 % — la
   collecte forward qui justifie leur maintien en démo (§2) est
   actuellement, de fait, impossible. Décision de risque/capital
   réservée à Ismaël (invariant #4) : continuer à exposer le compte
   démo en l'état n'apporte aucun bénéfice de collecte de données tant
   que le point 2 n'est pas traité.
6. **Réparation du script de fidélité simulateur** (§4, point 4) :
   mineure en soi, mais bloque un contrôle qualité du projet depuis
   3 semaines et demie.
7. **Réexamen de la règle « hypothèse close reste en démo pour la
   collecte forward »** (pré-enregistrée le 29/08) : son postulat — que
   les trades s'accumuleraient — ne s'est pas vérifié (0 trade clos en
   25 jours). Vaut la peine d'être révisée à la lumière de ce fait,
   plutôt que laissée comme une règle qui ne produit plus ce pour quoi
   elle a été écrite.

Aucune proposition de ce rapport ne touche à un seuil de confiance, un
plafond de risque, ou un fichier de configuration de production —
conformément au mandat de cette mission (audit et recommandation, pas
déploiement).
