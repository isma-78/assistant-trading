# Checklist unique — déploiement autonome démo (reprise du 08/10/2026)

**Note sur l'état de ce fichier.** Ce document EXISTAIT DÉJÀ (créé
pendant la Partie 4 du 08/10/2026, mandat « couples v2 »), mais ne
contenait que la section d'activation du shadow de tous les couples
(étape 7 ci-dessous), avec une note disant que la séquence de
déploiement principale n'avait jamais été écrite. **Ce mandat la
réécrit en entier** : les 9 étapes demandées, dans l'ordre, la section
shadow_couples déjà écrite reprise et renumérotée à sa place (étape 7).

Pour qui : Ismaël, PowerShell (PC) → SSH (VPS). Chaque étape donne la
commande exacte et le résultat attendu. **Ne pas sauter d'étape, ne pas
paralléliser.** Toujours vérifier PAR CODE que l'environnement est
démo avant tout appel broker ou déploiement (`CAPITAL_ENVIRONMENT` dans
`.env`, jamais supposé).

État de repli connu : commit actuellement déployé sur le VPS =
**`e27b626`** (vérifié par l'agent le 08/10/2026 via `git log` en
lecture seule sur le VPS).

---

## Étape 1 — Nettoyage vérifié

**État au 08/10/2026 ~18:36 UTC (lecture seule, snapshot frais de la
base VPS)** :
- CHFJPY (trade 14877) et BTCUSD (trade 15948, combo H2 retiré) :
  **toujours ouvertes**, encore à fermer.
- H2/US30 (position hors base, créée 2026-10-07T11:11:09, âgée
  d'environ 7h25) et H2/GOLD (hors base, créée 2026-10-06T19:05:42,
  âgée d'environ 23h30) : **confirmées orphelines** (âge >= 120 min,
  règle ajoutée ce mandat), prêtes à fermer.
