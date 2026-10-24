"""
tests/test_storage.py
Tests de no-regresión para la capa de persistencia.
"""

from agent import storage


class _DummyResult:
    def __init__(self, data=None):
        self.data = data if data is not None else []


class _DummyTable:
    def __init__(self, parent, name: str):
        self.parent = parent
        self.name = name
        self._mode = None
        self._payload = None

    def select(self, *_args, **_kwargs):
        self._mode = "select"
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._mode = "update"
        self._payload = payload
        return self

    def execute(self):
        if self._mode == "insert":
            self.parent.last_insert = self._payload
            return _DummyResult([])
        if self._mode == "update":
            self.parent.last_update = self._payload
            return _DummyResult([])
        return _DummyResult([])


class _DummySupabase:
    def __init__(self):
        self.last_insert = None
        self.last_update = None

    def table(self, name: str):
        return _DummyTable(self, name)


class _TimeoutOnceTable:
    def __init__(self, parent):
        self.parent = parent
        self._payload = None

    def upsert(self, payload):
        self._payload = payload
        return self

    def execute(self):
        self.parent.calls += 1
        if self.parent.calls == 1:
            raise Exception(
                {
                    "message": "canceling statement due to statement timeout",
                    "code": "57014",
                }
            )
        self.parent.last_upsert = self._payload
        return _DummyResult([])


class _TimeoutOnceSupabase:
    def __init__(self):
        self.calls = 0
        self.last_upsert = None

    def table(self, _name: str):
        return _TimeoutOnceTable(self)


def test_sanitize_credentials_removes_garmin_password_without_mutating_input():
    original = {
        "garmin_email": "runner@example.com",
        "garmin_password": "super-secret",
        "other": "value",
    }

    cleaned = storage._sanitize_credentials_for_storage(original)

    assert "garmin_password" not in cleaned
    assert cleaned["garmin_email"] == "runner@example.com"
    assert cleaned["other"] == "value"
    # No debe mutar el diccionario original
    assert original["garmin_password"] == "super-secret"


def test_update_user_credentials_never_persists_garmin_password(monkeypatch):
    fake_sb = _DummySupabase()

    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_require_supabase", lambda: fake_sb)

    storage.update_user_credentials(
        {
            "garmin_email": "runner@example.com",
            "garmin_password": "super-secret",
            "timezone": "Europe/Madrid",
        }
    )

    assert fake_sb.last_update is not None
    persisted_credentials = fake_sb.last_update["credentials"]
    assert "garmin_password" not in persisted_credentials
    assert persisted_credentials["garmin_email"] == "runner@example.com"
    assert persisted_credentials["timezone"] == "Europe/Madrid"


# ─── Supabase Auth (supabase/README.md) ─────────────────────────────────────


class _FakeUser:
    def __init__(self, email, uid="auth-uid-1"):
        self.email = email
        self.id = uid


class _FakeSession:
    def __init__(self, email):
        self.user = _FakeUser(email)


class _FakeAuth:
    def __init__(self, session_email=None, sign_in_error=None):
        self.session = _FakeSession(session_email) if session_email else None
        self.sign_in_error = sign_in_error
        self.signed_in_with = None

    def get_session(self):
        return self.session

    def sign_in_with_password(self, creds):
        if self.sign_in_error:
            raise Exception(self.sign_in_error)
        self.signed_in_with = creds
        self.session = _FakeSession(creds["email"])


class _FakeRpc:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return _DummyResult(self.value)


class _AuthSupabase(_DummySupabase):
    def __init__(self, auth, claimed_id=None, rows=None):
        super().__init__()
        self.auth = auth
        self.claimed_id = claimed_id
        self.rows = rows or []

    def rpc(self, name):
        assert name == "kairos_claim_app_user"
        return _FakeRpc(self.claimed_id)

    def table(self, name):
        table = _DummyTable(self, name)
        parent = self

        def _execute():
            if table._mode == "insert":
                parent.last_insert = table._payload
            return _DummyResult(parent.rows if table._mode == "select" else [])

        table.execute = _execute
        return table


def test_file_session_storage_roundtrip(tmp_path):
    store = storage._FileSessionStorage(tmp_path / "kairos" / "session.json")
    assert store.get_item("k") is None
    store.set_item("k", "v")
    assert storage._FileSessionStorage(tmp_path / "kairos" / "session.json").get_item("k") == "v"
    store.remove_item("k")
    assert store.get_item("k") is None


