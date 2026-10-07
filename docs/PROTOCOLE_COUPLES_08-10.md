# Protocole pré-enregistré — test structurel des couples actif × hypothèse (Partie 3, 08/10/2026)

**Écrit et commité AVANT tout calcul de ce mandat.** Aucune caractéristique,
aucune espérance, aucune corrélation n'a été calculée avant le commit de
ce fichier. Branche `couples-08-10`, créée depuis `fidelite-08-10`.

Teste une hypothèse STRUCTURELLE (une caractéristique de marché mesurée
sur les PRIX SEULS prédit, par hypothèse, quels actifs lui conviennent le
mieux) — jamais un tri post-hoc des cellules (actif, hypothèse) gagnantes
(déjà tenté, déjà invalidé au walk-forward le 05-06/10, bruit).

---

## 0bis. Lecture prudente actée AVANT tout calcul (ambiguïté du mandat)

Le mandat associe deux caractéristiques à H1/H3 dans une seule phrase :
« tendance/ADX (H1) et pullback (H3) → persistance élevée et coût/ATR
faible ». Lu comme une structure parallèle **un-à-un** (H1↔persistance,
H3↔coût/ATR), la seule lecture qui respecte littéralement « UNE seule
caractéristique prédictive » par hypothèse énoncée juste avant. Retenue
telle quelle, jamais réinterprétée après avoir vu un résultat. Mapping
figé :

