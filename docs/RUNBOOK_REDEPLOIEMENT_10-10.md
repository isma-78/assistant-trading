# Runbook — redéploiement du samedi 10/10/2026 (supervisé par Ismaël)

Pour qui : Ismaël, depuis son PC Windows (PowerShell), vers le VPS Linux.
Durée prévue : 45 à 60 minutes. Marchés FX/indices fermés le samedi, crypto
ouverte. Chaque étape : **une commande à copier-coller**, puis **le
résultat attendu**. Si le résultat diffère : **arrête-toi**, ne passe pas à
l'étape suivante, et va à la section « Retour arrière » si besoin.

Ce qui est déployé : la branche `bilan-05-10` (correctifs A2, A3, A4, A6,
A7, A8, A9, A10, alertes de jalons, scripts A1/A5, étiquetage des
cellules). **Aucune liste d'actifs v3 n'est activée** : la procédure de
sélection par actif n'a été validée pour aucune hypothèse
(docs/APPLICATION_05-10.md §4). Aucune époque ne change. Les 5 hypothèses,
**H2 comprise**, redémarrent et continuent à trader en démo.

Rappel : jamais `CAPITAL_ENVIRONMENT=live`. Le `.env` ne bouge pas.

---

## Partie A — sur le PC (PowerShell)

### A1. Ouvrir PowerShell dans le dossier du projet
```powershell
cd "C:\Users\ismael\Desktop\assitantrading\assistant-trading"
```
Attendu : l'invite se termine par `assistant-trading>`.

### A2. Vérifier qu'on est sur la bonne branche
```powershell
git branch --show-current
```
Attendu : `bilan-05-10`

### A3. Lancer tous les tests
```powershell
venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```
Attendu (après 3-4 minutes), dernière ligne : `1303 passed` (ou plus),
**aucun** `failed`. Si un test échoue : arrête-toi.

### A4. Intégrer la branche dans `main`
```powershell
git checkout main
```
Attendu : `Switched to branch 'main'`
```powershell
git merge --ff-only bilan-05-10
```
Attendu : `Fast-forward` suivi d'une liste de fichiers. Si tu lis
`Not possible to fast-forward` : arrête-toi.

### A5. Noter le numéro de version
```powershell
git log --oneline -1
```
Attendu : une ligne qui commence par 7 caractères (le « hash »). **Note-le.**
La version actuellement en production est `e27b626` (retour arrière).

### A6. Envoyer sur GitHub
```powershell
git push origin main
```
Attendu : `main -> main`, sans `rejected`.

---

## Partie B — sur le VPS

### B1. Se connecter
```powershell
ssh assistant@163.172.189.239
```
Attendu : invite `assistant@scw-musing-kalam:~$`. Toutes les commandes
suivantes se tapent dans cette fenêtre.

### B2. Aller dans le projet
```bash
cd ~/assistant-trading
```
Attendu : aucune sortie.

### B3. Sauvegarde de la base (AVANT TOUT)
```bash
venv/bin/python scripts/backup_db.py
```
Attendu : `Sauvegarde créée : /home/assistant/assistant-trading/data/backups/assistant_trading_2026101...Z.db`.
**Note le nom exact du fichier** (retour arrière).

### B4. État avant : aucune position non réconciliée, aucun ordre en attente
```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('file:data/assistant_trading.db?mode=ro',uri=True); print('en_attente', c.execute(\"select count(*) from trades where statut='en_attente'\").fetchone()[0]); print('paliers_en_attente', c.execute(\"select count(*) from trade_legs where statut='en_attente'\").fetchone()[0]); print('fantomes_depuis_E1', c.execute(\"select count(*) from trades where statut='ferme_non_reconcilie' and ouvert_at>='2026-09-25T19:12'\").fetchone()[0])"
```
Attendu : `en_attente 0`, `paliers_en_attente 0`, `fantomes_depuis_E1 0`.
Si `en_attente` ou `paliers_en_attente` > 0 : attends 15 minutes (un ordre
est en cours de vie, la péremption le règle) et recommence. Si
`fantomes_depuis_E1` > 0 : note-le et continue (c'est un constat, pas un
blocage).