def test_auth_failure_reasons():
    assert storage._auth_failure(Exception("Invalid login credentials"))["reason"] == "invalid"
    assert storage._auth_failure(Exception("Email not confirmed"))["reason"] == "unconfirmed"
    missing = Exception("Could not find the function public.kairos_claim_app_user without parameters")
    assert storage._auth_failure(missing)["reason"] == "schema"


def test_sign_in_links_and_activates_existing_app_user(monkeypatch):
    fake = _AuthSupabase(_FakeAuth(), claimed_id="legacy-id",
                         rows=[{"id": "legacy-id", "username": "runner@example.com", "credentials": {}}])
    monkeypatch.setattr(storage, "_require_supabase", lambda: fake)

    out = storage.sign_in("Runner@Example.com", "secret1")

    assert out["ok"] and out["app_user"]["id"] == "legacy-id"
    assert fake.auth.signed_in_with == {"email": "runner@example.com", "password": "secret1"}
    assert storage.get_active_user() == {"user_id": "legacy-id", "username": "runner@example.com"}
    storage.set_active_user(None)


def test_sign_in_new_account_has_no_app_user(monkeypatch):
    fake = _AuthSupabase(_FakeAuth(), claimed_id=None)
    monkeypatch.setattr(storage, "_require_supabase", lambda: fake)
    out = storage.sign_in("new@example.com", "secret1")
    assert out == {"ok": True, "app_user": None, "error": None}


def test_sign_in_wrong_password(monkeypatch):
    fake = _AuthSupabase(_FakeAuth(sign_in_error="Invalid login credentials"))
    monkeypatch.setattr(storage, "_require_supabase", lambda: fake)
    assert storage.sign_in("runner@example.com", "bad")["reason"] == "invalid"


def test_restore_session_ignores_session_of_another_email(monkeypatch):
    fake = _AuthSupabase(_FakeAuth(session_email="other@example.com"), claimed_id="x")
    monkeypatch.setattr(storage, "_require_supabase", lambda: fake)
    assert storage.restore_session("runner@example.com") is None


def test_create_app_user_links_auth_and_never_persists_garmin_password(monkeypatch):
    fake = _AuthSupabase(_FakeAuth(session_email="new@example.com"))
    monkeypatch.setattr(storage, "_require_supabase", lambda: fake)

    storage.create_app_user("new@example.com", {"garmin_email": "new@example.com", "garmin_password": "super-secret"})

    row = fake.last_insert
    assert row["username"] == "new@example.com" and row["auth_user_id"] == "auth-uid-1"
    assert "garmin_password" not in row["credentials"]
    assert "password_hash" not in row
    storage.set_active_user(None)


def test_is_statement_timeout_error_detects_postgrest_payload():
    exc = Exception({"message": "canceling statement due to statement timeout", "code": "57014"})
    assert storage._is_statement_timeout_error(exc) is True


def test_save_user_profile_retries_once_on_statement_timeout(monkeypatch):
    fake_sb = _TimeoutOnceSupabase()
    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_require_supabase", lambda: fake_sb)
    monkeypatch.setattr(storage.time, "sleep", lambda _s: None)

    storage.save_user_profile({"personal": {"name": "Rafa"}})

    assert fake_sb.calls == 2
    assert fake_sb.last_upsert == {
        "app_user_id": "user-1",
        "data": {"personal": {"name": "Rafa"}},
    }


def test_persist_session_summary_daily_updates_existing_date(monkeypatch):
    state = {
        "history": [],
        "session_summaries": [
            {"date": "2026-08-14", "summary": "old-1"},
            {"date": "2026-08-15", "summary": "old-today"},
        ],
    }
    saved = {}

    monkeypatch.setattr(storage, "load_session_context", lambda: dict(state))
    monkeypatch.setattr(storage, "save_session_context", lambda ctx: saved.update(ctx))

    class _FakeDate:
        @staticmethod
        def today():
            class _D:
                @staticmethod
                def isoformat():
                    return "2026-08-15"
            return _D()

    monkeypatch.setattr(storage, "date", _FakeDate)

    storage.persist_session_summary_daily("new-summary")

    summaries = saved.get("session_summaries") or []
    today = [s for s in summaries if s.get("date") == "2026-08-15"]
    assert len(today) == 1
    assert today[0]["summary"] == "new-summary"


