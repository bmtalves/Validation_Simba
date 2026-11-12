import csv

import pytest
import ifcopenshell
import ifcopenshell.api

import Validation_Simba
from Validation_Simba import Atom, Checker, find_required, get_arguments, make_label, parse_parameters, write_property, find_old_status
from Validation_Simba import Concept, validate, dashboard_data, find_rule_files, load_allowed_values

PROCESS_STATUS = ("rule", parse_parameters(
    "Set[Value]='NONS_Process' AND PropertyName[Value]='ProcessStatus' AND Value[Exists]=TRUE AND Value[Type]='IFCLABEL'"))
TILE_WIDTH = ("rule", parse_parameters(
    "Set[Value]='Pset_CoveringCeiling' AND PropertyName[Value]='TileWidth' AND Value[Exists]=TRUE AND Value[Type]='IFCPOSITIVELENGTHMEASURE'"))


def make_model():
    model = ifcopenshell.file(schema="IFC4")
    run = ifcopenshell.api.run
    wall_type = run("root.create_entity", model, ifc_class="IfcWallType", name="AVA.001", predefined_type="SOLIDWALL")
    pset = run("pset.add_pset", model, product=wall_type, name="Pset_WallCommon")
    run("pset.edit_pset", model, pset=pset, properties={"IsExternal": True})
    typed_wall = run("root.create_entity", model, ifc_class="IfcWall", name="AVA.001")
    run("type.assign_type", model, related_objects=[typed_wall], relating_type=wall_type)
    lone_wall = run("root.create_entity", model, ifc_class="IfcWall", name="AVA.002")
    return model, typed_wall, lone_wall


# Same shape Statsbygg uses for every property requirement
IS_EXTERNAL = ("or", [
    ("rule", parse_parameters("Set[Value]='Pset_WallCommon' AND PropertyName[Value]='IsExternal' AND Value[Exists]=TRUE AND Value[Type]='IFCBOOLEAN'")),
    ("and", [
        ("nor", [("rule", parse_parameters("Set[Value]='Pset_WallCommon' AND PropertyName[Value]='IsExternal'"))]),
        ("rule", parse_parameters("T_Set[Value]='Pset_WallCommon' AND T_PropertyName[Value]='IsExternal' AND T_Value[Exists]=TRUE AND T_Value[Type]='IFCBOOLEAN'")),
    ]),
])
PREDEFINED_TYPE = ("rule", parse_parameters("PredefinedType[Exists]=TRUE OR T_PredefinedType[Exists]=TRUE"))


def test_parse_parameters():
    assert parse_parameters("Name[Exists]=TRUE") == [[Atom(False, "Name", "Exists", "TRUE")]]
    assert parse_parameters("PredefinedType[Exists]=TRUE OR T_PredefinedType[Exists]=TRUE") == [
        [Atom(False, "PredefinedType", "Exists", "TRUE")],
        [Atom(True, "PredefinedType", "Exists", "TRUE")],
    ]
    assert parse_parameters("LongName[Value]='Statsbyggs Byggnummer'") == [[Atom(False, "LongName", "Value", "Statsbyggs Byggnummer")]]
    assert parse_parameters("CRSName[Value]=reg'^EPSG:(.*)'") == [[Atom(False, "CRSName", "Value", "^EPSG:(.*)", True)]]


def test_find_required_and_label():
    required = find_required(IS_EXTERNAL)
    assert required == ("property", "Pset_WallCommon", "IsExternal", "IFCBOOLEAN")
    assert make_label("e2db IsExternal : 9f80 Pset_WallCommon : 200 Wall", required) == "Pset_WallCommon.IsExternal"
    assert find_required(("rule", parse_parameters("LongName[Exists]=TRUE"))) == ("attribute", "LongName")
    assert find_required(PREDEFINED_TYPE) is None
    assert make_label("0004 Predefined type : 200 Wall : 01 Architectural (ARK)", None) == "Predefined type"


def test_property_inherited_from_type():
    model, typed_wall, lone_wall = make_model()
    checker = Checker(model)
    assert checker.evaluate(IS_EXTERNAL, typed_wall) == True
    assert checker.evaluate(IS_EXTERNAL, lone_wall) == False


