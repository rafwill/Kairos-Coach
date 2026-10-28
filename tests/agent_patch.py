"""
tests/agent_patch.py
Sustituir una función o un nombre de agent/ en todos los módulos que lo usan (28/10, pendiente 7).

Al dividir agent/trainer_agent.py, una función puede quedar en otro módulo que importa `call_tool`, `date`… por su
cuenta: sustituirla solo en trainer_agent dejaría ese módulo con la de verdad (y un test llamando a Garmin). Estos
ayudantes la sustituyen en cada módulo de agent/ donde ese nombre apunta al mismo objeto.
"""

import sys
from contextlib import ExitStack, contextmanager
from unittest.mock import DEFAULT, patch


def _bound_in(name: str) -> tuple[object, list]:
    """El objeto original y los módulos de agent/ que lo tienen con ese nombre (el que lo define, el primero)."""
    modules = [m for n, m in list(sys.modules.items())
               if m is not None and (n == "agent" or n.startswith("agent.")) and name in vars(m)]
    if not modules:
        raise AttributeError(f"ningún módulo de agent/ tiene {name!r}")
    defining = [m for m in modules if getattr(vars(m)[name], "__module__", None) == m.__name__]
    original = vars((defining or modules)[0])[name]
    same = [m for m in modules if vars(m)[name] is original]
    same.sort(key=lambda m: m not in defining)
    return original, same


def patch_everywhere(monkeypatch, name: str, value) -> None:
    """monkeypatch.setattr en todos los módulos de agent/ que usan `name`."""
    for module in _bound_in(name)[1]:
        monkeypatch.setattr(module, name, value)


@contextmanager
def patch_all(name: str, new=DEFAULT, **kwargs):
    """Como unittest.mock.patch, pero en todos los módulos de agent/ que usan `name`; devuelve el sustituto."""
    _, modules = _bound_in(name)
    with ExitStack() as stack:
        replacement = stack.enter_context(patch.object(modules[0], name, new, **kwargs))
        for module in modules[1:]:
            stack.enter_context(patch.object(module, name, replacement))
        yield replacement