- H2/US100 (hors base, créée 2026-10-07T17:15:04, âgée d'environ 81
  minutes) : **PAS encore confirmée orpheline** (< 120 min) — NE PAS
  fermer maintenant ; une position sœur réconciliée (trade 16906, même
  entrée/stop, créée 12 minutes plus tard) existe déjà, ce qui suggère
  une jambe orpheline d'un essai de placement antérieur, mais la marge
  de prudence de 120 minutes n'est pas encore écoulée au moment de
  cette lecture — à revérifier avant toute action (relancer le dry-run,
  qui la reclassera automatiquement une fois l'âge atteint).
- Ordres de palier H3/H4 (catégorie a, 6 connus) : **déjà tous résolus**
  (0 ordre en attente chez H3 et H4) — rien à annuler.
- Aucune autre position hors base détectée sur les comptes accessibles
  (main, H2, H3, H4 — H5 non vérifié, identifiants de compte absents de
  ce `.env` local).

**Verdict : « à lancer par Ismaël ».** L'agent a tenté `--apply` une
fois (comme demandé) ; **refusé par le classificateur de sécurité du
harnais**, sans contournement tenté. Commande exacte à lancer, depuis
`C:\Users\ismael\Desktop\assitantrading\assistant-trading` :

```powershell
$env:PYTHONIOENCODING="utf-8"; venv\Scripts\python.exe scripts\nettoyage_broker_08-10.py --db-path data\assistant_trading_vps_snapshot_fresh.db --apply
```

**Avant de lancer cette commande**, retélécharger un snapshot frais
(le précédent date du 08/10/2026 ~18:36 UTC) :

```powershell
scp assistant@163.172.189.239:~/assistant-trading/data/assistant_trading.db data\assistant_trading_vps_snapshot_fresh.db
```

**Résultat attendu** : fermeture de CHFJPY (14877), BTCUSD (15948),
H2/US30, H2/GOLD (si toujours orphelines au moment du lancement) ;
H2/US100 fermée SEULEMENT si son âge a dépassé 120 minutes à ce
moment-là (sinon re-signalée « trop récente », normal). `Appels HTTP
consommés : N/40` affiché en fin de script — si `N` approche 40, ne pas
relancer la même session, attendre le prochain cycle.

**Vérification après exécution** (relancer le dry-run, sans `--apply`,
sur un nouveau snapshot) :
```powershell
scp assistant@163.172.189.239:~/assistant-trading/data/assistant_trading.db data\assistant_trading_vps_snapshot_fresh2.db
$env:PYTHONIOENCODING="utf-8"; venv\Scripts\python.exe scripts\nettoyage_broker_08-10.py --db-path data\assistant_trading_vps_snapshot_fresh2.db
```
Attendu : « Étape 1 à fermer : [] », « Étape 4 à fermer : [] » (ou
seulement H2/US100 si elle n'avait pas encore 120 minutes), catégorie
(c) absente des positions ouvertes.

---

## Étape 2 — Sauvegarde de la base, vérifiée

Sur le VPS :
```bash
ssh assistant@163.172.189.239
cd ~/assistant-trading
venv/bin/python scripts/backup_db.py
```
Attendu : un nouveau fichier dans `data/backups/`, horodaté.

Vérification (taille non nulle, intégrité SQLite, copie lisible) :
```bash
LATEST=$(ls -t data/backups/*.db | head -1)
ls -la "$LATEST"
sqlite3 "$LATEST" "PRAGMA integrity_check;"
sqlite3 "$LATEST" "SELECT COUNT(*) FROM trades;"
```
Attendu : taille > 0 ; `integrity_check` = `ok` ; un compte de lignes
cohérent avec `data/assistant_trading.db` lui-même (pas 0, pas une
erreur).

---

## Étape 3 — Arrêt puis redémarrage de TOUS les exécuteurs (H2 inclus)

**Préalable obligatoire** : aucune position ouverte non réconciliée ni
ordre en attente non suivi (vérifié par l'étape 1 déjà effectuée avec
succès — relancer le dry-run de nettoyage une dernière fois juste
avant cette étape pour confirmer).

```bash
cd ~/assistant-trading
scripts/restart_process.sh all
```
Attendu : 6 lignes `<session> OK pid=<pid> démarré=<horodatage>` (
`executor_loop`, `trend_executor`, `hypothesis2_executor`,
`hypothesis3_executor`, `hypothesis4_executor`,
`hypothesis5_executor`). Si une ligne dit `ECHEC du redémarrage` :
**STOP, ne pas continuer**, investiguer avant toute autre étape.

Vérifier que E2 et les deux shadows sont bien à OFF (comportement par
défaut, aucune action requise sauf si déjà modifié avant) :
```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/assistant_trading.db'); print(c.execute(\"select value from system_state where key='e2_enabled'\").fetchall())"
crontab -l | grep -E "shadow_cycle|shadow_couples_cycle"
```
Attendu : `[]` ou `[('false',)]` pour `e2_enabled` (jamais `'true'`
avant l'étape 8) ; aucune ligne cron shadow encore installée.

---

## Étape 4 — Surveillance 60 minutes

Critères de retour arrière automatique (un seul suffit, voir
`docs/PROTOCOLE_AUTONOME_08-10.md` §6) : exécuteur absent ou en boucle
de crash ; exception non gérée hors fail-safe déjà en place ;
coupe-circuit déclenché par une erreur de CODE (pas une perte réelle
légitime) ; stop élargi ; ordre émis par un shadow (devrait être
structurellement impossible) ; plafonds de cluster incohérents avec les
positions réellement ouvertes ; journaux non horodatés ; réconciliation
en échec ; position ouverte non réconciliée apparue après le
redémarrage.

Vérifications, à répéter à T+15, T+30, T+60 minutes :
```bash
venv/bin/python scripts/process_watchdog.py
tail -n 50 logs/executor_loop.log logs/trend_executor.log logs/hypothesis2_executor.log logs/hypothesis3_executor.log logs/hypothesis4_executor.log logs/hypothesis5_executor.log
```
Attendu : les 6 process « up », aucun `Traceback`, aucune ligne de
stop élargi.

**Retour arrière si un critère se déclenche** :
```bash
cd ~/assistant-trading
git checkout e27b626
scripts/restart_process.sh all
venv/bin/python scripts/process_watchdog.py
```
Puis envoyer l'alerte Telegram manuellement (texte libre décrivant le
retour arrière) et **ARRÊTER là** — aucune nouvelle tentative autonome
dans la même session.

---

## Étape 5 — Financement : capture + rattrapage idempotent depuis le 30/08

```bash
venv/bin/python scripts/backfill_financing.py --from 2026-08-30
venv/bin/python scripts/capture_financing.py
crontab -l | grep capture_financing
```
Si le cron `capture_financing.py` n'est pas déjà installé :
```bash
(crontab -l; echo '0 4 * * * cd /home/assistant/assistant-trading && venv/bin/python scripts/capture_financing.py >> logs/capture_financing_cron.log 2>&1') | crontab -
```
Attendu : `backfill_financing.py` rapporte le nombre de transactions
rattrapées (0 si déjà à jour, jamais une erreur) ; `financing_
transactions` contient des lignes récentes (`SELECT MAX(date_utc) FROM
financing_transactions;` proche d'aujourd'hui).

---

## Étape 6 — Shadow des candidates `shadow_v3cand` ON

**Seulement après validation de l'étape 4.** Suivre
`docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md` en entier (déjà écrit, non
répété ici) : premier cycle manuel (`scripts/run_shadow_cycle.py`),
preuve qu'aucun ordre n'est émis (`trades` inchangée), puis crons
(`run_shadow_cycle.py`, `shadow_milestone_alerts.py`).

---

## Étape 7 — Shadow des couples `shadow_couples` ON

**Seulement après 30 minutes de l'étape 6 sans violation** (aucun ordre
émis par `shadow_v3cand`, aucune erreur dans `logs/shadow_cycle_cron.log`).

```bash
cd ~/assistant-trading
git pull --ff-only   # si pas déjà fait — inclut src/shadow_tracking.SHADOW_COUPLES
venv/bin/python -m pytest -q -p no:cacheprovider
venv/bin/python scripts/run_shadow_couples_cycle.py
```
Attendu : tests verts ; `Cycle shadow_couples terminé : 0 position(s)
virtuelle(s) fermée(s).` (normal au premier cycle, peut prendre
plusieurs minutes : 5 hypothèses x 9 actifs).

Vérifier qu'aucun ordre n'a été passé :
```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('file:data/assistant_trading.db?mode=ro',uri=True); print('trades (inchange depuis avant ce cycle)', c.execute(\"select count(*) from trades\").fetchone()[0]); print('shadow_trades _shadow_couples', c.execute(\"select source,count(*) from shadow_trades where source like '%_shadow_couples' group by 1\").fetchall())"
```

Installer les crons :
```bash
(crontab -l; echo '*/15 * * * * cd /home/assistant/assistant-trading && venv/bin/python scripts/run_shadow_couples_cycle.py >> logs/shadow_couples_cycle_cron.log 2>&1') | crontab -
(crontab -l; echo '0 6 * * 1 cd /home/assistant/assistant-trading && venv/bin/python scripts/rapport_couples_forward.py >> logs/rapport_couples_forward_cron.log 2>&1') | crontab -
```

Vérification 1h et 1 jour après :
```bash
tail -n 30 logs/shadow_couples_cycle_cron.log
venv/bin/python -c "import sqlite3; c=sqlite3.connect('file:data/assistant_trading.db?mode=ro',uri=True); print(c.execute(\"select count(*) from trades where source like '%_shadow_couples%'\").fetchone()[0])"
```
Attendu : pas de `Traceback` ; le second comte doit être **`0`** (aucune
source `_shadow_couples` ne doit JAMAIS apparaître dans `trades`,
sinon anomalie grave — voir retour arrière).

---

## Étape 8 — E2 ON (seulement si toutes les conditions ci-dessus sont validées)

**Préalable** : le diff `docs/PATCH_EXECUTOR_E2_PROPOSE.diff` doit être
APPLIQUÉ à `src/executor.py` (jamais fait par l'agent — seul point
d'appel autorisé du resserrement de stop) et un test prouvant
l'impossibilité d'élargir doit passer (`tests/test_stop_tightening_
retry.py::test_e2_on_stop_widening_blocked_even_if_risk_engine_wrongly_
approves`, déjà présent et vert).

```bash
cd ~/assistant-trading
git apply --check docs/PATCH_EXECUTOR_E2_PROPOSE.diff   # doit réussir sans erreur
git apply docs/PATCH_EXECUTOR_E2_PROPOSE.diff
venv/bin/python -m pytest -q -p no:cacheprovider
```
Si la suite n'est pas 100% verte après le patch : **STOP, annuler le
patch** (`git checkout -- src/executor.py`), E2 reste OFF.

Si verte, redémarrer les exécuteurs pour charger le nouveau code, PUIS
activer :
```bash
scripts/restart_process.sh all
venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/assistant_trading.db'); c.execute(\"INSERT INTO system_state (key, value, updated_at) VALUES ('e2_enabled', 'true', datetime('now')) ON CONFLICT(key) DO UPDATE SET value='true', updated_at=datetime('now')\"); c.commit()"
```

Noter l'horodatage exact d'activation (T0 d'E2). Installer la
surveillance planifiée :
```bash
(crontab -l; echo '*/10 * * * * cd /home/assistant/assistant-trading && venv/bin/python scripts/mesure_effet_e2.py --db-path data/assistant_trading.db --since "<T0 ISO exact>" --apply-stop >> logs/mesure_effet_e2_cron.log 2>&1') | crontab -
```
Remplacer `<T0 ISO exact>` par l'horodatage noté ci-dessus — **champ à
remplir, aucune valeur inventée**.

**Arrêt automatique** (un seul motif suffit, déjà implémenté dans
`scripts/mesure_effet_e2.py` avec `--apply-stop`) : tout élargissement
détecté ; >= 3 erreurs inattendues du module en 10 minutes ; hausse du
taux de 429 des exécuteurs vs référence pré-activation (19,75%, voir
`docs/REFERENCE_E2_08-10.md`).

---

## Étape 9 — Crons, dans cet ordre exact

1. Rapport hebdomadaire shadow (`rapport_hebdo_shadow.py`) — déjà
   installé à l'étape 6 (`docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md`).
2. Rapport hebdomadaire des couples (`rapport_couples_forward.py`) —
   déjà installé à l'étape 7 ci-dessus.
3. Alertes de jalons 30/40/53 (`shadow_milestone_alerts.py`) — déjà
   installé à l'étape 6.
4. Mesure d'effet E2 (`mesure_effet_e2.py --apply-stop`) — déjà
   installé à l'étape 8.
5. Rafraîchissement hebdomadaire de l'historique :
```bash
(crontab -l; echo '0 5 * * 0 cd /home/assistant/assistant-trading && venv/bin/python scripts/rafraichir_historique.py >> logs/rafraichir_historique_cron.log 2>&1') | crontab -
```
   (dimanche 05:00 UTC, hors heures actives — demande explicite du
   protocole, seulement si un premier rafraîchissement manuel a déjà
   réussi : c'est le cas, voir `docs/AUTONOMIE_08-10_PARTIE1.md` étape 2).
6. Mesure de fidélité mensuelle :
```bash
(crontab -l; echo '0 6 1 * * cd /home/assistant/assistant-trading && venv/bin/python scripts/mesure_fidelite_continue.py >> logs/mesure_fidelite_continue_cron.log 2>&1') | crontab -
```

Vérification finale :
```bash
crontab -l
```
Attendu : toutes les lignes ci-dessus présentes, dans n'importe quel
ordre d'affichage (`crontab -l` ne garantit pas l'ordre d'installation),
mais **aucune absente**.

---

## Retour arrière général (à n'importe quelle étape)

```bash
cd ~/assistant-trading
git checkout e27b626
scripts/restart_process.sh all
venv/bin/python scripts/process_watchdog.py
```
Puis alerte Telegram manuelle, et **STOP** — aucune nouvelle tentative
autonome dans la même session.

## Commande d'arrêt d'urgence (E2 et LES DEUX shadows à OFF, sans redéploiement)

```bash
cd ~/assistant-trading && crontab -l | grep -v -E "shadow_cycle|shadow_couples_cycle|shadow_milestone|rapport_hebdo_shadow|rapport_couples_forward|mesure_fidelite_continue|mesure_effet_e2" | crontab - && venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/assistant_trading.db'); c.execute(\"INSERT INTO system_state (key, value, updated_at) VALUES ('e2_enabled', 'false', datetime('now')) ON CONFLICT(key) DO UPDATE SET value='false', updated_at=datetime('now')\"); c.commit()"
```
Effet : tous les crons de suivi/E2/shadow retirés immédiatement (y
compris `shadow_couples`, ajouté par ce mandat, absent du verrou
d'origine `docs/PROTOCOLE_AUTONOME_08-10.md` §7 — **étendu ici**) ;
`e2_enabled='false'` lu au cycle suivant de chaque exécuteur. Les 6
exécuteurs et les shadows eux-mêmes continuent de tourner (ce verrou ne
les arrête pas, seulement E2 et la surveillance automatisée).
