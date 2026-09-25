#!/usr/bin/env bash
# restart_process.sh — Redémarrage supervisé d'UN process dans sa session tmux
# (26/09/2026, voir docs/DECISIONS.md). À n'utiliser que lors d'un redémarrage
# déjà planifié : ce script n'est lancé par aucun cron.
#
# `python -u` : sortie non tamponnée — sans lui, `| tee` retient les journaux
# jusqu'à plusieurs Ko (voire jusqu'à l'arrêt du process), ce qui rend les
# logs inutilisables pour surveiller la santé en direct.
#
# Usage : scripts/restart_process.sh <session>   (ou "all" pour les 6 exécuteurs)

set -u
cd "$(dirname "$0")/.."

declare -A MODULES=(
  [executor_loop]=src.executor
  [trend_executor]=src.trend_executor
  [hypothesis2_executor]=src.hypothesis2_executor
  [hypothesis3_executor]=src.hypothesis3_executor
  [hypothesis4_executor]=src.hypothesis4_executor
  [hypothesis5_executor]=src.hypothesis5_executor
  [telegram_listener]=src.telegram_listener
  [control_bot]=src.control_bot
)
EXECUTORS="executor_loop trend_executor hypothesis2_executor hypothesis3_executor hypothesis4_executor hypothesis5_executor"

restart_one() {
  local session="$1" module="${MODULES[$1]:-}"
  if [ -z "$module" ]; then echo "Session inconnue : $session"; return 1; fi
  tmux send-keys -t "$session" C-c
  sleep 3
  tmux send-keys -t "$session" \
    "cd $(pwd) && venv/bin/python -u -m $module 2>&1 | tee -a logs/$session.log" Enter
  sleep 6
  local pid
  pid=$(pgrep -f "python -u -m $module\$")
  if [ -z "$pid" ]; then echo "$session : ECHEC du redémarrage"; return 1; fi
  echo "$session OK pid=$pid démarré=$(date -u -d "$(ps -o lstart= -p "$pid")" +%Y-%m-%dT%H:%M:%SZ)"
}

if [ "${1:-}" = "all" ]; then
  for s in $EXECUTORS; do restart_one "$s" || exit 1; done
else
  restart_one "${1:?usage: $0 <session>|all}"
fi
