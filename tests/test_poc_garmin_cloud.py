"""Prueba de concepto de la web (fase 0, docs/web.md): el veredicto sigue el criterio fijado antes de ver resultados."""

from datetime import datetime, timedelta

from tools.poc_garmin_cloud import startup_calls, verdict

T0 = datetime(2026, 11, 7, 9, 0).astimezone()


def _pass(hours: float, state: str = "OK", login: bool = False, crash: str = "") -> dict:
    return {"cuando": (T0 + timedelta(hours=hours)).isoformat(timespec="seconds"), "login_nuevo": login,
            "tokens_renovados": False, "fallo": crash, "llamadas": [{"estado": state}] * 7}


def test_the_pass_makes_the_seven_startup_calls():
    calls = startup_calls(T0.date())
    assert [t for t, _ in calls].count("get_body_battery") == 2 and len(calls) == 7


def test_seventy_two_hours_without_failures_is_a_go():
    passes = [_pass(0, login=True)] + [_pass(3 * i) for i in range(1, 25)]
    assert verdict(passes)[0] == "VALE"


def test_too_short_is_still_running():
    assert verdict([_pass(3 * i) for i in range(10)])[0] == "EN CURSO"
    assert verdict([])[0] == "SIN DATOS"


def test_a_new_password_login_after_the_first_or_too_many_failures_is_a_no_go():
    passes = [_pass(3 * i) for i in range(25)]
    passes[10]["login_nuevo"] = True
    assert verdict(passes)[0] == "NO VALE"
    failing = [_pass(3 * i, state="ENTORNO" if i % 10 == 0 else "OK") for i in range(25)]
    assert verdict(failing)[0] == "NO VALE"  # 3 de 25 con 429: 88 % < 95 %
