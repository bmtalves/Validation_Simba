import pytest
import ifcopenshell
import ifcopenshell.api

from Validation_Simba import Atom, Checker, find_fix, get_arguments, make_label, parse_parameters, parse_value, apply_fix, find_old_status
from Validation_Simba import Concept, validate, dashboard_data, find_rule_files


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


def test_find_fix_and_label():
    fix = find_fix(IS_EXTERNAL)
    assert fix == ("property", "Pset_WallCommon", "IsExternal", "IFCBOOLEAN")
    assert make_label("e2db IsExternal : 9f80 Pset_WallCommon : 200 Wall", fix) == "Pset_WallCommon.IsExternal"
    assert find_fix(("rule", parse_parameters("LongName[Exists]=TRUE"))) == ("attribute", "LongName")
    assert find_fix(PREDEFINED_TYPE) is None
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


def test_apply_fix():
    model, typed_wall, lone_wall = make_model()
    apply_fix(model, lone_wall, ("property", "Pset_WallCommon", "IsExternal", "IFCBOOLEAN"), True)
    apply_fix(model, lone_wall, ("attribute", "Description"), "Yttervegg")
    assert Checker(model).evaluate(IS_EXTERNAL, lone_wall) == True
    assert lone_wall.Description == "Yttervegg"


def test_find_old_status():
    model, typed_wall, lone_wall = make_model()
    apply_fix(model, typed_wall, ("property", "NOSSB_Process", "DesignedStatus", "IFCLABEL"), "400")
    apply_fix(model, lone_wall, ("property", "NOSSB_Process", "DesignedStatus", "IFCLABEL"), "300")
    apply_fix(model, lone_wall, ("property", "NONS_Process", "ProcessStatus", "IFCLABEL"), "Godkjent")
    # lone_wall already has a ProcessStatus, so it must not be overwritten
    assert find_old_status(model) == [(typed_wall, "400")]


def test_dashboard_data():
    model, typed_wall, lone_wall = make_model()
    concept = Concept("200 Wall", "IfcWall", "Pset_WallCommon.IsExternal", IS_EXTERNAL, None, find_fix(IS_EXTERNAL))
    results = validate(model, [concept])
    data = dashboard_data(results, model, "folder/model.ifc", "B5.1.1 Handover", passed_before=0)
    assert data["model"] == "model.ifc"
    assert data["before"] == {"passed": 0}
    assert data["requirements"] == [{"group": "200 Wall", "label": "Pset_WallCommon.IsExternal", "pset": "Pset_WallCommon",
                                     "type": "IFCBOOLEAN", "fixable": True, "passed": 1, "failed": 1}]
    failed = {o["name"]: o["failed"] for o in data["objects"]}
    assert failed == {"AVA.001": [], "AVA.002": ["Pset_WallCommon.IsExternal"]}


def test_parse_value():
    assert parse_value("yes", "IFCBOOLEAN") == True
    assert parse_value("False", "IFCBOOLEAN") == False
    assert parse_value("unknown", "IFCLOGICAL") == "UNKNOWN"
    assert parse_value("0,25", "IFCTHERMALTRANSMITTANCEMEASURE") == 0.25
    assert parse_value(" ARK ", "IFCLABEL") == "ARK"
    for text, ifc_type in [("maybe", "IFCBOOLEAN"), ("abc", "IFCAREAMEASURE"), ("0", "IFCPOSITIVELENGTHMEASURE"),
                           ("1.5", "IFCNORMALISEDRATIOMEASURE"), ("", "IFCLABEL")]:
        with pytest.raises(ValueError):
            parse_value(text, ifc_type)


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
