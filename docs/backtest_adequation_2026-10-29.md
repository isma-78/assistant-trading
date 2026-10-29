# Backtest hypothèse × actif — résultats (29/10/2026, démo)

Protocole pré-enregistré : `docs/protocole_backtest_2026-10-29.md` (commit `574eca4`, **avant** tout calcul ; amendement n°1 écrit
après lecture de la variante A, voir ci-dessous). Code de production rejoué tel quel (`*_strategy_v2`, sorties, sizing), 2019-01-01 →
2026-10-29, train = [2019, 2024-03-20[, test = [2024-03-20, 2026-10-29]. Anti look-ahead : rejeu tronqué = rejeu complet pour les 5
hypothèses (données réelles GBPUSD, `data/backtest_29-10/lookahead.log` + test unitaire). 41 couples (CHFJPY : H1 seule).

## Décision (règles strictes du protocole §7 + amendement)
**Aucun couple n'est CONCENTRER. Un seul est SUPPRIMER sur A *et* B : H2/US100** (hors mapping actuel → sans effet).
Tout le reste est **non concluant** : **mapping inchangé** (H1/H2/H3/H5 : GBPUSD, USDJPY ; H4 : US30, EURUSD). Holm (deux familles,
≈ 40 couples) : aucun p-positif ajusté < 0,19 ; GOLD/H1 (+0,26 R, IC [+0,07 ; +0,46]) est le plus proche mais non significatif après Holm.

## Matrice (variante B = pré-contrôles de production ; R net moyen du test (n) ; ◆ = actif d'entrée actuel)
| Actif | H1 | H2 | H3 | H4 | H5 |
|---|---|---|---|---|---|
| GBPUSD | ◆-0.28 (114) | ◆-0.46 (46) | ◆-0.12 (122) | -0.10 (95) | ◆-0.38 (76) |
| USDJPY | ◆-0.15 (121) | ◆-0.12 (318) | ◆-0.21 (112) | -0.02 (117) | ◆+0.20 (93) |
| EURUSD | +0.04 (67) | -0.13 (74) | -0.18 (119) | ◆+0.13 (109) | -0.04 (76) |
| US30 | +0.04 (131) | -0.09 (449) | -0.14 (154) | ◆-0.15 (126) | -0.13 (80) |
| US100 | -0.08 (203) | -0.27 (296) **SUPPR.** | -0.12 (151) | -0.04 (136) | +0.06 (86) |
| GOLD | +0.26 (126) | +0.03 (372) | +0.13 (128) | -0.09 (101) | +0.21 (83) |
| BTCUSD | +0.02 (212) | -0.16 (329) | -0.04 (136) | -0.08 (118) | -0.07 (81) |
| ETHUSD | -0.16 (235) | -0.07 (208) | -0.11 (154) | +0.08 (136) | -0.12 (92) |
| CHFJPY | -0.31 (92) | n/d | n/d | n/d | n/d |

Variante A (stop brut, sans filtre coût) : mêmes conclusions sauf H2, où A donne SUPPRIMER sur GOLD/US30/USDJPY (R −1 à −4, artefact de
stops quasi nuls et de coûts de plusieurs R) que B n'a pas confirmés → « non concluant ». Variante B retenue pour les R ; décision = A∩B.
Seuils d'échantillon : n_test ≥ 30 partout sauf H2 EUR/GBP en A (n = 10-14, non concluant).

## Lecture
- **Aucune hypothèse n'a de couple positif robuste** (net de coûts, significatif après Holm, ≥ 3/4 fenêtres) : **proposition de suspension
  consignée pour H1, H2, H3, H4 et H5 — NON appliquée** (décision d'Ismaël ; H2 reste active par décision du 05/10). Pas de preuve de
  perte non plus : 40 couples sur 41 sont statistiquement indistinguables de 0.
- **Écarts avec la structure théorique** (19) : dans 10 cas la structure « adaptée » donne un R de test négatif — dont tous les couples
  actuels GBPUSD/USDJPY de H1-H3, GBPUSD de H5 et US30 de H4 ; dans 9 cas une structure « inadaptée » donne un R positif (GOLD H1/H3/H5, ETHUSD H4,
  EURUSD/US30/BTCUSD H1…), tous non significatifs. La prédiction structurelle n'est donc **pas confirmée** par le backtest (ni infirmée).
- **Corrélations** (R hebdomadaires, test) : paires actuelles GBPUSD/USDJPY ρ = −0,03 (H1), −0,10 (H2), +0,05 (H3), −0,06 (H5) ; seule
  paire > 0,3 : EURUSD/GBPUSD (0,39 H2, 0,37 H5), non utilisée. **Plafond forex 50 €** : appliqué aux 1 751 trades forex du test (5 hypothèses
  poolées), il en bloque 264 (15 %) — la concentration sur 2 paires forex × 4 hypothèses sature le plafond partagé.
- Remplissage (niveau atteint dans l'heure suivante) ≈ 94 % pour H2 GBPUSD/USDJPY ; le live expire à 15 min : moins de fills en réalité.

## Limites
Test **non vierge** (2024-2026 déjà analysé, fenêtre scellée consommée par ce mandat) ; une seule trajectoire de marché, régimes mal
couverts (peu de phases de vraie tendance 2024-26) ; coûts = spread historique + glissement 1 × spread sur sorties/STOP + financement
1 bp/j (pas le financement réel par actif) ; entrée valable 1 h (live : 15 min) ; distances de stop garanti = valeurs statiques ;
filtre « heures chères » et plafond de cluster temps réel non rejoués ; tests de 40 couples à très faible puissance (IC ±0,2 R).
Variante B choisie **après** avoir vu A (divulgué) ; la règle A∩B limite le risque de sélection mais ne l'annule pas.

## Application et suites
Mapping, époque et processus **inchangés** (aucune modification de comportement à déployer). **Époque P2 non enregistrée** : sans changement
de mapping elle scinderait inutilement l'échantillon P1 (la surveillance compte désormais l'époque `P<n>` la plus récente ; commande si
voulu : `python scripts/enregistrer_epoque_post_correction.py data/assistant_trading.db "" P2_29-10 "<description>"` — à saisir avec un horodatage
UTC). Code livré : `entry_mode="level_fill"` et `entry_precheck` du moteur (100 %), `src/adequation_decision.py`, scripts `backtest_adequation_*`.
**Résumé narratif (trade 17340)** : cause racine = `anthropic.BadRequestError 400 « credit balance is too low »` (crédit API Anthropic épuisé,
176 échecs journalisés sur les exécuteurs) ; pas un défaut de code, aucune correction possible côté dépôt (recharger les crédits). Partie
déterministe conservée ; le narratif n'entre dans aucune décision.
