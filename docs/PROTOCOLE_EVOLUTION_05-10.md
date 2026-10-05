# Protocole pré-enregistré — évolution des hypothèses par la liste d'actifs (05/10/2026)

**Écrit et commité AVANT tout calcul de backtest de l'étape 4.** Aucun
chiffre de cellule (hypothèse × actif), aucune espérance par actif sur
2019-2022, aucun résultat de walk-forward n'a été calculé avant le commit
de ce fichier. Le hash du commit est noté dans `docs/APPLICATION_05-10.md`.

Données « vues » : tous les trades live jusqu'au 05/10/2026 inclus (et
tous les chiffres du bilan du 05/10) ont déjà été regardés. **Ils ne
servent jamais de test** de ce protocole, ni pour la sélection d'actifs,
ni pour la validation. La fenêtre de confirmation 2023-01-01 → 2024-06-14
reste **scellée** : aucune bougie de cette fenêtre n'est chargée. Aucune
bougie antérieure au 2019-01-01 n'est utilisée.

---

## 1. Variable d'évolution et budget (invariant #10)

Une seule variable d'évolution par hypothèse : **la liste d'actifs
autorisés** (univers de trading), choisie parmi les 9 actifs déployés
(GOLD, US100, US30, EURUSD, GBPUSD, USDJPY, BTCUSD, ETHUSD, CHFJPY).

| Hypothèse | Variables ajustées avant | + liste d'actifs | Après | Plafond 5 |
|---|---|---|---|---|
| H1/L1 | 3 | +1 | 4 | respecté |
| H2/L2 | 4 | +1 | 5 | respecté, **plafond atteint** |
| H3/L3 | 3 | +1 | 4 | respecté |
| H4/L4 | 3 | +1 | 4 | respecté |
| H5/L5 | 4 (dont TSMOM, E2) | +1 | 5 | respecté, **plafond atteint** |

Aucune hypothèse ne dépasse 5 : les cinq peuvent évoluer par cette voie.
Pour H2 et H5, plus aucune variable ne pourra être ajoutée ensuite sans
en retirer une.

Plancher de 10 trades par variable : seuil de verdict d'une hypothèse
évoluée = max(30, 10 × variables), arrondi au jalon supérieur de la
décision 3 (30/40/53) : **H1, H3, H4 → 40 ; H2, H5 → 53**.

## 2. Justifications théoriques (écrites sans aucun résultat du projet)

Principe commun : une règle de trading n'a pas de raison d'avoir la même
espérance sur tous les marchés. Trois mécanismes généraux, documentés hors
de ce projet, font varier son efficacité d'un actif à l'autre :
(i) la **persistance** des mouvements (tendance ou retour à la moyenne)
propre à chaque marché et à chaque échelle de temps ; (ii) la structure de
**volatilité** et de sessions (marchés 24/7 ou 24/5, ouvertures, gaps) ;
(iii) le **coût relatif** : le spread rapporté à l'amplitude typique d'un
mouvement (coût/ATR). Une règle dont l'avantage brut est faible n'est
rentable que là où ce coût relatif est petit.

- **H1/L1 — régime ADX + pente de MA.** Une entrée sur tendance établie
  profite des marchés où le momentum de série temporelle est persistant
  (littérature : Moskowitz, Ooi & Pedersen 2012 ; plus marqué sur
  matières premières et certaines devises que sur les indices actions à
  l'échelle intrajournalière, où le retour à la moyenne est fréquent).
  Attendu : meilleure efficacité sur les actifs à tendances longues et à
  coût/ATR faible.
- **H2/L2 — confluence multi-unités de temps.** L'alignement de plusieurs
  échelles n'apporte de l'information que si la persistance existe à ces
  échelles (structure fractale, exposant de Hurst > 0,5). Sur un marché
  proche de la marche aléatoire à l'échelle horaire, la confluence est
  presque toujours vraie ou presque toujours fausse sans valeur
  prédictive. Attendu : efficacité concentrée sur les actifs à dérive
  persistante multi-échelle.
- **H3/L3 — pullback en tendance.** Acheter un retracement suppose une
  tendance de fond qui reprend après une respiration. Cela fonctionne là
  où les retracements sont des pauses de liquidité (marchés profonds, à
  tendance) plutôt que des retournements, et où le stop placé au-delà du
  retracement reste grand devant le spread.
- **H4/L4 — divergence RSI/OBV (retournement).** Un signal de
  retournement a un avantage sur les marchés à retour à la moyenne à
  l'échelle horaire (paires de devises sans dérive forte, indices en
  intrajournalier) et un désavantage sur les marchés à tendance forte. La
  jambe OBV repose sur un volume « tick » de CFD, dont la qualité varie
  selon l'actif.
