# Addendum au runbook du 10/10/2026 — activation du suivi shadow des 4 candidates V2

Pour qui : Ismaël, PowerShell → VPS. **À exécuter APRÈS que toutes les
étapes du runbook principal (`docs/RUNBOOK_REDEPLOIEMENT_10-10.md`) sont
validées et que les 6 exécuteurs tournent normalement — jamais avant,
jamais en même temps.** Durée : 15-20 minutes.

Ce que fait ce suivi : les 4 candidates (H1 reprise ADX, H2 transition de
confluence, H3 filtre d'expansion de volatilité, H4 filtre ADX)
reçoivent des signaux VIRTUELS — aucun ordre, aucun appel broker
d'écriture, aucun effet sur le plafond de cluster ni sur le capital. Les
hypothèses `_v2` actuelles (H1-H5) continuent à trader exactement comme
avant, sans aucune modification.

Rappel : jamais `CAPITAL_ENVIRONMENT=live`.

---

## Partie A — sur le PC (si pas déjà fait aujourd'hui)

### A1. Vérifier qu'on est sur la version fusionnée
```powershell
cd "C:\Users\ismael\Desktop\assitantrading\assistant-trading"
git branch --show-current
```
Attendu : `main` (fusionné depuis `evolution-06-10`, comme pour
`bilan-05-10` au runbook principal).

Si `evolution-06-10` n'est pas encore fusionnée :
```powershell
git checkout main
git merge --ff-only evolution-06-10
git push origin main
```
Attendu : `Fast-forward`, puis `main -> main` sans `rejected`.

---

## Partie B — sur le VPS

### B1. Se connecter et récupérer le code
```bash
ssh assistant@163.172.189.239
cd ~/assistant-trading
git pull --ff-only
```
Attendu : `Fast-forward`, liste de fichiers incluant
`src/shadow_tracking.py`, `src/shadow_milestone.py`,
`scripts/run_shadow_cycle.py`, `scripts/shadow_milestone_alerts.py`.

### B2. Tests
```bash
venv/bin/python -m pytest -q -p no:cacheprovider
```
Attendu : `1458 passed` (ou plus), aucun `failed`.

### B3. Vérifier que les 6 exécuteurs principaux tournent déjà normalement
```bash
venv/bin/python scripts/epoch_status.py
```
Attendu : 5 lignes, comme avant (le shadow ne les modifie pas). Si ce
n'est pas déjà vérifié par le runbook principal, **arrête-toi ici** et
reviens-y d'abord.

### B4. Premier cycle shadow — test manuel (pas encore en cron)
```bash
venv/bin/python scripts/run_shadow_cycle.py
```
Attendu : `Cycle shadow terminé : 0 position(s) virtuelle(s) fermée(s).`
(aucune position ouverte encore, c'est normal au premier cycle). Peut
prendre 1-2 minutes (lecture de bougies pour 4 candidates × 8-9 actifs).
Si tu lis une erreur de session ou un 429 : relance la commande une fois
après 30 secondes ; si ça persiste, arrête-toi et ne vas pas plus loin.

### B5. Vérifier qu'AUCUN ordre n'a été passé
```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('file:data/assistant_trading.db?mode=ro',uri=True); print('trades v2 (doit etre inchange depuis B3)', c.execute(\"select count(*) from trades\").fetchone()[0]); print('shadow_trades', c.execute(\"select source,count(*) from shadow_trades group by 1\").fetchall())"
```
Attendu : le compte de `trades` (sources `_v2`) est le MÊME qu'avant B4
(le shadow n'y touche jamais) ; `shadow_trades` montre 0 à quelques lignes
par source (un signal shadow a pu s'ouvrir, c'est normal et attendu).

### B6. Installer le cron du cycle shadow (lecture seule côté broker)
```bash
(crontab -l; echo '*/15 * * * * cd /home/assistant/assistant-trading && venv/bin/python scripts/run_shadow_cycle.py >> logs/shadow_cycle_cron.log 2>&1') | crontab -
```

### B7. Installer le cron des alertes shadow (aucun appel broker)
```bash
(crontab -l; echo '*/15 * * * * cd /home/assistant/assistant-trading && venv/bin/python scripts/shadow_milestone_alerts.py >> logs/shadow_milestone_alerts_cron.log 2>&1') | crontab -
```
```bash
crontab -l
```
Attendu : les 7 lignes désormais présentes (sauvegarde, watchdog,
financement, cycle d'ajustement, alertes de jalons `_v2`, cycle shadow,
alertes shadow).

### B8. Premier passage des alertes shadow (lecture seule)
```bash
venv/bin/python scripts/shadow_milestone_alerts.py
```
Attendu : une ligne par candidate, `n=0` (ou très peu), `eligible=[]`,
`nouveaux=[]`. Aucun message Telegram à ce stade (0 jalon atteint).

### B9. Installer le cron du rapport hebdomadaire shadow (lecture seule, aucun appel broker)
```bash
(crontab -l; echo '0 6 * * 1 cd /home/assistant/assistant-trading && venv/bin/python scripts/rapport_hebdo_shadow.py >> logs/rapport_hebdo_shadow_cron.log 2>&1') | crontab -
```
```bash
venv/bin/python scripts/rapport_hebdo_shadow.py
```
Attendu : `Rapport écrit : .../docs/SUIVI_SHADOW/AAAA-MM-JJ.md` puis
`Résumé Telegram envoyé.` (ou l'avertissement d'échec, sans arrêt).
```bash
cat docs/SUIVI_SHADOW/*.md
```
Attendu : une section par candidate (« Aucune époque shadow écrite » si
le cycle B4-B6 n'a encore ouvert aucun signal, sinon les chiffres).

### B10. Installer le cron de remesure mensuelle de fidélité (lecture seule, aucun appel broker)
**Ne PAS activer avant que `data/historical/` soit rafraîchi au-delà du
2026-09-25T18:00:00 UTC** (sinon la mesure reste bornée à la même
fenêtre pré-E1 que celle du 07/10/2026, voir `docs/FIDELITE_07-10.md`) —
attends le prochain rafraîchissement planifié de l'historique avant
cette étape, ou saute-la pour l'instant et reviens-y plus tard :
```bash
(crontab -l; echo '0 6 1 * * cd /home/assistant/assistant-trading && venv/bin/python scripts/mesure_fidelite_continue.py >> logs/mesure_fidelite_continue_cron.log 2>&1') | crontab -
```
```bash
crontab -l
```
Attendu : les 9 lignes désormais présentes (les 5 du runbook principal +
cycle shadow + alertes shadow + rapport hebdo shadow + remesure
mensuelle de fidélité).

---

## Partie C — vérifications (à refaire 1h, puis 1 jour après B6/B7)

### C1. Le cycle shadow tourne sans erreur
```bash
tail -n 30 logs/shadow_cycle_cron.log
```
Attendu : des lignes `Cycle shadow terminé : N position(s)...`, sans
`Traceback`.

### C2. Aucun ordre, toujours
```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('file:data/assistant_trading.db?mode=ro',uri=True); print(c.execute(\"select count(*) from trades where source like '%v3cand%'\").fetchone()[0])"
```
Attendu : `0` — **aucune source `_v3cand` ne doit JAMAIS apparaître dans
`trades`** (table réelle). Si ce nombre n'est pas 0 : arrête tout
(`crontab -l | grep -v shadow | crontab -`) et signale-le, c'est une
anomalie grave (ne devrait structurellement pas pouvoir se produire,
`src/shadow_tracking.py` n'appelle aucune méthode d'écriture broker).

### C3. Époques shadow écrites (T0)
```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('file:data/assistant_trading.db?mode=ro',uri=True); [print(r) for r in c.execute('select * from shadow_epochs')]"
```
Attendu : 4 lignes (une par candidate), `started_at` = l'heure de B4.

---

## Ce qui se passe ensuite (aucune action de ta part)

- Jalons : à n ≥ 30/40/53 signaux shadow réconciliés **ET** au moins 8
  semaines après T0 (le plus tardif des deux), une alerte Telegram arrive
  automatiquement avec l'espérance et le MDE — **aucune action
  automatique**, aucune promotion.
- Verdict forward : calculé et envoyé par Telegram à chaque jalon atteint
  (confirmée / indémontrable sur cette fenêtre / non confirmée).
- Promotion éventuelle en démo réelle (`hypothesisN_v3`) : **seulement**
  si le walk-forward (déjà calculé, voir
  `docs/EVOLUTION_V2_06-10.md`) ET le verdict forward sont tous deux
  positifs — reste une décision explicite d'Ismaël, jamais automatique.

## Retour arrière (si une étape échoue, ou pour arrêter le shadow)

```bash
crontab -l | grep -v -E "shadow_cycle|shadow_milestone|rapport_hebdo_shadow|mesure_fidelite_continue" | crontab -
```
Attendu : les 4 lignes shadow (cycle, alertes, rapport hebdo, remesure
mensuelle) disparaissent de `crontab -l`, les autres restent. Les tables `shadow_trades`/`shadow_partials`/`shadow_epochs`
peuvent être laissées telles quelles (lecture seule, sans effet sur rien
d'autre) ou vidées si souhaité :
```bash
venv/bin/python -c "import sqlite3; c=sqlite3.connect('data/assistant_trading.db'); c.execute('delete from shadow_partials'); c.execute('delete from shadow_trades'); c.execute('delete from shadow_epochs'); c.commit()"
```
Aucun impact sur les hypothèses `_v2`, le capital ou le plafond de
cluster dans tous les cas — ce suivi n'y touche jamais.