### B5. Récupérer le nouveau code
```bash
git pull --ff-only
```
Attendu : `Fast-forward` puis la liste des fichiers. Les process en cours ne
sont pas affectés tant qu'ils ne sont pas redémarrés.
```bash
git log --oneline -1
```
Attendu : le même hash qu'en A5.

### B6. Tests sur le VPS
```bash
venv/bin/python -m pytest -q -p no:cacheprovider
```
Attendu : `1303 passed` (ou plus), aucun `failed`. Sinon : retour arrière.

### B7. Vérification broker en lecture seule (A1)
```bash
venv/bin/python scripts/verify_broker_readonly.py
```
Attendu : pour H2, H3, H4, la liste des positions ouvertes chez le broker,
puis `Appels HTTP effectués : 9/10`.
- Une ligne qui commence par `CRITIQUE` = position **hors base** ou **sans
  stop**. Note-la (actif, taille, âge, risque en €). Continue le
  redéploiement : A2 empêche que ça se reproduise. Pour la position déjà
  hors base, la décision de la fermer à la main sur la plateforme démo
  Capital.com t'appartient.
- `429 reçu ... ARRÊT` : attends 5 minutes et relance la commande.

### B8. Clôture des positions du combo H2 retiré (A5) — d'abord une simulation
```bash
venv/bin/python scripts/close_retired_combo_positions.py
```
Attendu : 4 lignes `[simulation] trade ... serait fermée`, ou `ABSENTE chez
le broker`, ou `non éligible`.

Ensuite, si tu décides de fermer :
```bash
venv/bin/python scripts/close_retired_combo_positions.py --apply
```
Attendu, pour chaque trade : `fermée chez le broker` puis `réconcilié en
base (statut=ferme, R=...)` dans les 5 minutes.
**Samedi : BTCUSD (15948) et ETHUSD (15954) seulement peuvent fermer**
(crypto ouverte). CHFJPY (14877) et GBPUSD (16329) renverront une erreur de
marché fermé : relance la même commande `--apply` **lundi 12/10 après 08:00
heure de Paris**. Le script saute automatiquement les trades déjà fermés.

### B9. Correction de l'étiquette exit_type de H5 (A6) — simulation puis écriture
```bash
venv/bin/python scripts/fix_exit_type_labels.py
```
Attendu : `N trade(s) H5 à corriger (simulation...)` avec N ≥ 7.
```bash
venv/bin/python scripts/fix_exit_type_labels.py --apply
```
Attendu : `N trade(s) H5 corrigé(s)`. Relancer donne `0` (idempotent).

### B10. Redémarrer les 6 exécuteurs, un par un (H2 compris)
```bash
scripts/restart_process.sh all
```
Attendu : 6 lignes `executor_loop OK pid=...`, `trend_executor OK ...`,
`hypothesis2_executor OK ...`, `hypothesis3_executor OK ...`,
`hypothesis4_executor OK ...`, `hypothesis5_executor OK ...`. Le script
s'arrête à la première ligne `ECHEC` : dans ce cas, va au retour arrière.

### B11. Rattrapage du financement (A4)
```bash
venv/bin/python scripts/capture_financing.py
```
Attendu : `Capture financement : N nouvelle(s) transaction(s) SWAP persistée(s).`
(et non plus `ModuleNotFoundError`).
```bash
venv/bin/python scripts/backfill_financing.py --from 2026-08-30
```
Attendu : une ligne par jour, puis `Terminé : N transaction(s) SWAP
ajoutée(s).` Si tu lis `Refus de l'API ... ARRÊT` : le broker n'accepte pas
les dates passées sur cet endpoint, le rattrapage est impossible. Note-le,
ce n'est pas bloquant. `429` : relance plus tard, c'est sans doublon.

### B12. Installer l'alerte de jalons (décision 7)
```bash
(crontab -l; echo '*/15 * * * * cd /home/assistant/assistant-trading && venv/bin/python scripts/milestone_alerts.py >> logs/milestone_alerts_cron.log 2>&1') | crontab -
```
```bash
crontab -l
```
Attendu : 5 lignes (sauvegarde 03:00, watchdog */5, financement 04:00,
cycle 05:00, jalons */15).
```bash
venv/bin/python scripts/milestone_alerts.py
```
Attendu : aucune sortie (aucune hypothèse n'est encore à 30 trades
réconciliés). Aucun message Telegram.

---

