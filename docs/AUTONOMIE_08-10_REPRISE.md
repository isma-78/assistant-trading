# Rapport final — Reprise du 08/10/2026 : nettoyage, checklist, déploiement

Branche `couples-v2-08-10` (déjà en ancêtre linéaire de `bilan-05-10`,
`evolution-06-10`, `fidelite-07-10`, `fidelite-08-10`, `couples-08-10`
— rien à fusionner). Aucun push, aucune action réelle sur le VPS au-delà
de lectures et d'un téléchargement de snapshot. `--apply` du nettoyage
tenté une fois, refusé par le classificateur de sécurité du harnais,
jamais contourné.

## 1. Verdict en 5 lignes

Nettoyage : **à lancer par Ismaël** (commande exacte ci-dessous — 2
positions du combo H2 retiré et 2 orphelines confirmées restent
ouvertes). Fusion et tests : **faits** — les 5 branches sont déjà des
ancêtres de la branche courante (aucun conflit à résoudre), suite
complète **1592 passed**, 0 échec. Déploiement : **non effectué**,
conformément au mandat (l'étape 1 ne donne pas « nettoyage fait »).
Retour arrière : **sans objet** (rien n'a été déployé).

## 2. Nettoyage — état exact

Snapshot frais du 08/10/2026 ~18:36 UTC (téléchargement lecture seule) :

| Élément | État | Âge au moment de la lecture |
|---|---|---|
| CHFJPY, trade 14877 (combo H2 retiré) | **ouverte** | ~29 jours |
| BTCUSD, trade 15948 (combo H2 retiré) | **ouverte** | ~16 jours |
| H2 US30 (hors base) | **confirmée orpheline** (>=120 min) | ~7h25 |
| H2 GOLD (hors base) | **confirmée orpheline** (>=120 min) | ~23h30 |
| H2 US100 (hors base) | **PAS encore confirmée** (<120 min), absente de la base | 81 min 26s |
| Ordres de palier H3/H4 (catégorie a, 6 connus) | **déjà tous résolus** (0 en attente) | — |
| Positions ouvertes non réconciliées | **3** (US30, GOLD, US100 ci-dessus) | — |

**Règle des 120 minutes ajoutée** à `scripts/nettoyage_broker_08-10.py`
(`ORPHAN_MIN_AGE_MINUTES`, testée — 12 tests nouveaux) : une position
hors base n'est désormais éligible à une fermeture que passé ce délai.
H2/US100 en est l'illustration directe (même défaut qui a motivé la
règle : une jambe hors base pendant sa fenêtre de réconciliation
normale, jamais fermée ici par excès de prudence).

**Commande à lancer soi-même** (depuis
`C:\Users\ismael\Desktop\assitantrading\assistant-trading`) :
```powershell
scp assistant@163.172.189.239:~/assistant-trading/data/assistant_trading.db data\assistant_trading_vps_snapshot_fresh.db
$env:PYTHONIOENCODING="utf-8"; venv\Scripts\python.exe scripts\nettoyage_broker_08-10.py --db-path data\assistant_trading_vps_snapshot_fresh.db --apply
```
Résultat attendu : fermeture de CHFJPY/14877, BTCUSD/15948, H2/US30,
H2/GOLD ; H2/US100 fermée seulement si son âge dépasse 120 minutes au
moment du lancement (sinon re-signalée, normal — relancer le dry-run
plus tard pour la voir basculer).

## 3. Déploiement — étapes 1 à 9

| # | Étape | Résultat |
|---|---|---|
| 1 | Nettoyage vérifié | **à lancer par Ismaël** (voir §2) |
| 2 | Sauvegarde vérifiée | non effectuée (dépend de 1) |
| 3 | Arrêt/redémarrage des 6 exécuteurs | non effectué (dépend de 1-2) |
| 4 | Surveillance 60 min | non effectuée |
| 5 | Financement (capture+rattrapage) | non effectué |
| 6 | Shadow `shadow_v3cand` ON | non effectué |
| 7 | Shadow `shadow_couples` ON | non effectué (code et tests prêts depuis la Partie 4) |
| 8 | E2 ON | non effectué (patch non appliqué) |
| 9 | Crons | non effectué |

Commit actuellement déployé sur le VPS (état de repli) : **`e27b626`**
(vérifié par lecture `git log` sur le VPS). Séquence complète, commandes
exactes, retour arrière et commande d'arrêt d'urgence :
`docs/CHECKLIST_10-10_UNIQUE.md` (réécrit en entier ce mandat — existait
déjà partiellement depuis la Partie 4, avec une note honnête sur cet
état).

**Défaut A1/A2 toujours actif en production, motif d'urgence** : le
code qui crée des positions orphelines hors base (second ordre d'un
placement à paliers refusé après que le premier a été rempli, jamais
reclassé automatiquement — `_place_limit_orders`) n'a jamais été
corrigé. H2/US30, H2/GOLD et H2/US100 en sont trois occurrences
fraîches (créées entre le 06/10 et le 07/10/2026), pas seulement un
problème historique du 05/10. Un nettoyage manuel répété sans corriger
la cause laissera le même défaut produire de nouvelles orphelines.

## 4. Ce qu'Ismaël doit lancer lui-même, dans l'ordre

1. La commande `--apply` du §2 (nettoyage).
2. Vérifier le résultat (relancer le dry-run sur un nouveau snapshot).
3. Suivre `docs/CHECKLIST_10-10_UNIQUE.md` étapes 2 à 9, dans l'ordre,
   sans sauter d'étape.
4. Trancher la décision 7 (H5, voir §5) — échéance cette semaine.
5. Décider si/quand corriger la cause racine A1/A2 (hors périmètre de
   ce mandat, jamais touché à `executor.py` au-delà du patch E2 déjà
   proposé).

## 5. Décision 7 (H5) et décisions humaines restantes

**Énoncé exact** (`docs/DECISIONS.md`, 2026-08-30) : *« Règle de
collecte forward H5 : attendre l'effet des Bugs 1/2 déployés le
30/08/2026 (1-2 semaines) avant de réexaminer, plutôt qu'un changement
immédiat. Rien à appliquer maintenant. »* **Échéance indicative :
09-10/10/2026 — cette semaine.** **Non tranchée par l'agent** (comme
demandé) : la règle actuelle (aucune restriction de collecte H5) reste
inchangée.

