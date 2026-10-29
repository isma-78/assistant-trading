# Bilan d'analyse des trades — 29/10/2026 (démo uniquement)

Sources : base VPS (copie lecture seule, 29/10 18:40 UTC), bougies horaires VPS, `data/analysis_29-10/out/` (fiches, synthèse). Protocoles pré-enregistrés : `docs/adequation_paires_2026-10-29.md` (commit `041264c`), `docs/corrections_hypotheses_2026-10-29.md` (`5540c0b`). Corrections de l'audit du 24/09 : **actives** (Bugs 1/2 `0442f53`, `531c704` dans le code déployé `c0b2dad`, 100 % des trades E1 avec stop garanti).

## Jeu de données
418 trades vivants (hors backtest) → **259 exploitables** (51 v1, 19 v2 pré-E1, **189 en ère E1/E2**). Écartés, jamais utilisés : 101 anomalie technique connue, 26 fermés non réconciliés (aucun R), 15 encore ouverts, 12 sorties sans prix broker, 5 CHFJPY exclue de H2-H5. Seule l'ère E1 (stop garanti, 3 jambes 50/30/20) sert aux statistiques : **−0,123 R/trade, −23,3 R, IC 95 % [−0,28 ; +0,04]** — non significatif.
Limite : MAE/MFE et ATR ne sont calculables que là où des bougies existent (66/189 et 79/189 dans `out/` ; l'actualisation des bougies échouait en 429 depuis le 11/10 — corrigée et rattrapée le 29/10 soir : MAE/MFE 189/189 dans `out_maj/`, voir `corrections_hypotheses_2026-10-29.md` §6.3).

## Causes des gains (74 « tendance conforme », 8 chance, aucun « timing »)
Le gain vient surtout d'une sortie par trailing ou d'un TP1 atteint, c.-à-d. d'un mouvement soutenu dans le sens du signal. Coût moyen médian à l'entrée très bas (0,025 R). Les gains marginaux (<0,3 R, 8 cas) sont de la chance.

## Causes des pertes (E1, 107 perdants)
(a) hypothèse non applicable à l'actif / jamais validée : **40** ; (b) actif inadapté (coût spread/stop ≥ 0,10 R ou veto de coût) : **40** (surtout BTCUSD/ETHUSD, CHFJPY) ; (e) stop trop serré : **11** (non actionnable : élargir un stop est interdit) ; (c) hors session : 5 ; (f) bruit : 4 ; non classés (bougies absentes) : 7 ; (d) exécution défaillante sur trades ouverts : 0 (le glissement d'entrée moyen est ≈ 0).
**Trades manqués (E1)** : 442 ordres jamais remplis — 208 `limit.price` (niveau déjà exécutable : un niveau de cassure est un ordre STOP, pas LIMIT), 137 autre échec, 55 péremption, 42 refus de stop (USDJPY H2, EURUSD H4 concentrés) ; rejets de risque comptés par tentative (un setup est retenté chaque minute) : cluster 14 485, coupe-circuit 1 695.

## Contrefactuels retenus (règles générales avant l'entrée)
1. Refuser si spread/stop > 0,10 R : 34 trades écartés, −0,18 R/trade (descriptif).
2. Ne pas envoyer un ordre limite déjà exécutable (208 refus certains) : zéro effet sur les fills, moins d'appels/429.
3. Périmètre d'actifs structurel (ci-dessous). Rejetés : « éviter la session US » (−0,20 vs −0,08 R, post hoc, aucune théorie) ; tout élargissement de stop.

## Matrice hypothèse × actif (adéquation structurelle ; n E1 entre parenthèses)
Retenus (**adaptés**, décision structurelle car tous « non concluants », n_test < 10) : **H1, H2, H3, H5 → GBPUSD, USDJPY** ; **H4 → US30, EURUSD** (US100 écarté : ρ=0,84 avec US30 ; GBPUSD écarté : ρ=0,64-0,79 avec EURUSD).
**Supprimés** : BTCUSD, ETHUSD (coût/ATR 0,28-0,56 sur 2019-22, veto) et CHFJPY (0,18) pour toutes ; GOLD, US100, US30, EURUSD pour H1/H2/H3/H5 (VR8 < 0,93 sur l'une des deux fenêtres : retour à la moyenne contradictoire avec une hypothèse de continuation) ; GOLD pour H4 (douteux). **Concentrer : aucun couple** (aucun couple n'a n_test ≥ 10 non négatif). **Seuls couples testables** (n_test ≥ 10 : H2/ETHUSD n=16, H2/US30 n=14) : non significativement négatifs après Holm (p = 0,52 et 0,15) ; supprimés pour raison structurelle.

## Hypothèses
H1 conservée (+0,065 R, n=28) · H2 conservée, active (−0,056, n=83), actifs recalculés · H3 conservée sous surveillance (−0,284, n=36) · H4 conservée (−0,111, n=23) · H5 conservée sous surveillance (−0,406, n=19). Aucun IC n'exclut 0. **Aucune variable de stratégie modifiée.** Règle de suspension pré-enregistrée : n ≥ 15 post-correction et borne haute de l'IC 95 % < 0.

## Limites statistiques
n minuscule par couple (médiane ≈ 5), pas de puissance pour un seul test par couple ; split 60/40 chronologique (coupure 15/10) laisse < 10 trades de test presque partout ; bootstrap par trade (dépendance intra-semaine ignorée) ; seuils structurels fixés après avoir vu les mesures 2019-22 (divulgué, confirmés sur 2024-26 : les deux fenêtres divergent pour GOLD/US100/US30/EURUSD, d'où leur retrait) ; **les actifs conservés ont un E1 défavorable (n=19, −0,48 R)** — le protocole interdit de s'y fier ; plafond de cluster 50 € du forex (GBPUSD+USDJPY partagent `fx_majors_jpy`) limitera le flux.

## Post-correction
Début enregistré dans `hypothesis_epochs` (époque `P1_29-10`) au redémarrage ; toute évaluation future porte sur les trades suivants.
