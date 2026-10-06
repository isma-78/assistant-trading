# Protocole pré-enregistré — test des 4 candidates V2 (06/10/2026)

**Écrit et commité AVANT tout calcul de ce mandat** (aucun grid search,
aucun walk-forward, aucun résultat de simulation n'a été produit avant le
commit de ce fichier — hash noté dans `docs/EVOLUTION_V2_06-10.md`).

Reprend, sans réécriture a posteriori, les 4 candidates de
`docs/EVOLUTIONS_CANDIDATES.md` (horodaté 2026-10-05, commit `6db22aa`).
H5 : **aucune candidate, aucune évolution** — elle reste inchangée (déjà
acté le 05/10). Les trades live du 03/09 au 05/10 restent des données
« vues » : ils ne servent jamais de test, ni ici ni ailleurs. La fenêtre
2023-01-01 → 2024-06-14 reste scellée (garde par assertion dans le code,
reprise de `scripts/_evolution_cells_05-10.py`). Aucune bougie avant
2019-01-01.

---

## 1. Définition exacte de chaque candidate (1 seule variable nouvelle)

### H1/L1 — entrée sur reprise après croisement ADX

Reprise textuelle de `EVOLUTIONS_CANDIDATES.md` : « Attendre la première
reprise dans le sens de la pente (clôture au-delà du plus haut ou plus bas
de la bougie de signal, dans une fenêtre fixe) filtre les croisements
d'épuisement. »

**Mécanique** (module `src/hypothesis1_strategy_v3cand.py`, calcul pur) :
1. Direction = pente de MA(`MA_PERIOD`) à la bougie courante (identique à
   `hypothesis1_strategy_v2._ma_slope_direction`, réutilisée telle quelle).
2. Cherche, dans les `ADX_RESUMPTION_WINDOW_CANDLES` dernières bougies, le
   croisement ADX le plus récent (`adx[i-1] ≤ ADX_THRESHOLD < adx[i]`,
   réutilise `compute_adx_series`) dont la pente à la bougie `i` (calculée
   avec `candles[:i+1]` seulement — causal) confirme la MÊME direction que
   la bougie courante.
3. Si un tel croisement `i` existe : signal seulement si la clôture
   courante dépasse le plus haut (long) / le plus bas (short) de la
   bougie `i`. Sinon : aucun signal (jamais un signal au croisement
   lui-même, contrairement à L1/v2).
4. Stop/TP : identiques à L1/v2 (K_ATR × ATR(14) depuis le prix courant).

**Nouvelle variable AJUSTÉE** : `ADX_RESUMPTION_WINDOW_CANDLES`.
**Grille (6 valeurs, déclarée maintenant)** : {2, 3, 5, 8, 12, 18} bougies
HOUR. **Valeur par défaut du module** (jamais utilisée pour la sélection,
seulement si le module est importé sans override) : 5.
**Figé** : `MA_PERIOD=200`, `ADX_THRESHOLD=25.0`, `K_ATR=2.0`,
`ADX_PERIOD=14`, `SLOPE_LOOKBACK=5` (valeurs déployées de L1/v2,
inchangées).
**Budget** : 3/5 → **4/5**.
**Univers** : identique à L1/v2 (9 actifs, CHFJPY incluse).
**Donnée manquante** (ADX/pente indisponible au croisement candidat) :
croisement ignoré, cherche le suivant — jamais un signal deviné.

### H2/L2 — signal-événement : transition nouvellement établie

Reprise textuelle : « N'entrer que sur la transition "non aligné → aligné"
(au plus k bougies, k figé) rend la logique sélective sans nouvel
indicateur. »

**Mécanique** (module `src/hypothesis2_strategy_v3cand.py`) :
1. Calcule le signal de base EXACTEMENT comme L2/v2
   (`hypothesis2_strategy_v2.evaluate_entry`, réutilisé sans modification).
   Si aucun signal : aucun signal candidate.
2. Si un signal existe (direction `d` à la bougie courante `t` de la série
   native) : vérifie qu'à la bougie `t − TRANSITION_LOOKBACK_CANDLES` de
   la MÊME série native, le vote de confluence (`compute_tf_vote`,
   réutilisé) n'était PAS déjà `d` — recalculé avec les fenêtres H1/H4
   **tronquées aux bougies dont `time_utc` ≤ celui de la bougie `t−k`**
   (jamais les bougies H1/H4 « du futur » relativement à ce point de
   contrôle — anti-lookahead explicite, testé).