## Partie C — vérifications après redémarrage (10 minutes après B10)

### C1. Journaux horodatés (A7)
```bash
tail -n 3 logs/hypothesis2_executor.log
```
Attendu : des lignes qui commencent par une date, par exemple
`2026-10-10T14:05:12.345Z INFO:src...`.

### C2. Les 8 process sont vus vivants par le watchdog
```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('file:data/assistant_trading.db?mode=ro',uri=True); [print(r) for r in c.execute(\"select key,value,updated_at from system_state where key like 'watchdog:status:%'\")]"
```
Attendu : 8 lignes avec `up`, `updated_at` de moins de 5 minutes. Une seule
ligne `down` : relance `scripts/restart_process.sh <nom>` pour ce process.

### C3. Aucun coupe-circuit déclenché
```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('file:data/assistant_trading.db?mode=ro',uri=True); print(c.execute(\"select count(*) from circuit_breaker_events where cleared_at is null\").fetchone()[0]); [print(r) for r in c.execute(\"select key,value from system_state where key like 'api_error_streak:%'\")]"
```
Attendu : `0`, puis 6 lignes avec la valeur `0` (ou 1-2 passagèrement).

### C4. Plafonds inchangés
```bash
grep -n "^CLUSTER_EXPOSURE_CAP_EUR\|^EXPOSURE_CAP_FRACTION" src/circuit_breaker.py; git diff e27b626 --stat -- src/risk_engine.py src/validator.py src/circuit_breaker.py .env
```
Attendu : `CLUSTER_EXPOSURE_CAP_EUR = 50.0`, la ligne `EXPOSURE_CAP_FRACTION`,
et **aucune** ligne de différence pour risk_engine/validator/circuit_breaker.

### C5. Compteurs de verdict (A9 : CHFJPY exclue de H2-H5)
```bash
venv/bin/python scripts/epoch_status.py
```
Attendu : 5 lignes, époques `E1` (H1-H4) et `E2` (H5), inchangées, avec
`réconciliés=…`, `CHFJPY_exclus=…`.

### C6. Les exécuteurs tournent en `python -u`
```bash
ps -eo etime,cmd | grep "python -u -m src" | grep -v grep
```
Attendu : 6 lignes `venv/bin/python -u -m src....`, démarrées il y a
quelques minutes.

---

## Activation des listes d'actifs v3

**Rien à faire ce samedi.** La procédure n'est validée pour aucune
hypothèse : aucune liste v3, aucune nouvelle époque, compteurs inchangés.
Le fichier `src/asset_cell_labels.py` (étiquettes) n'est lu par aucun
exécuteur.

---

## Retour arrière (si une étape B6, B10 ou C échoue)

1. Revenir à l'ancienne version du code :
   ```bash
   cd ~/assistant-trading && git reset --hard e27b626
   ```
   Attendu : `HEAD is now at e27b626 ...`
2. Redémarrer les 6 exécuteurs sur l'ancienne version :
   ```bash
   scripts/restart_process.sh all
   ```
   Attendu : 6 lignes `OK`. (L'ancien watchdog cherche `python -m`, il
   signalera ces process `down` tant qu'ils tournent en `python -u` : pour
   l'éviter, relance-les à la main dans chaque session tmux avec la
   commande de démarrage d'origine `venv/bin/python -m src.<module> 2>&1 |
   tee -a logs/<session>.log`.)
3. Retirer l'alerte de jalons du cron :
   ```bash
   crontab -l | grep -v milestone_alerts | crontab -
   ```
4. La base : **ne la restaure pas** sauf problème de données avéré. La
   seule écriture de ce redéploiement est l'étiquette H5 (B9), sans effet
   sur les R et réversible :
   ```bash
   venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/assistant_trading.db'); c.execute(\"update trades set exit_type='tp_partiel' where source='hypothesis5_v2' and exit_type='trailing_pur'\"); c.commit()"
   ```
   Restauration complète (dernier recours, exécuteurs ARRÊTÉS d'abord par
   Ctrl-C dans chaque session tmux) : `cp data/backups/<fichier noté en B3>
   data/assistant_trading.db`.
5. Sur le PC, remettre `main` comme avant : `git checkout main; git reset
   --hard e27b626; git push --force-with-lease origin main` (uniquement si
   tu veux annuler aussi sur GitHub).