def test_wrong_value_type_on_element_fails():
    model, typed_wall, _ = make_model()
    pset = ifcopenshell.api.run("pset.add_pset", model, product=typed_wall, name="Pset_WallCommon")
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"IsExternal": model.create_entity("IfcLabel", "yes")})
    # The element's own (wrong) value hides the type's value
    assert Checker(model).evaluate(IS_EXTERNAL, typed_wall) == False


def test_predefined_type():
    model, typed_wall, lone_wall = make_model()
    checker = Checker(model)
    assert checker.evaluate(PREDEFINED_TYPE, typed_wall) == True
    assert checker.evaluate(PREDEFINED_TYPE, lone_wall) == False


def test_find_old_status():
    model, typed_wall, lone_wall = make_model()
    write_property(model, typed_wall, "NOSSB_Process", "DesignedStatus", "IFCLABEL", "400")
    write_property(model, lone_wall, "NOSSB_Process", "DesignedStatus", "IFCLABEL", "300")
    write_property(model, lone_wall, "NONS_Process", "ProcessStatus", "IFCLABEL", "Godkjent")
    # lone_wall already has a ProcessStatus, so it must not be overwritten
    assert find_old_status(model) == [(typed_wall, "400")]


def test_migrate_designed_status(monkeypatch):
    model, typed_wall, lone_wall = make_model()
    write_property(model, typed_wall, "NOSSB_Process", "DesignedStatus", "IFCLABEL", "350")
    monkeypatch.setattr(Validation_Simba, "ask_yes_no", lambda prompt: True)
    assert Validation_Simba.migrate_designed_status(model) == True
    assert Checker(model).evaluate(PROCESS_STATUS, typed_wall) == True
    assert Checker(model).evaluate(PROCESS_STATUS, lone_wall) == False
    # Nothing left to copy the second time
    assert Validation_Simba.migrate_designed_status(model) == False


def test_dashboard_data():
    model, typed_wall, lone_wall = make_model()
    concept = Concept("200 Wall", "IfcWall", "Pset_WallCommon.IsExternal", IS_EXTERNAL, None, find_required(IS_EXTERNAL))
    results = validate(model, [concept])
    data = dashboard_data(results, model, "folder/model.ifc", "B5.1.1 Handover")
    assert data["model"] == "model.ifc"
    assert data["requirements"] == [{"group": "200 Wall", "label": "Pset_WallCommon.IsExternal", "index": 0,
                                     "pset": "Pset_WallCommon", "type": "IFCBOOLEAN", "passed": 1, "failed": 0, "missing": 1}]
    failed = {o["name"]: o["failed"] for o in data["objects"]}
    assert failed == {"AVA.001": [], "AVA.002": ["Pset_WallCommon.IsExternal"]}
    # [object, requirement, status (1 passed, 0 failed, 2 missing), value index (-1 = missing)]
    assert data["values"] == ["TRUE"]
    checks = {data["objects"][obj]["name"]: (passed, value) for obj, req, passed, value in data["checks"]}
    assert checks == {"AVA.001": (1, 0), "AVA.002": (2, -1)}


def test_values_recorded():
    model, typed_wall, lone_wall = make_model()
    # Element's own wrong-typed value is shown with its type, and wins over the type's TRUE
    write_property(model, typed_wall, "Pset_WallCommon", "IsExternal", "IFCLABEL", "yes")
    write_property(model, lone_wall, "Pset_CoveringCeiling", "TileWidth", "IFCPOSITIVELENGTHMEASURE", 600.0)
    checker = Checker(model)
    is_external = Concept("200 Wall", "IfcWall", "Pset_WallCommon.IsExternal", IS_EXTERNAL, None, find_required(IS_EXTERNAL))
    tile_width = Concept("200 Wall", "IfcWall", "Pset_CoveringCeiling.TileWidth", TILE_WIDTH, None, find_required(TILE_WIDTH))
    predefined = Concept("200 Wall", "IfcWall", "Predefined type", PREDEFINED_TYPE, None, find_required(PREDEFINED_TYPE))
    assert checker.value_of(is_external, typed_wall) == "yes (IfcLabel)"
    assert checker.value_of(is_external, lone_wall) is None
    assert checker.value_of(tile_width, lone_wall) == "600"
    assert checker.value_of(predefined, typed_wall) == "SOLIDWALL"
    assert checker.value_of(predefined, lone_wall) is None