def test_persist_session_summary_daily_appends_when_missing(monkeypatch):
    state = {"history": [], "session_summaries": [{"date": "2026-08-14", "summary": "old"}]}
    saved = {}

    monkeypatch.setattr(storage, "load_session_context", lambda: dict(state))
    monkeypatch.setattr(storage, "save_session_context", lambda ctx: saved.update(ctx))

    class _FakeDate:
        @staticmethod
        def today():
            class _D:
                @staticmethod
                def isoformat():
                    return "2026-08-15"
            return _D()

    monkeypatch.setattr(storage, "date", _FakeDate)

    storage.persist_session_summary_daily("today-summary")

    summaries = saved.get("session_summaries") or []
    assert any(s.get("date") == "2026-08-15" and s.get("summary") == "today-summary" for s in summaries)


class _MemoryResult:
    def __init__(self, data=None):
        self.data = data if data is not None else []


class _MemoryTable:
    def __init__(self, parent, name: str):
        self.parent = parent
        self.name = name
        self._mode = None
        self._payload = None
        self._filters: list[tuple[str, str, object]] = []
        self._order: tuple[str, bool] | None = None
        self._limit = None

    def select(self, *_args, **_kwargs):
        self._mode = "select"
        return self

    def eq(self, field, value):
        self._filters.append(("eq", field, value))
        return self

    def neq(self, field, value):
        self._filters.append(("neq", field, value))
        return self

    def gte(self, field, value):
        self._filters.append(("gte", field, value))
        return self

    def order(self, field, desc=False):
        self._order = (field, bool(desc))
        return self

    def limit(self, n):
        self._limit = int(n)
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._mode = "update"
        self._payload = payload
        return self

    def upsert(self, payload, *_args, **_kwargs):
        self._mode = "upsert"
        self._payload = payload
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def _rows(self):
        return self.parent.rows.setdefault(self.name, [])

    def _match(self, row):
        for op, field, value in self._filters:
            if op == "eq" and row.get(field) != value:
                return False
            if op == "neq" and row.get(field) == value:
                return False
            if op == "gte" and not (row.get(field) is not None and row.get(field) >= value):
                return False
        return True

    def _selected(self):
        items = [dict(r) for r in self._rows() if self._match(r)]
        if self._order:
            field, is_desc = self._order
            items = sorted(items, key=lambda x: x.get(field), reverse=is_desc)
        if self._limit is not None:
            items = items[: self._limit]
        return items

    def execute(self):
        rows = self._rows()

        if self._mode == "select":
            return _MemoryResult(self._selected())

        if self._mode == "insert":
            payload = self._payload
            if isinstance(payload, list):
                for item in payload:
                    rows.append(dict(item))
                return _MemoryResult([dict(x) for x in payload])
            rows.append(dict(payload))
            return _MemoryResult([dict(payload)])

        if self._mode == "update":
            updated = []
            for row in rows:
                if self._match(row):
                    row.update(dict(self._payload or {}))
                    updated.append(dict(row))
            return _MemoryResult(updated)

        if self._mode == "delete":
            kept = []
            deleted = []
            for row in rows:
                if self._match(row):
                    deleted.append(dict(row))
                else:
                    kept.append(row)
            self.parent.rows[self.name] = kept
            return _MemoryResult(deleted)

        if self._mode == "upsert":
            payload = dict(self._payload or {})
            if self.name == "training_plan_version":
                match = None
                for row in rows:
                    if row.get("plan_id") == payload.get("plan_id") and row.get("version_number") == payload.get("version_number"):
                        match = row
                        break
                if match is not None:
                    match.update(payload)
                else:
                    rows.append(payload)
            else:
                rows.append(payload)
            return _MemoryResult([payload])

        return _MemoryResult([])


class _MemorySupabase:
    def __init__(self):
        self.rows = {
            "training_plan": [],
            "training_plan_session": [],
            "training_plan_version": [],
        }

    def table(self, name: str):
        return _MemoryTable(self, name)


class _MemoryTableNoStructured(_MemoryTable):
    def execute(self):
        if self.name == "training_plan_session" and self._mode == "insert":
            payload = self._payload
            rows = payload if isinstance(payload, list) else [payload]
            for row in rows:
                if isinstance(row, dict) and "structured_workout" in row:
                    raise Exception("column training_plan_session.structured_workout does not exist")
        return super().execute()