3. Si la bougie `t−k` n'existe pas encore (historique insuffisant) :
   aucun signal (fail-safe).
4. Stop/TP/entrée : identiques au signal de base (étape 1), inchangés.

**Nouvelle variable AJUSTÉE** : `TRANSITION_LOOKBACK_CANDLES` (bougies de
la résolution native déployée, HOUR).
**Grille (6 valeurs)** : {1, 2, 3, 4, 6, 8}. **Défaut** : 3.
**Figé** : `EMA_PERIOD=50`, `RSI_THRESHOLD=50.0`, `N_TF=2`,
`SCORE_THRESHOLD=2/3` (grille par défaut RÉELLEMENT déployée depuis le
25/09, jamais le combo retiré).
**Budget** : 4/5 → **5/5, plafond atteint** (aucune liste d'actifs v3
n'a été déployée pour H2 le 05/10 — étiquetage seul — donc ce budget ne
dépasse pas 5).
**Univers** : identique à L2/v2 (8 actifs, CHFJPY exclue).

### H3/L3 — pas de pullback en expansion de volatilité

Reprise textuelle : « Un retracement qui survient pendant une expansion
de volatilité (ATR court > ATR long) a plus de chances d'être un
retournement qu'une pause de liquidité. »

**Mécanique** (module `src/hypothesis3_strategy_v3cand.py`) :
1. Calcule le signal de base EXACTEMENT comme L3/v2
   (`hypothesis3_strategy_v2.evaluate_entry`, réutilisé sans modification).
   Si aucun signal : aucun signal candidate.
2. Si un signal existe : calcule `ATR(ATR_SHORT_PERIOD_FIGÉ)` et
   `ATR(ATR_PERIOD=14)` à la bougie courante (réutilise `compute_atr`,
   déjà causal). Si le ratio court/long > `VOLATILITY_EXPANSION_RATIO` :
   **aucun signal** (expansion détectée, retracement écarté).