def test_status_missing_or_failed(tmp_path):
    model, typed_wall, lone_wall = make_model()
    other_wall = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcWall", name="AVA.003")
    write_property(model, other_wall, "Pset_WallCommon", "IsExternal", "IFCLABEL", "yes")
    concept = Concept("200 Wall", "IfcWall", "Pset_WallCommon.IsExternal", IS_EXTERNAL, None, find_required(IS_EXTERNAL))
    results = validate(model, [concept])
    statuses = {r.element.Name: r.status for r in results}
    # Inherited TRUE passes, no value is missing, a value of the wrong type fails
    assert statuses == {"AVA.001": "passed", "AVA.002": "missing", "AVA.003": "failed"}

    path = tmp_path / "report.csv"
    Validation_Simba.export_csv(results, path)
    rows = {row["Name"]: (row["Value"], row["Validation"]) for row in csv.DictReader(open(path, encoding="utf-8-sig"))}
    assert rows == {"AVA.001": ("TRUE", "PASSED"), "AVA.002": ("", "MISSING"), "AVA.003": ("yes (IfcLabel)", "FAILED")}


def test_load_allowed_values(tmp_path):
    path = tmp_path / "allowed_values.csv"
    path.write_text(
        "Property,Allowed values,Source\n"
        "# NONS_Process.DuplicateOwnedBy,ARK;RIB,commented out\n"
        "NONS_Process.ProcessStatus, A ; B ,\n"
        "NONS_Thermal.ThermalTransmittanceReq,\"re:\\d+([.,]\\d+)?\",\n"
        "NONS_Process.ConstructedStatus,,empty list is ignored\n",
        encoding="utf-8")
    allowed = load_allowed_values(path)
    assert allowed[("NONS_Process", "ProcessStatus")] == ["A", "B"]
    assert allowed[("NONS_Thermal", "ThermalTransmittanceReq")][0].pattern == r"\d+([.,]\d+)?"
    assert len(allowed) == 2
    assert load_allowed_values(tmp_path / "missing.csv") == {}


def test_allowed_values_checked():
    model, typed_wall, lone_wall = make_model()
    write_property(model, typed_wall, "NONS_Process", "ProcessStatus", "IFCLABEL", "Godkjent")
    write_property(model, lone_wall, "NONS_Process", "ProcessStatus", "IFCLABEL", "350")
    allowed = {("NONS_Process", "ProcessStatus"): ["Godkjent", "Til godkjenning"]}
    assert Checker(model, allowed).evaluate(PROCESS_STATUS, typed_wall) == True
    assert Checker(model, allowed).evaluate(PROCESS_STATUS, lone_wall) == False
    # Without a list any filled-in text passes
    assert Checker(model).evaluate(PROCESS_STATUS, lone_wall) == True


def test_measure_range_checked():
    model, typed_wall, lone_wall = make_model()
    write_property(model, typed_wall, "Pset_CoveringCeiling", "TileWidth", "IFCPOSITIVELENGTHMEASURE", 600.0)
    write_property(model, lone_wall, "Pset_CoveringCeiling", "TileWidth", "IFCPOSITIVELENGTHMEASURE", -5.0)
    checker = Checker(model)
    assert checker.evaluate(TILE_WIDTH, typed_wall) == True
    assert checker.evaluate(TILE_WIDTH, lone_wall) == False


def test_find_rule_files(tmp_path):
    rules = tmp_path / "Simba Rules"
    rules.mkdir()
    (rules / "b3-1.mvdxml").write_text("")
    (tmp_path / "b3-1.mvdxml").write_text("")
    (tmp_path / "b5-1.mvdxml").write_text("")
    (tmp_path / "notes.txt").write_text("")
    found = find_rule_files([rules, tmp_path, tmp_path / "missing"])
    assert [p.name for p in found] == ["b3-1.mvdxml", "b5-1.mvdxml"]
    assert found[0].parent == rules


def test_get_arguments():
    assert get_arguments(["project.py", "model.ifc"]) == ("model.ifc", None)
    assert get_arguments(["project.py", "MODEL.IFC", "rules.mvdxml"])[1].name == "rules.mvdxml"
    for argv in (["project.py"], ["project.py", "model.txt"], ["project.py", "model.ifc", "rules.xml"], ["project.py", "a.ifc", "b", "c"]):
        with pytest.raises(SystemExit):
            get_arguments(argv)
