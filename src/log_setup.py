"""
log_setup.py — Format de journal commun aux process de production (A7,
bilan du 05/10/2026).

Jusqu'ici `logging.basicConfig(level=INFO)` produisait des lignes SANS
horodatage (`INFO:src.executor:...`) : impossible de dater une erreur ou un
429 a posteriori. Le format ajoute un horodatage UTC ISO en tête et garde
ensuite exactement `NIVEAU:module:message`, pour que les recherches déjà
utilisées (`grep "ERROR:src.executor"`) fonctionnent toujours.
"""

import logging
import time

LOG_FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)s:%(name)s:%(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def configure_logging(level: int = logging.INFO) -> None:
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    formatter.converter = time.gmtime
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
