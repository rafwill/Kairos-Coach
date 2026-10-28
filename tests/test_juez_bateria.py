"""
tests/test_juez_bateria.py
Reglas globales del juez de la batería (tools/juez_bateria.py) que no dependen de datos reales.
"""

import importlib.util
import sys
from pathlib import Path

from agent import tool_loop

_spec = importlib.util.spec_from_file_location("juez_bateria", Path(__file__).resolve().parent.parent / "tools" / "juez_bateria.py")
juez = importlib.util.module_from_spec(_spec)
sys.modules["juez_bateria"] = juez  # las dataclasses lo buscan ahí
_spec.loader.exec_module(juez)


def _verdict(body: str):
    v = juez.Verdict(n=1, question="¿Ha mejorado mi FC en reposo este mes?", seconds=1.0)
    juez.global_checks(v, body)
    return v


def test_loop_giving_up_is_a_failure():
    # 28/10 (decisión del usuario): la respuesta de tope del bucle pasaba como buena (bucle_8a_2, pregunta 6).
    v = _verdict(f"Kairos Coach\n{tool_loop.MAX_ITERATIONS_REPLY}")
    assert v.status == "FALLO" and any("no llegó a responder" in f for f in v.failures)


def test_provider_failure_is_an_environment_failure():
    v = _verdict("Kairos Coach\nLa consulta ha excedido el tiempo máximo de respuesta del modelo.\n\nOpciones: …")
    assert v.status == "ENTORNO"


def test_a_normal_answer_passes_the_global_checks():
    assert _verdict("## 🧭 Resumen\nTu FC en reposo ha bajado de 40 a 38 lpm este mes.").status == "OK"