- **H5/L5 — compression → expansion (+ filtre TSMOM).** Le regroupement de
  volatilité (Engle 1982, Bollerslev 1986) annonce QUAND, pas dans quel
  sens. Le gain dépend de la poursuite du mouvement après l'expansion, plus
  fréquente sur les marchés à momentum persistant (matières premières,
  crypto), et du rapport entre largeur de compression et spread.

Ces mécanismes disent POURQUOI la liste d'actifs peut compter. Ils ne
disent pas QUELS actifs : la sélection est faite par la règle mécanique
ci-dessous, et sa valeur est testée par le walk-forward (§4) puis en
forward (§5). Une sélection non validée est traitée comme du bruit.

## 3. Règle de classement des cellules (hypothèse × actif)

**Données** : découverte 2019-01-01 → 2022-12-31 uniquement (bougies
antérieures au 2023-01-01 ; un trade non clos au 31/12/2022 est ignoré).

**Moteur et configuration** : `backtest_engine.replay_hypothesis` (moteur
corrigé du lookahead du 30/08), configuration RÉELLEMENT déployée de
chaque hypothèse (résolution HOUR ; H2 + HOUR_4/DAY aux valeurs de grille
par défaut ; H5 + DAY avec le filtre TSMOM d'E2 et trailing Donchian ;
H3/H4 sans confirmation de régime ; paramètres par défaut des modules,
aucun override). Coûts : modèle §2.6 du moteur (spread bid/ask réel de
chaque bougie, slippage de sortie = 100 % du spread, financement 1 pb/jour,
toujours un coût). Écarts connus avec le live, non modélisés : filtre
« heures chères », plafond de cluster (effet de portefeuille, hors d'une
cellule), remplissage différé.

**Refus de resserrement de stop (A8)** : modélisé si A8 est prêt avant le
calcul. Probabilité de refus par mise à jour de stop (trailing et passage
au breakeven) = taux de refus FINAL mesuré par actif sur les journaux E1
(bilan du 05/10, toutes hypothèses poolées) : BTCUSD 0,458 ; GOLD 0,306 ;
EURUSD 0,176 ; ETHUSD 0,065 ; US100 0,023 ; USDJPY 0,0 ; actifs avec moins
de 10 demandes (US30, GBPUSD, CHFJPY) : taux global 0,197. Tirage
pseudo-aléatoire à graine fixe (20261005), stop inchangé en cas de refus.
Si A8 n'est pas prêt : « fidélité simulateur non établie » est écrit à
côté de chaque résultat. Le classement est fait sur la version AVEC
modèle de refus. La version sans modèle est rapportée pour information
et ne décide de rien.

**Par cellule** :
- n = nombre de trades fermés ; m_cell = espérance nette (R moyen, coûts
  inclus) ; m_hyp = espérance nette de TOUS les trades de l'hypothèse
  (toutes cellules) sur la même période.
- Espérance contractée : `c = w·m_cell + (1 − w)·m_hyp`, avec
  `w = n / (n + 100)`.
- Statut :
  - **non classable** si n < 100, ou si l'historique nécessaire est
    absent, ou si l'actif est exclu du pré-enregistrement de
    l'hypothèse (CHFJPY pour H2-H5, décision 4 du 05/10) ;
  - **retenue** si c > 0 ET si l'espérance brute de la cellule est
    **strictement positive sur chacune des deux moitiés** de la période
    d'apprentissage (2019-2020 et 2021-2022 pour la matrice finale).
    Lecture prudente retenue à l'écriture : « signe identique » est lu
    comme « identique ET positif » ;
  - **non retenue** sinon.

