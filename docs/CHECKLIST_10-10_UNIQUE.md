# Checklist unique du 10/10/2026

**Note honnête sur l'état de ce fichier (08/10/2026)** : le mandat de la
Partie 2 du 08/10/2026 prévoyait que ce fichier contienne la séquence
complète de déploiement (sauvegarde, fusion, déploiement VPS,
financement, shadow, E2, crons) si l'accès VPS n'était pas disponible
depuis cette machine. **L'accès VPS ÉTAIT disponible** (SSH testé avec
succès), mais l'action d'écriture broker (`--apply` du nettoyage, étape
1 de la Partie 2) a été bloquée par le classificateur de sécurité du
harnais lui-même — jamais exécutée par l'agent. Le déploiement
(étapes 2 à 4 de la Partie 2) n'a donc jamais été entamé, et ce fichier
ne contenait **aucune section avant aujourd'hui**.

Ce document contient pour l'instant **uniquement la section ajoutée
par la Partie 4** (activation du shadow de tous les couples). Elle
suppose que la séquence de déploiement principale (runbook du 10/10,
`docs/RUNBOOK_REDEPLOIEMENT_10-10.md` s'il existe, sinon à écrire) et
l'activation du shadow `_v3cand` existant (`docs/RUNBOOK_ADDENDUM_
SHADOW_10-10.md`) sont déjà validées — **ne pas exécuter la section
ci-dessous avant cela**.

---

## Activation du shadow de TOUS les couples (`_shadow_couples`, Partie 4)

Pour qui : Ismaël, PowerShell → VPS. **À exécuter APRÈS la surveillance
de 60 minutes du déploiement principal ET après que le shadow `_v3cand`
existant (`docs/RUNBOOK_ADDENDUM_SHADOW_10-10.md`) est lui-même validé
— jamais avant, jamais en même temps.**

Ce que fait ce suivi : les 5 hypothèses `_v2` ACTUELLEMENT déployées
(H1-H5), sur les 9 actifs de la liste blanche courante (`src.asset_
whitelist.ASSET_WHITELIST`, CHFJPY comprise), reçoivent chacune un
second flux de signaux VIRTUELS (`shadow_trades`, source suffixée
`_shadow_couples`) — EN PLUS de ce qu'elles font déjà réellement. Aucun
ordre, aucun appel broker d'écriture, aucun effet sur le plafond de
cluster ni sur le capital, aucune modification des hypothèses `_v2`
elles-mêmes.

### 1. Sur le PC — vérifier la branche et pousser

```powershell
cd "C:\Users\ismael\Desktop\assitantrading\assistant-trading"
git branch --show-current
```
Attendu : la branche qui porte ce commit, fusionnée dans `main` au
moment du déploiement principal (hors périmètre de cette checklist —
voir la note en tête de fichier).

### 2. Sur le VPS — récupérer le code

```bash
ssh assistant@163.172.189.239
cd ~/assistant-trading
git pull --ff-only
```
Attendu : `Fast-forward`, liste de fichiers incluant
`src/couples_forward_report.py`, `scripts/run_shadow_couples_cycle.py`,
`scripts/rapport_couples_forward.py`, et `SHADOW_COUPLES` dans
`src/shadow_tracking.py`.

### 3. Tests

```bash
venv/bin/python -m pytest -q -p no:cacheprovider
```
Attendu : tous verts, aucun `failed`.

### 4. Premier cycle shadow_couples — test manuel (pas encore en cron)

```bash
venv/bin/python scripts/run_shadow_couples_cycle.py
```
Attendu : `Cycle shadow_couples terminé : 0 position(s) virtuelle(s)
fermée(s).` (normal au premier cycle). Peut prendre plusieurs minutes
(5 hypothèses × 9 actifs, certaines avec résolutions supplémentaires).
Si erreur de session ou 429 : relancer une fois après 30 secondes ; si
ça persiste, arrêter et ne pas aller plus loin.

### 5. Vérifier qu'AUCUN ordre n'a été passé

```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('file:data/assistant_trading.db?mode=ro',uri=True); print('trades (doit etre inchange depuis 3)', c.execute(\"select count(*) from trades\").fetchone()[0]); print('shadow_trades _shadow_couples', c.execute(\"select source,count(*) from shadow_trades where source like '%_shadow_couples' group by 1\").fetchall())"
```
Attendu : le compte de `trades` est inchangé depuis l'étape 3 (le
shadow n'y touche jamais) ; `shadow_trades` montre 0 à quelques lignes
par source `_shadow_couples`.

### 6. Installer le cron du cycle shadow_couples (lecture seule côté broker)

```bash
(crontab -l; echo '*/15 * * * * cd /home/assistant/assistant-trading && venv/bin/python scripts/run_shadow_couples_cycle.py >> logs/shadow_couples_cycle_cron.log 2>&1') | crontab -
```

### 7. Installer le cron du rapport hebdomadaire des couples (lecture seule, aucun appel broker)

```bash
(crontab -l; echo '0 6 * * 1 cd /home/assistant/assistant-trading && venv/bin/python scripts/rapport_couples_forward.py >> logs/rapport_couples_forward_cron.log 2>&1') | crontab -
```
```bash
venv/bin/python scripts/rapport_couples_forward.py
```
Attendu : `Rapport écrit : .../docs/SUIVI_COUPLES/AAAA-MM-JJ.md (45
couples).`

### 8. Vérifications (à refaire 1h, puis 1 jour après l'étape 6)

```bash
tail -n 30 logs/shadow_couples_cycle_cron.log
```
Attendu : des lignes `Cycle shadow_couples terminé : N...`, sans
`Traceback`.

```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('file:data/assistant_trading.db?mode=ro',uri=True); print(c.execute(\"select count(*) from trades where source like '%_shadow_couples%'\").fetchone()[0])"
```
Attendu : `0` — **aucune source `_shadow_couples` ne doit JAMAIS
apparaître dans `trades`** (table réelle). Si ce n'est pas `0` : arrêt
immédiat (voir retour arrière ci-dessous), anomalie grave.

## Retour arrière (si une étape échoue, ou pour arrêter le shadow_couples)

```bash
crontab -l | grep -v -E "shadow_couples_cycle|rapport_couples_forward" | crontab -
```
Attendu : les 2 lignes `shadow_couples` disparaissent de `crontab -l`,
les autres (y compris le shadow `_v3cand` existant) restent. Les tables
`shadow_trades`/`shadow_partials`/`shadow_epochs` peuvent être laissées
telles quelles (lecture seule, sans effet sur rien d'autre) ou vidées
pour les seules sources `_shadow_couples` si souhaité :

```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/assistant_trading.db'); c.execute(\"delete from shadow_partials where shadow_trade_id in (select id from shadow_trades where source like '%_shadow_couples')\"); c.execute(\"delete from shadow_trades where source like '%_shadow_couples'\"); c.execute(\"delete from shadow_epochs where source like '%_shadow_couples'\"); c.commit()"
```

Aucun impact sur les hypothèses `_v2`, le capital, le plafond de
cluster ou le shadow `_v3cand` existant dans tous les cas.