| Hyp. | Caractéristique unique testée | Sens attendu (prédit une MEILLEURE espérance nette) | Justification théorique (écrite sans avoir regardé aucun résultat) |
|---|---|---|---|
| H1 (tendance/ADX, reprise après croisement) | persistance (VR8) | **élevée** | Une entrée de continuation de tendance a besoin que le mouvement, une fois engagé, tende à se poursuivre (autocorrélation positive des rendements) — sur un actif mean-reverting (VR8<1), le même croisement ADX est plus souvent suivi d'un retournement que d'une poursuite. |
| H2 (confluence multi-TF) | persistance (VR8) | **élevée** | La confluence agrège plusieurs horizons dans le MÊME sens — sa prémisse implicite est qu'un alignement multi-TF, une fois constaté, persiste assez longtemps pour être exploité ; sur un actif mean-reverting, l'alignement se défait vite. |
| H3 (pas de pullback en expansion de volatilité) | coût/ATR | **faible** | Un pullback est une entrée de PRÉCISION (retracement mesuré en fraction d'ATR) — si le spread est grand par rapport à l'ATR, une part disproportionnée du mouvement attendu est consommée par le coût de franchissement avant même le stop/TP, quel que soit le réglage. |
| H4 (divergence RSI/OBV, filtre ADX) | persistance (VR8) | **faible** (signe négatif attendu) | C'est une hypothèse de RETOURNEMENT — elle a théoriquement besoin d'un actif où les mouvements ne persistent PAS indéfiniment (VR8<1), sinon la divergence est noyée dans la tendance qui continue. |
| H5 (compression/expansion de volatilité) | regroupement de volatilité (autocorr `|r|` lag1) | **élevé** | La prémisse explicite de H5 est qu'une période de faible volatilité est suivie d'une expansion — ce n'est exploitable QUE si la volatilité se regroupe en régimes (autocorrélation positive de `|r|`), jamais si elle est i.i.d. d'une bougie à l'autre. |

Si cette lecture s'avère théoriquement intenable en construisant le
code, la correction sera consignée ici, AVANT tout calcul (jamais après
avoir vu un résultat) — non exercé en pratique : le mapping ci-dessus
est jugé défendable tel quel.

## 1. Caractéristiques structurelles (prix seuls, jamais un résultat de stratégie)

Toujours calculées sur la série des prix MID (moyenne bid/ask de clôture,
même convention que `backtest_engine.HistoricalBar.to_candle`), bougies
HOUR (résolution native déployée des 5 hypothèses — voir
`docs/AUTONOMIE_08-10.md` Partie 1, étape 1 : H1/H3/H4 HOUR, H2 HOUR
pour l'entrée, H5 HOUR pour l'entrée). CHFJPY exclue de ce mandat pour
TOUTES les hypothèses (cohérent avec la règle déjà retenue en Partie 1
pour H1-H4 ; étendue ici à H5 par cohérence, jamais mesurée séparément
avant — note : le mandat dit « CHFJPY exclue du M15 », mais aucune
hypothèse déployée n'utilise M15 — lecture prudente retenue : la règle
d'exclusion s'applique telle qu'établie en Partie 1, pas littéralement à
une résolution qui n'existe pas en déploiement réel).

- **(a) coût/ATR** = médiane(spread de clôture = ask−bid) ÷ ATR(14).
  L'ATR(14) est une SÉRIE (lissage de Wilder standard, identique à
  `src.market_data.compute_atr`, mais la série complète est conservée au
  lieu du seul dernier point) ; la valeur retenue pour le ratio est la
  **médiane de cette série sur la fenêtre** (jamais le seul point final,
  pour ne pas dépendre d'un unique instant).
- **(b) persistance** = ratio de variance **non chevauchant** à l'échelle
  q=8 : les rendements logarithmiques 1-bougie sont regroupés en blocs
  consécutifs non chevauchants de 8, sommés (rendement 8-bougies par
  bloc) ; `VR8 = Var(rendements 8-bougies par bloc) / (8 × Var(rendements
  1-bougie, même échantillon complet))`. VR8>1 = persistance (tendance),
  VR8<1 = retour à la moyenne. Les derniers rendements qui ne remplissent
  pas un bloc complet de 8 sont ignorés (jamais complétés par une valeur
  devinée).
- **(c) regroupement de volatilité** = autocorrélation de Pearson entre
  `|rendement_t|` et `|rendement_{t-1}|` sur toute la fenêtre (rendements
  logarithmiques 1-bougie, même série que (b) avant le regroupement par
  blocs).

Données manquantes (bougie absente, bid/ask invalide) : bougie ignorée
(fail-safe), jamais interpolée. Un actif dont moins de 2×8=16 bougies
valides existent sur la fenêtre n'a pas de VR8 calculable (`None`,
jamais une valeur par défaut).

## 2. Fenêtres (anti-fuite, walk-forward à 2 sens)

Deux fenêtres indépendantes, chacune utilisée pour un sens du test :

| Sens | Caractéristique mesurée sur | Espérance (découverte) mesurée sur |
|---|---|---|
| 1 | 2019-01-01 → 2020-01-01 | 2021-01-01 → 2023-01-01 (2021+2022 poolés) |
| 2 | 2019-01-01 → 2021-01-01 | 2022-01-01 → 2023-01-01 |

Jamais la fenêtre scellée 2023-01-01→2024-06-14 (garde par assertion,
même code que `scripts/_fidelite_h1h4_08-10.py`). Rien avant 2019-01-01.
Le tableau descriptif de l'étape 1 (rapport §2) utilise la fenêtre
**complète 2019-01-01→2023-01-01** (avant la fenêtre scellée), à titre
descriptif SEULEMENT — jamais réutilisée comme entrée du test walk-forward
lui-même (qui n'utilise QUE les deux sous-fenêtres du tableau ci-dessus).

## 3. Espérance nette par actif de la baseline v2 (« en découverte »)

Pour chaque hypothèse, moteur `backtest_engine.replay_hypothesis`
**inchangé**, module `evaluate_entry` de la version `_v2` actuellement
déployée (H2 : grille par défaut EMA 50/RSI 50/N_TF 2/score 2/3, seule
config réellement déployée depuis le 25/09 — jamais le combo retiré),
univers = actifs réellement déployés par hypothèse (Partie 1, étape 1),
CHFJPY exclue (règle ci-dessus). Coûts réels (modèle §2.6, inchangé),
refus de resserrement de stop (`src.simulator_fidelity.StopRefusalModel`,
taux mesurés A8, inchangés) et plafond de cluster
(`src.simulator_fidelity.apply_cluster_cap`, inchangé) modélisés — IDENTIQUE
à la méthodologie de `docs/PROTOCOLE_EVOLUTION_V2_06-10.md` §5 (jamais
réécrite ici).

- **Espérance nette brute** = moyenne des `r_multiple_total` des trades
  de cet actif sur la fenêtre de test.
- **Espérance nette contractée** = brute × n/(n+100) (repli vers zéro
  pour les échantillons courts — jamais un intervalle de confiance
  substitué, juste une pénalité de prudence déclarée à l'avance).
- **n ≥ 100 trades requis** pour qu'un actif soit éligible au test de
  corrélation (en dessous : exclu du rang, jamais imputé à 0).

## 4. Test de corrélation structurelle

Par hypothèse, sur les actifs éligibles (n≥100) d'UNE fenêtre :
corrélation de **Spearman** entre le rang de la caractéristique unique
(table §0bis) et le rang de l'espérance nette **contractée** par actif.
**Test de permutation** : 10 000 permutations aléatoires de l'appariement
(actif ↔ espérance), graine fixe `20261008`, p = part des permutations
dont `|rho_permuté| ≥ |rho_observé|` (bilatéral — le sens est vérifié
séparément, voir §5).

**Moins de 6 actifs éligibles sur une fenêtre → « indémontrable »** pour
cette hypothèse (jamais un p-value calculé sur un échantillon d'actifs
trop petit pour être interprétable).

## 5. Règle de validation (walk-forward à 2 sens, Bonferroni m=5)

Une hypothèse est **« structurellement validée »** si et seulement si,
pour LES DEUX sens du tableau §2 :
1. le signe de `rho` correspond au sens attendu (§0bis) ;
2. **p corrigé (Bonferroni, m=5) < 0,05** (donc p brut < 0,01) ;
3. ≥ 6 actifs éligibles (sinon indémontrable pour ce sens, voir §4).

Si un seul des deux sens échoue (signe contraire, p non significatif,
ou indémontrable) : l'hypothèse est **« non validée »** si au moins un
sens a un signe CONTRAIRE avec p corrigé < 0,05 (rejet net) ; sinon
**« indémontrable »** (au moins un sens sous le seuil de 6 actifs, ou
p non significatif des deux côtés sans signe contraire net). Rapporté
sans enjolivement, aucun sous-groupe post-hoc.

## 6. Traçabilité du risque de faux positif cumulé

Ce mandat ajoute **5 tests** (un par hypothèse) à la fenêtre 2019-2022.
Essais déjà comptés sur CETTE fenêtre avant ce mandat (recensement avant
tout calcul, jamais mis à jour après coup pour améliorer un résultat) :
- Sélection par actif (grille de découverte du 02/09/2026) : **24 combos**
  testés (H2-H4 × grilles, voir `docs/DECISIONS.md` 02/09) + re-tuning
  cycles 1-3 des 25-26/08/2026 (**11 + 8 + 8 = 27 candidats** testés sur
  l'entraînement 2019-2022, voir CLAUDE.md "Cycle 2"/"Cycle 3").
- Candidates walk-forward du 06/10/2026 : **4 candidates** (H1, H2, H3,
  H4), grilles de 6 valeurs chacune = 24 variantes testées sur
  apprentissage (jamais 24 tests indépendants au sens de Bonferroni —
  une seule variante retenue par candidate avant tout test sur le test
  set, donc **4 essais** compte pour la multiplicité du verdict, pas 24,
  cohérent avec `docs/PROTOCOLE_EVOLUTION_V2_06-10.md` qui compte m=4).
- **Total avant ce mandat, au sens du nombre de VERDICTS tirés de cette
  fenêtre** : 24 (découverte 02/09) + 27 (cycles 1-3) + 4 (candidates
  06/10) = **55 essais**. + 5 de ce mandat = **60 essais cumulés** sur
  2019-2022 à ce jour. Consigné ici, jamais recalculé après avoir vu un
  résultat de ce mandat.

## 7. Règle d'application (écrite maintenant, avant tout calcul)

- Hypothèse **« structurellement validée »** → préparer une liste
  d'actifs `_v3` = les actifs éligibles (n≥100 sur AU MOINS une des deux
  fenêtres de découverte) dont le rang de la caractéristique est du bon
  côté de la médiane (meilleure moitié) — **MODE SHADOW SEULEMENT**
  (signaux virtuels, structure déjà existante `src/shadow_tracking.py`,
  AUCUN ordre, AUCUNE écriture broker), jugée en forward ensuite (hors
  périmètre de ce mandat — étape 5 construit le code et les tests,
  jamais le déploiement/l'activation).
- Hypothèse **« non validée »** ou **« indémontrable »** → aucune
  évolution, étiquetage seul dans le rapport.
- **Aucune restriction en démo avant un forward confirmé** (même règle
  que `docs/PROTOCOLE_EVOLUTION_V2_06-10.md` §7) — aucune hypothèse
  arrêtée, suspendue ou retirée par ce mandat, quel que soit le résultat.

## 8. Ce que ce protocole ne fait pas

Aucune modification de `risk_engine`/`validator`/`circuit_breaker`/
`executor`/`backtest_engine`/`simulator_fidelity`. Aucun stop élargi.
Aucune hypothèse arrêtée, suspendue ou retirée. Aucun appel broker, aucun
push, aucun déploiement, aucune action VPS. Aucun re-test de la sélection
par actif déjà invalidée (re-balayée nulle part ici — seule la
caractéristique STRUCTURELLE est nouvelle). Les trades live servent
exclusivement à la description de l'étape 4 (n, date n=30) — jamais au
test de corrélation lui-même.