3. Donnée indisponible (l'un des deux ATR est `None`) : **le filtre ne
   bloque pas** — le signal de base passe inchangé (le filtre ne peut
   retirer un trade que s'il a la preuve positive d'une expansion ;
   absence de preuve ≠ preuve d'expansion). En pratique inatteignable :
   `ATR(14)` est déjà garanti disponible dès que le signal de base existe
   (documenté dans L3/v2), et `ATR(7)` exige moins d'historique.
4. Stop/TP/entrée : identiques au signal de base, inchangés.

**Constante FIGÉE nouvelle, non balayée** : `ATR_SHORT_PERIOD = 7` (moitié
de `ATR_PERIOD`, choix a priori, jamais balayé — ce n'est pas la variable
ajustée).
**Nouvelle variable AJUSTÉE** : `VOLATILITY_EXPANSION_RATIO`.
**Grille (6 valeurs)** : {1.0, 1.1, 1.2, 1.3, 1.5, 2.0}. **Défaut** : 1.2.
**Figé** : `RETRACEMENT_RATIO=0.5`, `CONFIRMATION_BARS=2`,
`STOP_BUFFER_ATR=0.5` (valeurs déployées de L3/v2).
**Budget** : 3/5 → **4/5**.
**Univers** : identique à L3/v2 (8 actifs, CHFJPY exclue).

### H4/L4 — filtre de force de tendance (ADX)

Reprise textuelle : « Une divergence de retournement échoue plus souvent
en tendance forte [...]. Elle n'est retenue qu'avec ADX(14) sous un seuil
figé. »

**Mécanique** (module `src/hypothesis4_strategy_v3cand.py`) :
1. Calcule le signal de base EXACTEMENT comme L4/v2
   (`hypothesis4_strategy_v2.evaluate_entry`, réutilisé sans modification,
   `require_obv_confirmation=True` inchangé — pas une variable de grille).
   Si aucun signal : aucun signal candidate.
2. Si un signal existe : calcule `ADX(14)` à la bougie courante
   (`compute_adx_series`, réutilisée depuis `hypothesis1_strategy_v2`,
   même convention que L2/v2 réutilisant `compute_rsi_series` de L4/v2).
   Signal retenu seulement si `ADX(14) < ADX_FILTER_THRESHOLD`.
3. Donnée indisponible (ADX nécessite 29 bougies, peut dépasser le besoin
   du signal de base) : **le filtre ne bloque pas** — même règle que H3,
   pour la même raison (absence de preuve de tendance forte ≠ preuve de
   tendance forte). Lecture prudente retenue : un choix différent (bloquer
   par défaut) aurait été également défendable ; celui-ci est choisi pour
   traiter H3 et H4 de façon IDENTIQUE et non arbitraire entre les deux.
4. Stop/TP/entrée : identiques au signal de base, inchangés.

**Nouvelle variable AJUSTÉE** : `ADX_FILTER_THRESHOLD`.
**Grille (6 valeurs)** : {15, 20, 25, 30, 35, 40}. **Défaut** : 25.
**Figé** : `PIVOT_FRACTAL_N=3`, `MAX_PIVOT_DISTANCE_BARS=40`,
`STOP_ATR_MULT=1.5` (valeurs déployées de L4/v2).
**Budget** : 3/5 → **4/5**.
**Univers** : identique à L4/v2 (8 actifs, CHFJPY exclue).
**Règle du 25/09 rappelée** : ne pas empiler sur E1 avant son verdict —
cette candidate reste en test walk-forward/shadow, jamais promue avant
le verdict E1 de L4/v2 lui-même.

## 2. Budget de variables — vérifié avant tout calcul

| Hyp. | Avant | + candidate | Après | ≤ 5 ? |
|---|---|---|---|---|
| H1 | 3 | +1 | 4 | oui |
| H2 | 4 | +1 | 5 | oui, plafond atteint |
| H3 | 3 | +1 | 4 | oui |
| H4 | 3 | +1 | 4 | oui |

Aucun dépassement : les 4 candidates sont recevables. H2 ne pourra plus
recevoir de variable supplémentaire tant que celle-ci est en test.

## 3. H5 — rappel

Aucune candidate, aucune évolution, aucun calcul. Budget inchangé (4/5,
ou 5/5 si une liste d'actifs v3 était un jour déployée — non pertinent
ici).

## 4. Règle de fiabilité du simulateur (gate obligatoire)

Pour chaque hypothèse, écart live/backtest résiduel = |écart moyen R
live − backtest| mesuré en A8 (`docs/APPLICATION_05-10.md` §2,
`scripts/_a8_fidelity_report.py`). **Constat, avant tout calcul de ce
mandat** : A8 n'a mesuré cet écart que pour **H5** (1,284 R, seule
hypothèse comparable en R avant E1 — H1-H4 n'avaient pas de sortie
conforme avant E1, donc aucun écart de R résiduel n'a pu être mesuré pour
elles). **Lecture prudente retenue, notée ici avant tout calcul** :
l'absence de mesure n'équivaut PAS à « écart ≤ 0,30 R » — pour H1, H2, H3,
H4, la fidélité est donc **« non établie »**, et une validation walk-forward
positive pour l'une d'elles sera étiquetée **« non fiable (fidélité) »**
en parallèle de son statut de validation, jamais présentée comme
définitivement actionnable sans cette réserve. La règle numérique
(seuil 0,30 R) reste appliquée littéralement si une mesure existe.

## 5. Protocole walk-forward (étape 2)

**Sélection de la variante (sur apprentissage SEULEMENT)** : pour les 6
valeurs de la grille, rejoue la candidate (moteur corrigé, coûts réels,
refus de resserrement + plafond de cluster modélisés A8) sur la fenêtre
d'apprentissage, univers déployé de l'hypothèse. Élimine les variantes à
n < 200. Parmi les survivantes, retient celle dont **l'espérance nette
brute de la candidate elle-même** (jamais une différence vs baseline à ce
stade — réserve explicite anti-fuite) est la plus élevée. Si aucune
variante ne survit (toutes n < 200) : conclusion directe « indémontrable »
pour ce fold, sans sélection possible.

**Appariement baseline/candidate (sur test SEULEMENT)** : rejoue la
baseline v2 (inchangée) sur la fenêtre de test. Pour CHAQUE trade baseline,
cherche un trade candidate (variante retenue) de même actif, même
direction, horodatage d'entrée à ≤ 2 h (même tolérance que
`scripts/_compare_live_vs_backtest_window.py`). `diff_i = R_candidate − 0`
si aucun match (« non-signalé par la candidate = 0 R »), sinon
`diff_i = R_candidate_matché − R_baseline_i`. `n` = nombre de trades
baseline dans la fenêtre de test (dénominateur commun des deux moyennes).
Un trade candidate SANS trade baseline correspondant (possible : les deux
machines gèrent chacune une position à la fois, indépendamment) est
rapporté à part, jamais inclus dans `diff_i` (transparence, pas de règle
de comptage inventée au-delà de celle explicitement donnée).

