# Checkpoint — mandat autonome du 08/10/2026, Partie 1

## Étape 1 — TERMINÉE (commit `0562c63`)

`scripts/rafraichir_historique.py` écrit, 31 tests (broker mocké),
100% de couverture de ligne. Suite complète du projet : 1489/1489 verts
(aucune régression). Deux bugs réels trouvés et corrigés pendant les
tests, avant tout appel broker réel (détail dans
`docs/AUTONOMIE_08-10.md`).

27 combinaisons confirmées par code (H1 9×HOUR, H3 9×HOUR, H4 9×HOUR —
aucun override actif en base —, H2 9×{HOUR,HOUR_4,DAY}), union = les 27
fichiers déjà présents dans `data/historical/`.

## Étape 2 — TERMINÉE

`python scripts/rafraichir_historique.py` exécuté contre le compte démo
réel (vérifié `demo` par code avant tout appel). **27/27 combinaisons
`ok`, 0 abandon d'intégrité, 0 429, 27/200 appels consommés.** Nouvelle
borne uniforme HOUR sur les 9 actifs : **2026-10-07T04:00:00 UTC**
(c'était 2026-09-25T18:00:00 avant). Détail par combinaison (bougies
avant->après) dans la sortie du script, résumé dans
`docs/AUTONOMIE_08-10_PARTIE1.md` §2 une fois écrit.

Fichiers `data/historical/*.json` modifiés en place (append-only,
jamais commités — `data/historical/` est dans `.gitignore`).

**Prochaine étape : Étape 3** — mesure de fidélité H1-H4 (config.
actuelle, données rafraîchies), population `ouvert_at >=
2026-09-25T19:35:00` jusqu'à `2026-10-07T04:00:00`, CHFJPY à part pour
les 4 hypothèses. Live déjà compté sur le snapshot `prod_07-10.db` :
H1=8, H2=12, H3=7, H4=10 trades éligibles (0 CHFJPY) — tous < n=20, donc
« non établie (n insuffisant) » attendu pour les 4 sauf données live plus
fraîches non disponibles localement (pas d'accès VPS dans ce mandat,
limite à documenter en §6 du rapport final).
