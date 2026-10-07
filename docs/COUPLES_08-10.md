# Rapport final — Partie 3 : test structurel des couples actif × hypothèse (08/10/2026)

Branche `couples-08-10` depuis `fidelite-08-10`. Protocole :
`docs/PROTOCOLE_COUPLES_08-10.md` (commit `8ad1bda`, inchangé après
écriture). Aucun appel broker, aucun push, aucun déploiement, aucune
action VPS, aucune hypothèse arrêtée.

## 1. Verdict en 5 lignes

**0/5 hypothèses structurellement validées, 0/5 non validées, 5/5
indémontrables.** Aucune n'atteint le seuil de 6 actifs éligibles
(n≥100 trades) dans les DEUX sens du walk-forward — H2 est la seule à
en approcher un (7 éligibles en sens1, mais seulement 3 en sens2) ; les
4 autres ont 0 à 5 actifs éligibles sur au moins un sens. Conséquence
directe du protocole §7 : **aucune évolution, étiquetage seul**, aucune
liste `_v3` préparée.

## 2. Tableau actif × caractéristiques (fenêtre descriptive 2019-01-01 → 2023-01-01 EXCLU, soit 2019-2022 inclus — titre corrigé le 08/10/2026, voir docs/DECISIONS.md : étiquette seule, aucune valeur recalculée)

| Actif | n bougies HOUR | coût/ATR | persistance (VR8) | regroupement de volatilité |
|---|---|---|---|---|
| GOLD | 23 728 | 0,0687 | 0,9778 | 0,2809 |
| US100 | 23 654 | 0,0400 | 0,9196 | 0,3238 |
| US30 | 23 622 | 0,0385 | 0,8970 | 0,3653 |
| EURUSD | 24 952 | 0,0472 | 0,9182 | 0,2823 |
| GBPUSD | 24 974 | 0,0680 | 0,9581 | 0,3037 |
| USDJPY | 24 953 | 0,0688 | 0,9820 | 0,3002 |
| BTCUSD | 34 846 | 0,2762 | 0,9467 | 0,2747 |
| ETHUSD | 34 843 | 0,5584 | 0,9160 | 0,2552 |

Deux constats descriptifs (jamais utilisés comme verdict) : le
regroupement de volatilité est **positif pour les 8 actifs** (cohérent
avec la littérature — bon signe de correction du calcul) ; la
persistance (VR8) est **systématiquement < 1** (biais léger de retour à
la moyenne à l'échelle 8 bougies HOUR, aucun actif nettement trending à
cette échelle sur 2019-2022).

## 3. Test structurel par hypothèse

| Hyp. | Caractéristique (sens attendu) | Sens 1 : rho / p corrigé / éligibles | Sens 2 : rho / p corrigé / éligibles | Statut final |
|---|---|---|---|---|
| H1 | persistance VR8 (positif) | — / — / **5** (< 6) | — / — / **0** | indémontrable |
| H2 | persistance VR8 (positif) | **-0,536** / 1,0 / **7** | — / — / **3** (< 6) | indémontrable |
| H3 | coût/ATR (négatif) | — / — / **2** (< 6) | — / — / **0** | indémontrable |
| H4 | persistance VR8 (négatif) | — / — / **0** | — / — / **0** | indémontrable |
| H5 | regroupement de volatilité (positif) | — / — / **0** | — / — / **0** | indémontrable |

Seule H2/sens1 a assez d'actifs éligibles pour produire un rho réel : il
est **de signe contraire** à l'attendu (-0,536) mais **non significatif**
après correction de Bonferroni (p corrigé = 1,0 sur 10 000 permutations)
— un échec de puissance, pas un rejet net. Toutes les autres cellules
sont indémontrables par manque d'actifs à n≥100 trades sur la fenêtre
considérée (le seuil strict de 100 trades/actif sur une fenêtre de 1-2
ans, fixé AVANT tout calcul, filtre sévèrement H3/H4/H5 en particulier).

