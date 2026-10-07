# Référence E2 — taux de refus de resserrement de stop pré-activation (08/10/2026)

Écrit AVANT toute activation de E2 (interrupteur `system_state.e2_enabled`
toujours à `false`/absent à cette date). Sert de base de comparaison au
critère de succès du protocole §5 (`scripts/mesure_effet_e2.py`) : un
taux de refus FINAL inférieur d'au moins 50% (relatif) à CES chiffres,
zéro élargissement, aucune hausse des 429.

## Source

`docs/BILAN_05-10.md` §2.3 (demandes `update_position_stop` et refus
finaux, mesurés depuis les fichiers de log du VPS pendant une session
avec accès VPS, depuis le redémarrage corrigé du 25/09/2026 19:35 UTC).
Déjà repris en code dans `src/simulator_fidelity.MEASURED_STOP_REQUESTS`
(A8 du bilan du 05/10) — recalculé ici via
`src.simulator_fidelity.measured_refusal_rates()`, **aucune valeur
nouvelle, aucun recalcul de fond**.

**Limite assumée (consignée AVANT tout chiffre)** : cette Partie 1 du
mandat du 08/10 n'a pas d'accès VPS, donc pas d'accès aux fichiers de
log nécessaires pour remesurer ce taux sur l'intégralité de la fenêtre
jusqu'au 07/10/2026 (seul le mécanisme de journalisation applicatif en
base, table `logs`, pourrait le permettre — il est vide sur
l'instantané local disponible, voir `docs/AUTONOMIE_08-10.md` étape 4).
Ces chiffres restent néanmoins la référence correcte pour ce protocole :
une baseline "pré-activation" doit par construction précéder E2, et
aucune activation n'a eu lieu entre le 05/10 et aujourd'hui.

## Taux par actif (n >= 10 demandes)

| Actif | Demandes | Refus finaux | Taux |
|---|---|---|---|
| BTCUSD | 48 | 22 | 45,83% |
| GOLD | 36 | 11 | 30,56% |
| EURUSD | 136 | 24 | 17,65% |
| ETHUSD | 31 | 2 | 6,45% |
| US100 | 43 | 1 | 2,33% |
| USDJPY | 18 | 0 | 0,00% |

**US30** exclu de ce tableau (n=2 < 10, prend le taux poolé — même
règle que `MIN_REQUESTS_FOR_ASSET_RATE` dans le code).

## Taux poolé (toutes hypothèses, tous actifs confondus)

**19,75%** (62 refus finaux / 314 demandes).

## Utilisation

- Critère de validation de E2 (protocole §5) : taux de refus FINAL
  après activation inférieur à **9,87%** poolé (50% relatif de 19,75%),
  sur n >= 30 tentatives de resserrement après activation, zéro
  élargissement, pas de hausse des 429.
- Par actif, à titre indicatif seulement (le critère formel du
  protocole est poolé, jamais par cellule) : objectif < 22,9% pour
  BTCUSD, < 15,3% pour GOLD, < 8,8% pour EURUSD, etc.
