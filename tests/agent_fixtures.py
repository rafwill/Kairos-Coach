"""
tests/agent_fixtures.py
Fixtures compartidas por los tests de agent/ (28/10, al dividir tests/test_trainer_agent.py).
"""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_baseline_in_db():
    """Por defecto: activity_tss vacía y sin fila de semilla (los tests la sobrescriben si la necesitan)."""
    with patch("agent.trainer_agent._storage.get_activity_tss_map", return_value={}), \
         patch("agent.trainer_agent._storage.get_load_metrics_row", return_value=None):
        yield
