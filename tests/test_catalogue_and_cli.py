import pytest

from labbook import create_app
from labbook.cli import create_admin, seed
from labbook.models import EquipmentType, EquipmentUnit, Lab, Role, UnitStatus, User


@pytest.fixture
def catalogue(db):
    lab_a = Lab(name="Lab A", term_hours={"mon": [["08:00", "18:00"]]})
    lab_b = Lab(name="Lab B", term_hours={})
    scope = EquipmentType(name="Oscilloscope", category="Scopes",
                          description="Use **carefully** <script>alert(1)</script>")
    sa = EquipmentType(name="Spectrum Analyzer", category="RF", requires_approval=True)
    old = EquipmentType(name="Chart Recorder", category="Scopes", is_archived=True)
    db.session.add_all([lab_a, lab_b, scope, sa, old])
    db.session.flush()
    db.session.add_all([
        EquipmentUnit(type_id=scope.id, lab_id=lab_a.id, asset_tag="OSC-01"),
        EquipmentUnit(type_id=scope.id, lab_id=lab_a.id, asset_tag="OSC-02", status=UnitStatus.OUT_OF_SERVICE),
        EquipmentUnit(type_id=sa.id, lab_id=lab_b.id, asset_tag="SA-01"),
    ])
    db.session.commit()
    return {"lab_a": lab_a, "lab_b": lab_b, "scope": scope, "sa": sa, "old": old}


def test_catalogue_lists_active_types_with_filters(login_as, client, catalogue):
    login_as()
    page = client.get("/equipment/").text
    assert "Oscilloscope" in page and "Spectrum Analyzer" in page
    assert "Chart Recorder" not in page
    assert "1 of 1</strong> free now" in page

    assert "Spectrum Analyzer" not in client.get("/equipment/?no_approval=1").text
    assert "Oscilloscope" not in client.get(f"/equipment/?lab={catalogue['lab_b'].id}").text
    assert "Spectrum Analyzer" not in client.get("/equipment/?category=Scopes").text
    assert "Oscilloscope" not in client.get("/equipment/?q=spectrum").text


def test_detail_renders_markdown_safely(login_as, client, catalogue):
    login_as()
    page = client.get(f"/equipment/{catalogue['scope'].id}").text
    assert "<strong>carefully</strong>" in page
    assert "<script>alert(1)</script>" not in page


def test_archived_type_hidden_from_students(login_as, client, catalogue):
    login_as()
    assert client.get(f"/equipment/{catalogue['old'].id}").status_code == 404


def test_admin_home_shows_setup_checklist(login_as, client):
    login_as(Role.ADMIN)
    assert "Finish setting up LabBook" in client.get("/").text


def test_pages_render_for_each_role(login_as, client, catalogue):
    login_as(Role.ADMIN)
    for path in ["/", "/profile", "/privacy", "/equipment/", f"/equipment/{catalogue['scope'].id}", "/units/",
                 "/units/new", "/admin/users", "/admin/users/new", "/admin/labs", "/admin/labs/new",
                 f"/admin/labs/{catalogue['lab_a'].id}", "/admin/calendar", "/admin/calendar/terms/new",
                 "/admin/calendar/holidays/new", "/admin/types", "/admin/types/new",
                 f"/admin/types/{catalogue['scope'].id}", "/admin/settings", "/admin/privacy", "/nope"]:
        response = client.get(path)
        assert response.status_code == (404 if path == "/nope" else 200), path


# --- CLI ------------------------------------------------------------------------------------------

def test_create_admin_refuses_second_run_without_force(app, db):
    runner = app.test_cli_runner()
    result = runner.invoke(create_admin, ["--email", "Boss@Example.edu", "--name", "Boss"],
                           input="admin-password-1\nadmin-password-1\n")
    assert result.exit_code == 0, result.output
    admin = db.session.query(User).filter_by(email="boss@example.edu").one()
    assert admin.role is Role.ADMIN and admin.is_verified

    result = runner.invoke(create_admin, ["--email", "two@example.edu", "--name", "Two"],
                           input="admin-password-1\nadmin-password-1\n")
    assert result.exit_code != 0 and "--force" in result.output

    result = runner.invoke(create_admin, ["--email", "two@example.edu", "--name", "Two", "--force", "--invite"])
    assert result.exit_code == 0, result.output
    assert db.session.query(User).filter_by(role=Role.ADMIN).count() == 2


def test_seed_refuses_in_production(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    app = create_app("production", SECRET_KEY="x" * 40, SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'p.db'}")
    with app.app_context():
        result = app.test_cli_runner().invoke(seed)
    assert result.exit_code != 0 and "production" in result.output


def test_seed_loads_demo_data(app, db):
    result = app.test_cli_runner().invoke(seed)
    assert result.exit_code == 0, result.output
    assert db.session.query(User).count() == 5
    assert db.session.query(EquipmentType).count() >= 5
    assert app.test_cli_runner().invoke(seed).exit_code != 0  # already seeded
    assert app.test_cli_runner().invoke(seed, ["--reset"]).exit_code == 0