## 4. Test de la procédure : walk-forward (avant toute application)

- (a) Règle du §3 apprise sur 2019-2020 (moitiés : 2019 / 2020), cellules
  classées, puis évaluée sur 2021.
- (b) Règle apprise sur 2019-2021 (moitiés de même durée :
  2019-01-01 → 2020-06-30 / 2020-07-01 → 2021-12-31), évaluée sur 2022.
- n_min = 100 dans chaque fenêtre d'apprentissage, sans ajustement.
- Mesure par hypothèse et par fold : `D = moyenne R des trades de test des
  cellules retenues − moyenne R des trades de test des cellules non
  retenues`. Les cellules non classables sont exclues des deux groupes.
- IC : bootstrap par blocs calendaires (semaine ISO de l'heure d'entrée,
  UTC), 10 000 tirages, graine 20261005, sur les trades de test 2021 + 2022
  poolés (chaque trade gardant le statut que lui donne SON fold). Borne
  basse unilatérale corrigée m = 5 (quantile 1 %).
- **Procédure validée** pour une hypothèse si et seulement si D(a) > 0 ET
  D(b) > 0 ET borne basse > 0. Si un groupe est vide dans un fold, ou si
  aucune cellule n'est retenue : **non validée**. Non validée = la
  sélection par actif est du bruit pour cette hypothèse, sur cette fenêtre.

## 5. Application et test forward

**Application (redéploiement du 10/10, après validation d'Ismaël)** :
- procédure validée → liste v3 = cellules retenues + non classables (de la
  matrice finale 2019-2022). Les cellules non retenues passent en
  observation `shadow` (signal journalisé, résultat virtuel simulé, aucun
  ordre). Nouvelle époque `hypothesisN_v3`, compteur de verdict à zéro.
  Les trades antérieurs sont archivés comme données de découverte ;
- procédure non validée, ou aucune cellule retenue → **aucune
  restriction**. Chaque cellule reçoit seulement une étiquette, toutes
  continuent à trader en démo, époque inchangée.
- Aucune hypothèse n'est arrêtée, dans aucun cas.

**Test forward pré-enregistré** (hypothèses avec liste v3 seulement) :
- T0 = horodatage du redéploiement (époque `hypothesisN_v3`).
- Fin de la période de test : le plus tardif de (a) n_retenues ≥ jalon de
  l'hypothèse (H1/H3/H4 : 40 ; H2/H5 : 53, voir §1) et (b) 8 semaines
  après T0. Aucun regard sur la différence avant cette fin. Le MDE est
  rapporté à chaque jalon 30/40/53.
- Critère : `Δ = espérance nette (trades réels des cellules retenues) −
  espérance nette (résultats virtuels shadow)`, borne basse de l'IC
  bootstrap par blocs calendaires (semaines), unilatérale, corrigée
  **m = 40** (quantile 0,125 %).
  - borne basse > 0 → **évolution confirmée** ;
  - sinon, si MDE > effet attendu (= D poolé du walk-forward de
    l'hypothèse) → **indémontrable sur cette fenêtre** ;
  - sinon → **non confirmée**, retour à la liste complète (nouvelle
    époque, sans arrêt de l'hypothèse).
  - MDE = (z₁₋₀,₀₅/₄₀ + 0,8416) × √(s_r²/n_r + s_s²/n_s). Si n_s < 10 à la
    fin de la période : indémontrable.
- Comparaisons multiples : m = 40 (35 + 5 évolutions, décision 2). Si le
  nombre d'évolutions effectivement déployées change, m est recalculé
  AVANT tout regard (35 + nombre d'évolutions déployées).

## 6. Ce que ce protocole ne fait pas

Aucun changement de logique d'entrée ou de sortie, aucun paramètre
re-calé, aucune 6e logique, aucune hypothèse arrêtée. Les candidates de
mécanisme de l'étape 4.3 (`docs/EVOLUTIONS_CANDIDATES.md`) ne servent ni à
la sélection d'actifs ni à ce test.
