# Règles de surveillance automatiques — pré-enregistrement (29/10/2026)

Écrit et commité AVANT toute analyse des trades post-P1_29-10 et avant le code
qui les applique. Compte DÉMO uniquement. Aucun LLM. Aucun plafond de risque ni
paramètre de stratégie modifié.

## Périmètre de données
Seuls les trades ouverts APRÈS le début de l'époque `P1_29-10`
(`hypothesis_epochs.started_at`, 2026-10-29T19:03:55Z), réconciliés
(statut `ferme`, R présent, anomalie nulle, tous les partiels avec
`prix_sortie_reel`). Un trade par hypothèse (R pondéré 50/30/20).

## Règle de suspension (identique pour H1-H5)
Pour chaque hypothèse Hk, avec n_k = nombre de trades post-P1_29-10 réconciliés :
- n_k < 15 : statut **non concluant** (jamais de suspension).
- n_k ≥ 15 : calcul de l'IC du R moyen ; si **borne haute < 0** → **suspendue**.
- Sinon (n_k ≥ 15, borne haute ≥ 0) : **actif**.

Méthode statistique : **IC bilatéral de Student** sur le R moyen
(moyenne ± t_{1-α/2, n-1} · s/√n), choisi car déterministe, sans aléa, et
conservateur à petit n (le bootstrap par percentiles sous-couvre à n≈15).
La suspension est une décision d'arrêt unilatérale (« moyenne < 0 ») : on
teste H0 : μ ≥ 0 contre μ < 0.

Correction pour comparaisons multiples : **Holm** sur les 5 hypothèses.
Le niveau par hypothèse est α_(i) = 0,05 / (5 − i + 1) selon le rang des
p-values croissantes (test t unilatéral). L'IC utilisé pour la règle d'une
hypothèse est l'IC de Student au niveau 1 − 2·α_Holm(rang) (bilatéral
équivalent à l'unilatéral α_Holm). Une hypothèse est suspendue seulement si
son test unilatéral est rejeté au seuil de Holm, ce qui est équivalent à
borne haute < 0 à ce niveau. Si n_k ≥ 15 et s = 0 : borne haute = moyenne.

Les hypothèses sont évaluées séparément (aucune agrégation entre elles).

## Contrôle quotidien
Code déterministe (`src/surveillance.py`, script `scripts/controle_surveillance.py`
exécuté une fois par jour) : calcule la table ci-dessous, écrit une ligne de
journal par suspension et persiste la suspension dans la base
(`hypothesis_suspensions`). Un exécuteur dont l'hypothèse est suspendue
**n'ouvre plus d'entrée** (la gestion des positions ouvertes continue).
Fail-safe : toute erreur du contrôle ne lève jamais une suspension déjà
posée et n'en crée pas d'implicite ; une suspension ne se lève **que par
redéploiement** (commande manuelle documentée, jamais par le code).

## État quotidien
Fichier `data/surveillance/etat_AAAA-MM-JJ.md` : par hypothèse × actif,
n post-P1_29-10, R moyen, IC (Student 95 %), statut (actif / non concluant /
suspendu). Le statut par actif est informatif : seule la ligne par hypothèse
peut suspendre (10 trades minimum par variable ; sous 15, non concluant).

## Garde-fous
Aucune variable de stratégie modifiée ; aucun stop élargi ; réel verrouillé.