**Folds** : (a) apprentissage 2019-01-01→2021-01-01, test 2021 ;
(b) apprentissage 2019-01-01→2022-01-01, test 2022.

**MDE** (calculé avant de lire le signe de la différence) :
`MDE = (z_{1-0,05/4} + z_{0,80}) × s_diff / √n_test`, `s_diff` = écart-type
échantillon des `diff_i` sur 2021+2022 poolés, `n_test` = leur nombre.

**Borne basse** : bootstrap par blocs calendaires (semaine ISO de
l'entrée baseline), 10 000 tirages, graine 20261006, sur `diff_i`
2021+2022 poolés, quantile unilatéral `0,05/4 = 0,0125`.

**Effet attendu** (pour le critère « indémontrable ») : repris
littéralement de `EVOLUTIONS_CANDIDATES.md` (05/10, jamais recalculé) —
brut_min à n=40 : H1 0,24 R ; H2 0,14 R (H2 : 0,13 R à n=53, le jalon qui
s'applique réellement) ; H3 0,13 R ; H4 0,10 R.

**Règle de promotion walk-forward — « validée » si et seulement si** :
D(a) > 0 ET D(b) > 0 ET borne basse corrigée (m=4) > 0 ET
n_test_poolé ≥ 200 ET fidélité simulateur ≠ « non fiable »/« non
établie » (règle 4). **« Indémontrable sur cette fenêtre »** si la
condition précédente échoue ET MDE > effet attendu de l'hypothèse.
**« Non validée »** dans tous les autres cas. Rapportée sans
enjolivement ; aucun sous-groupe post-hoc.

## 6. Jalons forward (étape 4) et correction de multiplicité

**m = 44** pour tout verdict forward de ce mandat (40 + 4 candidates,
décision 2 du 05/10 appliquée + 4 nouvelles). Jalons par candidate :
n ≥ 30/40/53 signaux shadow réconciliés **ET** ≥ 8 semaines depuis T0
(horodatage du redéploiement effectif), le plus tardif des deux. Verdict
forward : borne basse corrigée m=44 > 0 → « confirmée » ; sinon MDE >
effet attendu → « indémontrable » ; sinon « non confirmée ». Alerte
Telegram à chaque jalon (espérance + MDE), aucune action automatique.

## 7. Règle de promotion (actée, jamais automatique)

Une candidate n'est **proposée** à la promotion en démo réelle
(`hypothesisN_v3`, nouvelle époque, compteur à zéro) que si **walk-forward
validé ET forward confirmé**. La promotion reste une décision d'Ismaël,
jamais automatique. Une candidate non validée au walk-forward reste en
shadow (accumulation de donnée), **jamais promue**, **jamais retirée** du
shadow pour autant — elle continue d'être suivie par simple
transparence, sans conséquence sur les hypothèses `_v2` actuelles.
Aucune hypothèse `_v2` ne perd son compteur de verdict avant promotion
effective d'une candidate (qui ouvrirait alors une nouvelle époque
distincte `_v3`, jamais rétroactive sur `_v2`).

## 8. Ce que ce protocole ne fait pas

Aucune modification de `risk_engine`/`validator`/`circuit_breaker`/
`executor`. Aucun stop élargi, aucune moyenne à la baisse. Aucune
hypothèse arrêtée, suspendue ou retirée. Aucun re-balayage des paramètres
déjà déployés (figés). Aucun re-test de la sélection par actif (déjà
non validée le 05/10, jamais rejouée ici). Aucune 6e logique, aucune
variable hors des 4 listées ci-dessus.