## 4. Couples à surveiller en forward (aucun verdict, aucune sélection)

Lecture descriptive de la matrice de découverte (étape 2) : les couples
dont l'espérance nette contractée est positive dans **les deux fenêtres
d'espérance** (2021-2023 ET 2022-2023), avec n raisonnable, malgré un
test structurel indémontrable :

| Couple | Espérance contractée (sens1 / sens2) | n (sens1 / sens2) | n live | Date n=30 estimée |
|---|---|---|---|---|
| H1 × US100 | +0,032R / +0,082R | 133 / 67 | 3 | 2026-12-02 |
| H1 × USDJPY | +0,051R / +0,177R | 84 / 51 | 0 | non estimable (n live < 2) |
| H1 × GBPUSD | +0,036R / +0,028R | 74 / 36 | 0 | non estimable (n live < 2) |

**Mise en garde explicite** : `H5 × USDJPY` affiche une espérance brute
de +2,22R en sens1 (n=29) — presque certainement un artefact de petit
échantillon (un ou deux trades extrêmes), **pas un signal**, rapporté
ici pour transparence plutôt que silencieusement écarté, jamais inclus
dans la liste ci-dessus. Aucun des 3 couples retenus n'a de fidélité
établie (Partie 1 : 0/4 pour H1-H4) — à lire comme une observation
brute à très faible n, jamais une recommandation.

## 5. Nombre cumulé d'essais sur la fenêtre 2019-2022

**60 essais cumulés** à ce jour (55 avant ce mandat : 24 découverte du
02/09 + 27 cycles de re-tuning 1-3 des 25-26/08 + 4 candidates
walk-forward du 06/10 ; + 5 de ce mandat, un par hypothèse). Détail et
sources dans `docs/PROTOCOLE_COUPLES_08-10.md` §6, consigné AVANT tout
calcul de ce mandat.

## 6. Ce que je n'ai pas pu faire ou vérifier, et pourquoi

1. **n live lu sur un instantané daté** (`data/assistant_trading_vps_
   snapshot_fresh.db`, téléchargé en lecture seule pendant la Partie 2
   de ce mandat, ~19:41 UTC le 07/10) — pas une lecture en direct du
   VPS (hors périmètre de cette Partie 3, qui n'autorise aucun accès
   VPS). Les n live sont donc une borne basse à cette heure-là, pas à
   l'instant de ce rapport.
2. **H2 rejoué avec la grille par défaut UNIFORME sur tout 2019-2022**
   (jamais les overrides historiques par période modélisés dans les
   scripts de fidélité de la Partie 1) — conforme au protocole
   (« jamais le combo retiré »), mais signifie que ce backtest ne
   reproduit pas exactement l'historique réel des paramètres H2 avant
   le 25/09/2026.
3. **Confirmation de régime croisée (US30/US100) non modélisée pour
   H3/H4** dans ce backtest de découverte — même simplification déjà
   acceptée dans les scripts de fidélité de la Partie 1 (`confirming_
   bars` jamais branché), pas une nouvelle lacune introduite ici.
4. **`is_donchian_trailing=True` pour H5** déduit du docstring du module
   (« sortie 100% trailing, aucun tp1/tp2 ») — H5 n'avait jamais été
   intégrée à ce type de backtest de découverte auparavant (les scripts
   de fidélité de la Partie 1 ne couvraient que H1-H4), donc aucun
   précédent direct à vérifier par recoupement.
5. **Puissance insuffisante** pour conclure sur 4 des 5 hypothèses
   (0 à 2 actifs éligibles sur au moins un sens, contre 6 requis) — un
   résultat du seuil n≥100 trades/actif/fenêtre fixé AVANT tout calcul
   (protocole §3), pas un choix fait après avoir vu les chiffres.
6. **`H5 × USDJPY` (sens1, +2,22R brut, n=29)** signalé mais non
   investigué plus avant (hors périmètre : ce mandat ne rejoue, n'ajuste
   ni n'explique aucun trade individuel).
