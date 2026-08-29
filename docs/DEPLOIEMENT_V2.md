# Déploiement V2 — refonte H1-H5 (L1-L5) + CHFJPY + legacy_sources

**À exécuter par Ismaël lui-même, en SSH, jamais par un agent** (invariant
du projet : aucun déploiement VPS sans son accord explicite). Ce document
est la séquence EXACTE, copier-coller, dans l'ordre. Chaque commande est
suivie du résultat attendu — si un résultat observé diffère, **arrêter et
ne PAS improviser** (voir "En cas d'écart" en fin de document).

Étape 1 du chantier en cours (docs/DECISIONS.md, 29/08/2026) — ce
déploiement ne dépend d'aucun calcul, d'aucune calibration, d'aucun gate :
c'est un déploiement de code (nouvelles logiques d'entrée L1-L5, CHFJPY,
mécanisme `legacy_sources`), pas un résultat de recherche.

## Ce que ce déploiement change (résumé, détail complet dans docs/DECISIONS.md)

- **H1-H5 tradent désormais leur nouvelle logique v2** (L1-L5 : régime ADX
  pour H1, confluence multi-résolution EMA/Ichimoku/RSI pour H2, pullback
  en tendance pour H3, divergence prix/RSI+OBV pour H4, compression
  Bollinger→expansion pour H5) au lieu des anciennes logiques v1.
- **CHFJPY ajouté aux 9 actifs, sur les 5 hypothèses**, dès le premier
  cycle après redémarrage (rattaché au cluster `fx_majors_jpy`, garde-fou
  de taille déjà couvert génériquement — voir docs/DECISIONS.md, point 3).
- **`legacy_sources`** : les positions v1 encore réellement ouvertes
  (H1/H3 seulement, 6 chacune au 29/08/2026) restent gérées
  (réconciliation, remplissage, trailing, `/stop_urgence`) sous leur
  ancienne étiquette de source, SANS bloquer le démarrage immédiat des
  signaux v2 sur les mêmes actifs.
- **Aucune migration de schéma manuelle nécessaire** — les seules
  colonnes ajoutées depuis le commit actuellement déployé (`12dc2b2`,
  `trade_partials.t_declenchement/p_declenchement/t_demande`) sont déjà
  en place (migration idempotente déjà appliquée par ce commit). Aucun
  changement de `src/db.py` dans ce lot. `init_db()` tourne de toute
  façon au démarrage de chaque process (idempotent, additif uniquement).
- **Aucune nouvelle variable d'environnement** — mêmes 5 comptes
  Capital.com déjà configurés dans `.env`.

## Vérifications avant de commencer

```bash
ssh assistant@163.172.189.239
cd ~/assistant-trading
git status
```
**Attendu** : seuls les fichiers déjà connus comme non trackés/modifiés
localement apparaissent (`scripts/backup_and_sync.sh` modifié — écart de
mode fichier uniquement, `chmod +x`, sans contenu différent ; quelques
scripts ponctuels et dossiers non trackés type `scripts/_info_tests.py`,
`data/comparisons/`, `logs/`, `db.sqlite`). **Aucun de ces éléments n'est
touché par le pull à venir** (vérifié : aucun commit entrant ne crée de
fichier à ces chemins). Si un AUTRE fichier apparaît modifié ou non
tracké (en particulier un fichier sous `src/` ou `tests/`), **arrêter** —
cela signifierait un travail en cours sur le VPS non documenté ici.

```bash
git log --oneline -1
```
**Attendu** : `12dc2b2 ...` (c'est le commit actuellement déployé — sert
de point de retour pour le rollback, voir en fin de document).

## Étape 1 — `git pull`

```bash
cd ~/assistant-trading
git pull origin main
```
**Attendu** : fast-forward propre, `56 files changed` environ (nouveaux
modules `src/hypothesis{1,2,3,4,5}_strategy_v2.py`, `docs/HYPOTHESES.md`
et `docs/DECISIONS.md` étendus, `src/executor.py`/`src/circuit_breaker.py`/
`src/asset_whitelist.py`/`src/technical_strategy_executor.py`/
`src/backtest_engine.py`/`src/trend_executor.py`/
`src/hypothesis{2,3,4,5}_executor.py` modifiés, anciens modules v1
déplacés vers `archive/`). Aucun message de conflit, aucun message
"untracked working tree file would be overwritten".

```bash
git log --oneline -1
```
**Attendu** : `e636120 ...` (dernier commit de ce chantier — vérifier que
ce hash correspond bien à `git log` en local avant de lancer le
déploiement ; s'il a évolué depuis l'écriture de ce document, c'est
attendu, prendre le HEAD réel).

## Étape 2 — Suite de tests (sécurité avant tout redémarrage)

```bash
source venv/bin/activate
python -m pytest -q
```
**Attendu** : tous les tests passent, aucune régression (le compte exact
de tests évolue à chaque session — se référer au nombre annoncé dans le
dernier commit de `docs/DECISIONS.md`, pas à un chiffre figé ici). Si un
test échoue : **ne pas redémarrer les process**, rapporter l'échec.