class _MemorySupabaseNoStructured(_MemorySupabase):
    def table(self, name: str):
        return _MemoryTableNoStructured(self, name)


class _MissingTableError(Exception):
    pass


class _MissingTrainingPlanTable:
    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        raise _MissingTableError(
            {
                "message": "Could not find the table 'public.training_plan' in the schema cache",
                "code": "PGRST205",
            }
        )


class _MissingTrainingPlanSupabase:
    def table(self, _name: str):
        return _MissingTrainingPlanTable()


def test_create_training_plan_enforces_single_active_and_versions(monkeypatch):
    fake_sb = _MemorySupabase()
    fake_sb.rows["training_plan"].append(
        {
            "id": "old-plan",
            "app_user_id": "user-1",
            "title": "Plan anterior",
            "status": "active",
        }
    )

    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_require_supabase", lambda: fake_sb)

    created = storage.create_training_plan(
        {
            "title": "Plan 10K",
            "description": "Base + específico",
            "objective": "Bajar de 45'",
            "difficulty": "moderate",
            "duration_weeks": 12,
            "status": "active",
        },
        sessions=[
            {
                "week_index": 1,
                "day_index": 2,
                "session_type": "running",
                "duration_min": 50,
                "intensity": "Z2",
                "exercises": ["rodaje"],
            }
        ],
    )

    plans = fake_sb.rows["training_plan"]
    active = [p for p in plans if p.get("app_user_id") == "user-1" and p.get("status") == "active"]
    assert len(active) == 1
    assert active[0]["id"] == created["id"]
    assert any(p.get("id") == "old-plan" and p.get("status") == "inactive" for p in plans)

    sessions = fake_sb.rows["training_plan_session"]
    assert len(sessions) == 1
    assert sessions[0]["plan_id"] == created["id"]
    assert isinstance(sessions[0].get("structured_workout"), dict)

    versions = fake_sb.rows["training_plan_version"]
    assert len(versions) == 1
    assert versions[0]["plan_id"] == created["id"]
    assert versions[0]["version_number"] == 1


def test_update_training_plan_creates_new_version_per_edit(monkeypatch):
    fake_sb = _MemorySupabase()

    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_require_supabase", lambda: fake_sb)

    created = storage.create_training_plan(
        {
            "title": "Plan Maratón",
            "status": "inactive",
        }
    )

    updated = storage.update_training_plan(
        created["id"],
        {"title": "Plan Maratón v2", "description": "Ajustado por fatiga"},
        change_reason="edit",
    )

    assert updated["title"] == "Plan Maratón v2"
    versions = [v for v in fake_sb.rows["training_plan_version"] if v.get("plan_id") == created["id"]]
    assert len(versions) == 2
    numbers = sorted(v.get("version_number") for v in versions)
    assert numbers == [1, 2]


def test_activate_training_plan_switches_active_plan_and_versions(monkeypatch):
    fake_sb = _MemorySupabase()

    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_require_supabase", lambda: fake_sb)

    first = storage.create_training_plan({"title": "Plan A", "status": "active"})
    second = storage.create_training_plan({"title": "Plan B", "status": "inactive"})

    storage.activate_training_plan(second["id"])

    p1 = storage.get_training_plan(first["id"])
    p2 = storage.get_training_plan(second["id"])
    assert p1 is not None and p1["status"] == "inactive"
    assert p2 is not None and p2["status"] == "active"

    versions = [v for v in fake_sb.rows["training_plan_version"] if v.get("plan_id") == second["id"]]
    assert len(versions) == 2


def test_create_training_plan_falls_back_when_structured_workout_column_missing(monkeypatch):
    fake_sb = _MemorySupabaseNoStructured()

    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_require_supabase", lambda: fake_sb)

    created = storage.create_training_plan(
        {
            "title": "Plan fallback",
            "status": "active",
        },
        sessions=[
            {
                "week_index": 1,
                "day_index": 1,
                "session_type": "running_z2",
                "duration_min": 45,
                "intensity": "RPE 3-4",
                "structured_workout": {
                    "schema": "kairos-workout-v1",
                    "sessionType": "running_z2",
                    "steps": [{"name": "Main", "type": "steady", "duration_min": 45, "intensityClass": "endurance"}],
                },
            }
        ],
    )

    assert created["id"]
    sessions = fake_sb.rows["training_plan_session"]
    assert len(sessions) == 1
    assert "structured_workout" not in sessions[0]


