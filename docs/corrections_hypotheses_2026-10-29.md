# Corrections des hypothèses H1-H5 et défauts d'exécution — pré-enregistrement (29/10/2026)

Écrit et commité AVANT l'implémentation. Suit `docs/adequation_paires_2026-10-29.md`
(commit `041264c`). Compte DÉMO uniquement ; verrou du réel intact ; aucun stop
n'est élargi ; plafonds de risque inchangés.

## 1. Constat de départ (ère E1/E2, trades réconciliés, n=189 ; détail dans le bilan)

R moyen par hypothèse (IC 95 % bootstrap) : H1 +0,065 (n=28), H2 −0,056 (n=83),
H3 −0,284 (n=36), H4 −0,111 (n=23), H5 −0,406 (n=19). **Aucun IC ne exclut 0** :
aucune hypothèse n'est significativement négative. Total −0,123 R/trade
(−23,3 R), IC [−0,28 ; +0,04].

## 2. Statut des hypothèses

Règle pré-enregistrée de **suspension** (appliquée à l'avenir, jamais à
l'ère qui l'a inspirée) : une hypothèse est suspendue si, sur les seuls
trades ouverts APRÈS la date de début post-correction, avec n ≥ 15
réconciliés sur ses actifs retenus, la **borne haute** de l'IC 95 % bootstrap
du R moyen est < 0. Revue à chaque palier n = 15, 30, 53.

| Hyp. | Décision | Raison |
|---|---|---|
| H1 | **Conservée** | Seule à R moyen > 0 ; aucun défaut propre. |
| H2 | **Conservée, active** (décision du 05/10) | Actifs recalculés (§3). |
| H3 | **Conservée sous surveillance** | Pire R après H5, mais IC contient 0 ; pertes dominées par coût (b) et persistance (a), traitées par le périmètre d'actifs. |
| H4 | **Conservée** | IC contient 0 ; seule hypothèse de retournement (prémisse P<1 testée structurellement). |
| H5 | **Conservée sous surveillance** | Idem H3 ; n=19 insuffisant pour suspendre (règle ci-dessus). |

**Aucune variable de stratégie n'est modifiée** (budget de 5 variables par
hypothèse intact) : aucune cause de perte ne se formule comme une règle
générale applicable avant l'entrée ET soutenue par ≥10 trades par variable sans
tomber dans le sur-ajustement. Les contrefactuels (e) « stop trop serré »
exigeraient d'élargir un stop : **interdit**, donc non actionnable.

## 3. Changement de périmètre (mapping hypothèse → actifs d'ENTRÉE)

Le périmètre d'actifs n'est pas une variable de stratégie (même statut que
l'ajout de CHFJPY le 28/08). Issu mécaniquement des règles du fichier
d'adéquation (scores, vetos sur deux fenêtres, ρ ≤ 0,60, ≤ 4 actifs,
tie-break coût/ATR), calculé par `scripts/_adequation_structurelle_29-10.py` :

H1 → GBPUSD, USDJPY · H2 → GBPUSD, USDJPY · H3 → GBPUSD, USDJPY ·
H4 → US30, EURUSD · H5 → GBPUSD, USDJPY.

**Garde-fou de sécurité** : seules les ENTRÉES sont restreintes. La gestion
des positions déjà ouvertes (stops, trailing, enveloppes, réconciliation)
continue sur l'ensemble des 9 actifs — jamais d'orphelin.

Risque assumé et divulgué : les actifs retenus ont un historique E1 pauvre
(n=19) et défavorable (−0,48 R) ; la règle interdit de décider sur ce
résultat (échantillon insuffisant). Conséquence pratique : le flux de trades
chute, l'évaluation sera lente.

## 4. Défauts d'exécution corrigés (règles générales, avant l'entrée)

1. **Filtre de coût à l'entrée** : refuser l'entrée si spread / distance du stop
   > 0,10 (coût d'une aller-retour > 0,10 R avant glissement et financement).
   Théorie : un coût > 0,10 R consomme une espérance typique de 0,1-0,2 R
   (fichier d'adéquation §1). Observé en E1 : 34 trades au-dessus du seuil,
   −0,18 R/trade (descriptif, non utilisé pour fixer le seuil).
2. **Ordre limite déjà exécutable** : 208/208 refus `limit.price` d'E1 étaient
   « exécutable immédiatement » (niveau d'entrée déjà franchi côté marché ;
   un niveau de cassure est sémantiquement un ordre STOP, pas LIMIT). Correctif
   sûr : ne pas envoyer un ordre que le broker refusera ; le setup est
   re-tenté au cycle suivant (comportement actuel inchangé sur les remplissages,
   moins d'appels, moins de pression 429).
   **Non implémenté (décision prudente, journalisée)** : passer les entrées de
   cassure en ordre STOP natif — exige un test d'écriture broker que je ne
   peux pas faire sans risque ; à valider par Ismaël sur le démo (étape
   documentée dans le bilan).
3. Aucune modification du stop garanti ni de ses bandes (min/max) : les
   `stoploss.minvalue/maxvalue` restent traités par le mécanisme de
   réessai existant (jamais d'élargissement d'une position ouverte).

## 5. Évaluation future

Début « post-correction » = horodatage de redémarrage des exécuteurs sur le code
corrigé (enregistré dans `hypothesis_epochs`, époque `P1_29-10`). Toute
évaluation future ne porte que sur les trades ouverts après cet instant.