```bash
python -m pytest --cov=src.risk_engine --cov=src.capital_manager --cov=src.go_nogo --cov=src.validator --cov=src.trend_strategy --cov=src.circuit_breaker --cov=src.metrics --cov=src.confidence_scorer --cov=src.ict_strategy --cov=src.mean_reversion_strategy --cov=src.hypothesis1_strategy_v2 --cov=src.hypothesis2_strategy_v2 --cov=src.hypothesis3_strategy_v2 --cov=src.hypothesis4_strategy_v2 --cov=src.hypothesis5_strategy_v2 --cov-report=term-missing --cov-fail-under=100 tests/
```
**Attendu** : `100%` sur chaque module listé (financiers critiques +
les 5 nouvelles logiques d'entrée).

## Étape 3 — Arrêt propre des 6 process de production

Concerne exactement : `executor_loop`, `trend_executor`,
`hypothesis2_executor`, `hypothesis3_executor`, `hypothesis4_executor`,
`hypothesis5_executor`. **`telegram_listener` et `control_bot` ne sont
PAS touchés** par ce chantier (aucun de leurs fichiers n'a changé) —
ne pas les arrêter.

```bash
tmux ls
```
**Attendu (avant arrêt)** : les 6 sessions listées ci-dessus présentes,
plus `telegram_listener`/`control_bot` (et d'éventuelles sessions
ponctuelles de recherche, `backtest_download` etc. — sans rapport,
ignorer).

```bash
for s in executor_loop trend_executor hypothesis2_executor hypothesis3_executor hypothesis4_executor hypothesis5_executor; do
  tmux kill-session -t "$s" 2>/dev/null
done
sleep 2
ps aux | grep -E "python -m src\.(executor|trend_executor|hypothesis[2-5]_executor)" | grep -v grep
```
**Attendu** : la commande `ps aux` ne retourne **aucune ligne** — les 6
process sont bien arrêtés (pas seulement leur session tmux). Si une
ligne apparaît encore, le process a survécu à `kill-session` (arrive si
une session avait été repeuplée par `send-keys` sur un shell persistant
plutôt que lancée en one-shot) : `kill <PID>` explicitement sur le PID
affiché, puis revérifier.

## Étape 4 — Migration

**Aucune étape manuelle** : `init_db()` est appelé au tout début de
chaque `python -m src.<module>` (voir le bloc `if __name__ == "__main__"`
de chacun) et applique ses migrations de colonnes de façon idempotente et
strictement additive — jamais destructive, jamais de valeur imputée. Le
redémarrage de l'étape 5 déclenche cette vérification automatiquement
pour les 6 process. Rien à faire ici au-delà de le savoir.

## Étape 5 — Redémarrage sur les modules v2

Les points d'entrée (`python -m src.<module>`) sont **inchangés** — la
bascule v1→v2 est interne à chaque module (import de
`hypothesisN_strategy_v2` au lieu de l'ancien), pas un nouveau nom de
fichier à lancer.

```bash
cd ~/assistant-trading
tmux new-session -d -s executor_loop "source venv/bin/activate && python -m src.executor"
tmux new-session -d -s trend_executor "source venv/bin/activate && python -m src.trend_executor"
tmux new-session -d -s hypothesis2_executor "source venv/bin/activate && python -m src.hypothesis2_executor"
tmux new-session -d -s hypothesis3_executor "source venv/bin/activate && python -m src.hypothesis3_executor"
tmux new-session -d -s hypothesis4_executor "source venv/bin/activate && python -m src.hypothesis4_executor"
tmux new-session -d -s hypothesis5_executor "source venv/bin/activate && python -m src.hypothesis5_executor"
sleep 5
tmux ls
```
**Attendu** : les 6 sessions réapparaissent dans `tmux ls`.

Réactiver la capture de log persistante (convention du 26/08/2026, sinon
perdue au premier crash d'un pane one-shot) :

```bash
for s in executor_loop trend_executor hypothesis2_executor hypothesis3_executor hypothesis4_executor hypothesis5_executor; do
  tmux pipe-pane -o -t "$s" "cat >> /tmp/${s}_pipe.log"
done
```

## Étape 6 — Vérifications post-déploiement

### 6a. PIDs fraîches (pas d'ancien process survivant)

```bash
ps aux | grep -E "python -m src\.(executor|trend_executor|hypothesis[2-5]_executor)" | grep -v grep
```
**Attendu** : exactement 6 lignes, PID différents de ceux d'avant l'étape
3 (comparer à un `ps aux` pris avant, si conservé), heure de démarrage =
maintenant.

### 6b. Watchdog

```bash
sleep 300   # laisser passer au moins un cycle cron (*/5 * * * *)
tail -20 logs/watchdog_cron.log
```
**Attendu** : aucune alerte "process mort" pour les 6 process (une
alerte transitoire juste après le redémarrage, avant que le premier
cycle watchdog les revoie vivants, serait un faux positif normal — ne
s'inquiéter que d'une alerte qui persiste au cycle suivant).

### 6c. Premiers signaux, aucun rejet inattendu

```bash
for s in executor_loop trend_executor hypothesis2_executor hypothesis3_executor hypothesis4_executor hypothesis5_executor; do
  echo "== $s =="; tail -30 "/tmp/${s}_pipe.log"
done
```
**Attendu** : logique de démarrage normale (chargement config, connexion
Capital.com, première itération de boucle). Rechercher spécifiquement :
- Absence de `Traceback` / `ImportError` (signerait un import cassé d'un
  des 5 nouveaux modules `hypothesisN_strategy_v2.py`).
- Absence de rejets `POSITION_SIZE_STEP_DEVIATION` en masse (attendu à
  0 aujourd'hui, `size_step == minDealSize` pour les 9 actifs — un
  volume inhabituel signalerait une régression du garde-fou).
- Présence, avec un peu de patience (les résolutions HOUR/HOUR_4/DAY
  n'émettent pas à chaque cycle), d'au moins une ligne mentionnant
  `CHFJPY` dans chacun des 5 logs H1-H5 — confirme que le nouvel actif
  est bien évalué à chaque cycle, pas seulement présent dans la liste en
  mémoire.

### 6d. legacy_sources actif (H1 et H3 seulement)

```bash
grep -c "legacy" /tmp/trend_executor_pipe.log /tmp/hypothesis3_executor_pipe.log
```
Si aucune ligne explicite ne mentionne "legacy" dans les logs (le
mécanisme est silencieux en fonctionnement normal), vérifier plutôt en
base que les positions v1 encore ouvertes début de session sont
toujours gérées :
```bash
sqlite3 data/assistant_trading.db "SELECT source, actif, statut, count(*) FROM trades WHERE source IN ('hypothesis','hypothesis3') AND statut='ouvert' GROUP BY source, actif, statut;"
```
**Attendu** : les positions v1 déjà ouvertes avant ce déploiement
apparaissent toujours `statut='ouvert'` (pas orphelines) ; observer sur
les heures suivantes qu'elles continuent de recevoir des mises à jour de
trailing/clôture normalement — pas de nouvelle ligne `source='hypothesis'`
ou `source='hypothesis3'` créée après le redémarrage (les nouveaux
signaux doivent tous porter `hypothesis_v2`/`hypothesis3_v2`).

### 6e. Aucun actif silencieusement écarté

```bash
sqlite3 data/assistant_trading.db "SELECT source, actif, count(*) FROM signals WHERE created_at > datetime('now','-2 hours') GROUP BY source, actif ORDER BY source;"
```
Vérifier au fil des heures suivantes que CHFJPY finit par apparaître pour
chacune des 5 sources v2 — si un actif reste absent après plusieurs
cycles alors que les autres apparaissent, **rapporter, ne pas corriger
en silence** (consigne explicite du point 3 : jamais d'actif écarté sans
signalement).

## Rollback

À utiliser uniquement si l'étape 6 révèle un problème bloquant (crash en
boucle, rejets massifs inattendus, position orpheline détectée).

```bash
for s in executor_loop trend_executor hypothesis2_executor hypothesis3_executor hypothesis4_executor hypothesis5_executor; do
  tmux kill-session -t "$s" 2>/dev/null
done
sleep 2
cd ~/assistant-trading
git checkout 12dc2b2 -- .
```
**Attendu** : retour à l'arbre de fichiers exactement tel qu'avant
l'étape 1 (dernier commit connu-bon avant ce chantier). Ne PAS utiliser
`git reset --hard` (préserverait moins bien un éventuel travail
intermédiaire non lié à ce déploiement).

Puis relancer les 6 process avec **exactement** la commande de l'étape 5
(les points d'entrée n'ont pas changé, ils rechargeront simplement
l'ancien code v1) :
```bash
tmux new-session -d -s executor_loop "source venv/bin/activate && python -m src.executor"
tmux new-session -d -s trend_executor "source venv/bin/activate && python -m src.trend_executor"
tmux new-session -d -s hypothesis2_executor "source venv/bin/activate && python -m src.hypothesis2_executor"
tmux new-session -d -s hypothesis3_executor "source venv/bin/activate && python -m src.hypothesis3_executor"
tmux new-session -d -s hypothesis4_executor "source venv/bin/activate && python -m src.hypothesis4_executor"
tmux new-session -d -s hypothesis5_executor "source venv/bin/activate && python -m src.hypothesis5_executor"
```

Revenir ensuite sur `main` (pour ne pas rester en HEAD détaché) une fois
le rollback jugé stable, sans re-déployer le nouveau code tant que le
problème n'est pas corrigé :
```bash
git checkout main
```

## En cas d'écart

Toute divergence avec un "Attendu" ci-dessus = **arrêt, pas
d'improvisation ni de contournement du blocage**. Consigner l'écart
observé, le rapporter, proposer un correctif — jamais silencieusement
retenter le déploiement ni écarter un actif/hypothèse en douce (voir
"CE QUE CE CHANTIER NE FAIT PAS", docs/HYPOTHESES.md du 29/08/2026).