Décisions humaines restantes : lancer le nettoyage (§2) ; trancher la
décision 7 ; suivre et valider chaque étape de
`docs/CHECKLIST_10-10_UNIQUE.md` ; décider du sort du défaut A1/A2
(corriger la cause, ou continuer à nettoyer manuellement) ; décider
d'appliquer le patch E2 (`docs/PATCH_EXECUTOR_E2_PROPOSE.diff`, jamais
appliqué).

## 6. Ce que je n'ai pas pu faire ou vérifier, et pourquoi

1. **`--apply` du nettoyage** : refusé par le classificateur de sécurité
   du harnais, jamais contourné (règle explicite du mandat).
2. **Déploiement réel (étapes 2-9)** : non tenté, car conditionné à
   l'étape 1 réussie, qui ne l'est pas.
3. **H2/US100, verdict définitif** : son âge (81 min au moment de la
   lecture) ne permet pas de trancher avec la marge de 120 minutes
   choisie ce mandat — nécessite une relecture plus tardive, pas faite
   ici (aurait consommé un nouveau cycle d'appels broker sans
   justification immédiate).
4. **H5 (compte `CAPITAL_ACCOUNT_ID_HYPOTHESIS5`)** : toujours absent de
   ce `.env` local — le nettoyage ne couvre que main/H2/H3/H4, comme en
   Partie 2.
5. **Cause racine du défaut A1/A2** : analysée et consignée (§3), mais
   non corrigée — `executor.py` reste interdit d'écriture hors le point
   d'appel E2, par construction de ce mandat.
