"""A7 (bilan du 05/10/2026) : journaux horodatés en UTC, format NIVEAU:module conservé."""

import logging
import re

from src.log_setup import configure_logging


def test_configured_log_line_is_timestamped_and_keeps_level_module_prefix(capsys):
    previous = logging.getLogger().handlers[:]
    try:
        configure_logging()
        logging.getLogger("src.executor").error("Échec du placement")
        line = capsys.readouterr().err.strip().splitlines()[-1]
    finally:
        logging.getLogger().handlers[:] = previous
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z ERROR:src\.executor:Échec du placement$", line)