def test_get_active_training_plan_returns_none_when_table_missing(monkeypatch):
    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_require_supabase", lambda: _MissingTrainingPlanSupabase())

    assert storage.get_active_training_plan() is None


def test_wellness_daily_roundtrip(monkeypatch):
    fake_sb = _MemorySupabase()

    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_supabase", lambda: fake_sb)

    storage.upsert_wellness_daily(
        "2026-10-06",
        body_battery={
            "level": 69,
            "charged": 58,
            "drained": 31,
            "highest": 100,
            "lowest": 42,
        },
        hrv={
            "last_night_avg_ms": 51,
            "weekly_avg_ms": 55,
            "status": "balanced",
        },
        source_payload={"source": "mcp"},
    )

    row = storage.get_wellness_daily("2026-10-06")
    assert row is not None
    assert float(row.get("body_battery_level")) == 69.0
    assert float(row.get("bb_charged")) == 58.0
    assert float(row.get("bb_drained")) == 31.0
    assert float(row.get("hrv_last_night_avg_ms")) == 51.0
    assert float(row.get("hrv_weekly_avg_ms")) == 55.0
    assert row.get("hrv_status") == "balanced"
    assert isinstance(row.get("pulled_at"), str)


def test_upsert_wellness_daily_ignores_empty_payload(monkeypatch):
    fake_sb = _MemorySupabase()

    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_supabase", lambda: fake_sb)

    storage.upsert_wellness_daily("2026-10-06")
    assert fake_sb.rows.get("wellness_daily", []) == []


def test_get_activity_tss_map_returns_rows_from_start_date(monkeypatch):
    fake_sb = _MemorySupabase()
    fake_sb.rows["activity_tss"] = [
        {"app_user_id": "user-1", "activity_id": 1, "start_date": "2026-10-01", "sport": "running", "tss": 55.5, "method": "TSS", "model": "running:pipeline_fisico"},
        {"app_user_id": "user-1", "activity_id": 2, "start_date": "2026-09-01", "sport": "running", "tss": 40.0, "method": "TSS", "model": "x"},
        {"app_user_id": "user-2", "activity_id": 3, "start_date": "2026-10-02", "sport": "running", "tss": 1.0, "method": "TSS", "model": "x"},
    ]
    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_supabase", lambda: fake_sb)

    result = storage.get_activity_tss_map("2026-09-15")

    assert list(result) == [1]
    assert result[1]["tss"] == 55.5
    assert result[1]["model"] == "running:pipeline_fisico"


def test_get_activity_tss_map_missing_table_means_no_baseline(monkeypatch):
    class _MissingActivityTssTable(_MissingTrainingPlanTable):
        def gte(self, *_args, **_kwargs):
            return self

        def execute(self):
            raise _MissingTableError(
                {"message": "Could not find the table 'public.activity_tss' in the schema cache", "code": "PGRST205"}
            )

    class _MissingActivityTssSupabase:
        def table(self, _name):
            return _MissingActivityTssTable()

    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_supabase", lambda: _MissingActivityTssSupabase())

    assert storage.get_activity_tss_map("2026-09-15") == {}


def test_get_activity_tss_map_read_error_returns_none(monkeypatch):
    class _BrokenTable(_MissingTrainingPlanTable):
        def gte(self, *_args, **_kwargs):
            return self

        def execute(self):
            raise Exception("connection reset")

    class _BrokenSupabase:
        def table(self, _name):
            return _BrokenTable()

    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_supabase", lambda: _BrokenSupabase())

    assert storage.get_activity_tss_map("2026-09-15") is None


def test_get_load_metrics_row_returns_day_or_none(monkeypatch):
    fake_sb = _MemorySupabase()
    fake_sb.rows["load_metrics_daily"] = [
        {"app_user_id": "user-1", "metric_date": "2026-10-19", "tss": 10, "atl": 40.5, "ctl": 58.25, "tsb": 17.75, "activities_count": 1},
    ]
    monkeypatch.setattr(storage, "_require_active_user_id", lambda: "user-1")
    monkeypatch.setattr(storage, "_supabase", lambda: fake_sb)

    row = storage.get_load_metrics_row("2026-10-19")
    assert row["ctl"] == 58.25 and row["atl"] == 40.5
    assert storage.get_load_metrics_row("2026-10-18") is None
