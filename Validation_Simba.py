import sys
import csv
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.api.pset


MVD = "{http://buildingsmart-tech.org/mvd/XML/1.1}"
TOOL_FOLDER = Path(__file__).resolve().parent
# Rules files are read from "Simba Rules" and from the tool's own folder
RULES_FOLDERS = [TOOL_FOLDER / "Simba Rules", TOOL_FOLDER]
DASHBOARD_TEMPLATE = TOOL_FOLDER / "dashboard_template.html"
ALLOWED_VALUES_FILE = TOOL_FOLDER / "allowed_values.csv"

# Statsbygg fills free-text [Value] rules with example values ('1234567', 'Building name'),
# so for these attributes a [Value] rule only means "must be filled in"
PLACEHOLDER_ATTRIBUTES = {"Name", "LongName", "Description", "LandTitleNumber"}
REQUIRED_ATTRIBUTES = PLACEHOLDER_ATTRIBUTES | {"ObjectType"}
PROPERTY_KEYS = {"Set", "PropertyName", "Value"}
POSITIONING_KEYS = {"ContextType", "ContextIdentifier", "CRSName", "HasGlobalPosition"}

# One rule condition, e.g. T_Set[Value]='Pset_WallCommon' or CRSName[Value]=reg'^EPSG:(.*)'
ATOM = re.compile(r"(T_)?(\w+)\[(\w+)\]=(reg)?('[^']*'|\S+)|\b(AND|OR)\b")


@dataclass
class Atom:
    on_type: bool   # T_ prefix: check the type object instead of the element
    key: str        # attribute or Set/PropertyName/Value
    metric: str     # Exists, Value or Type
    value: str
    is_regex: bool = False


@dataclass(eq=False)
class Concept:
    group: str       # ConceptRoot name, e.g. "200 Wall"
    entity: str      # e.g. "IfcWall" (subclasses included)
    label: str       # e.g. "Pset_WallCommon.IsExternal"
    rules: tuple
    applicability: tuple | None
    required: tuple | None
    required_by: set = field(default_factory=set)


@dataclass
class Result:
    concept: Concept
    element: ifcopenshell.entity_instance
    passed: bool
    value: str | None = None   # what the model has for this requirement, as text; None if missing


def main():
    model_path, rules_path = get_arguments(sys.argv)
    model = load_model(model_path)
    if model.schema != "IFC4":
        print(f"Warning: Statsbygg requires IFC4, this model is {model.schema}")

    concepts, phase = choose_exchange_requirement([rules_path] if rules_path else find_rule_files(RULES_FOLDERS))
    allowed = load_allowed_values(ALLOWED_VALUES_FILE)
    if allowed:
        print(f"Checking allowed values for {len(allowed)} properties from {ALLOWED_VALUES_FILE.name}")
    changed = migrate_designed_status(model)

    results = validate(model, concepts, allowed)
    print_summary(results)

    report_name = f"validation {Path(model_path).stem} {phase.split()[0]}"
    report = ask_option("Export a report?", ["csv", "html dashboard", "both", "no"])
    if report in ("csv", "both"):
        export_csv(results, f"{report_name}.csv")
        print(f"File saved as {report_name}.csv")
    if report in ("html dashboard", "both"):
        export_dashboard(results, model, model_path, phase, f"{report_name}.html")

    if changed:
        save_file(model, model_path)
    else:
        print("Program finished.")


def get_arguments(argv):
    if len(argv) < 2:
        sys.exit("Usage: python Validation_Simba.py model.ifc [rules.mvdxml]")
    if len(argv) > 3:
        sys.exit("Too many arguments")
    if not argv[1].lower().endswith(".ifc"):
        sys.exit("Not an IFC file")
    if len(argv) == 3:
        if not argv[2].lower().endswith(".mvdxml"):
            sys.exit("Not an mvdXML file")
        return argv[1], Path(argv[2])
    return argv[1], None


def find_rule_files(folders):
    files = []
    for folder in folders:
        files += sorted(folder.glob("*.mvdxml"))
    # The same file in both folders should only be listed once
    unique = {}
    for path in files:
        unique.setdefault(path.name, path)
    return list(unique.values())


def load_model(path):
    try:
        return ifcopenshell.open(path)
    except (FileNotFoundError, OSError):
        sys.exit("IFC file doesn't exist")


# ---------- Reading the mvdXML ----------

def load_rules(path):
    """Return ({exchange requirement uuid: name}, [Concept])"""
    try:
        root = ET.parse(path).getroot()
    except (FileNotFoundError, ET.ParseError):
        sys.exit(f"Could not read rules file {path}")

    exchange_requirements = {er.get("uuid"): er.get("name") for er in root.iter(MVD + "ExchangeRequirement")}
    concepts = []
    for concept_root in root.iter(MVD + "ConceptRoot"):
        applicability = concept_root.find(f"{MVD}Applicability/{MVD}TemplateRules")
        if applicability is not None:
            applicability = parse_rules(applicability)

        for node in concept_root.iterfind(f"{MVD}Concepts/{MVD}Concept"):
            rules_node = node.find(MVD + "TemplateRules")
            if rules_node is None:
                continue
            rules = parse_rules(rules_node)
            required = find_required(rules)
            required_by = {
                r.get("exchangeRequirement")
                for r in node.iterfind(f"{MVD}Requirements/{MVD}Requirement")
                if r.get("requirement") == "mandatory"
            }
            concepts.append(Concept(
                concept_root.get("name"),
                concept_root.get("applicableRootEntity"),
                make_label(node.get("name"), required),
                rules,
                applicability,
                required,
                required_by,
            ))
    return exchange_requirements, concepts


def parse_rules(node):
    """TemplateRules -> (operator, [children]), TemplateRule -> ("rule", [AND-groups joined by OR])"""
    if node.tag == MVD + "TemplateRule":
        return ("rule", parse_parameters(node.get("Parameters", "")))
    children = [parse_rules(child) for child in node if child.tag in (MVD + "TemplateRules", MVD + "TemplateRule")]
    return (node.get("operator", "and").lower(), children)


def parse_parameters(text):
    groups = [[]]
    for on_type, key, metric, regex, value, connector in ATOM.findall(text):
        if connector == "OR":
            groups.append([])
        elif not connector:
            groups[-1].append(Atom(bool(on_type), key, metric, value.strip("'"), bool(regex)))
    return [group for group in groups if group]


def iter_groups(node):
    kind, children = node
    if kind == "rule":
        yield from children
    else:
        for child in children:
            yield from iter_groups(child)


def find_required(rules):
    """What this concept asks for: ("property", pset, name, type), ("attribute", name), or None for
    checks such as PredefinedType, type object and georeferencing"""
    for group in iter_groups(rules):
        own = {(a.key, a.metric): a.value for a in group if not a.on_type}
        if ("Set", "Value") in own and ("PropertyName", "Value") in own and ("Value", "Exists") in own:
            return ("property", own[("Set", "Value")], own[("PropertyName", "Value")], own.get(("Value", "Type"), "IFCLABEL"))
    for group in iter_groups(rules):
        for atom in group:
            if not atom.on_type and atom.key in REQUIRED_ATTRIBUTES and atom.metric in ("Exists", "Value"):
                return ("attribute", atom.key)
    return None


def make_label(name, required):
    if required and required[0] == "property":
        return f"{required[1]}.{required[2]}"
    # "0001-003 Name : 200 Wall : 01 Architectural (ARK)" -> "Name"
    return re.sub(r"^\S+\s+", "", (name or "").split(" : ")[0])


# ---------- Checking the model ----------

class Checker:
    def __init__(self, model, allowed=None):
        self.model = model
        self.allowed = allowed or {}
        self.positioning = positioning_facts(model)
        self._properties = {}
        self._applicable = {}

    def applicable_elements(self, concept):
        key = (concept.group, concept.entity)
        if key not in self._applicable:
            try:
                elements = self.model.by_type(concept.entity)
            except RuntimeError:
                elements = []  # entity doesn't exist in this schema
            if concept.applicability:
                elements = [e for e in elements if self.evaluate(concept.applicability, e)]
            self._applicable[key] = elements
        return self._applicable[key]

    def evaluate(self, node, element):
        kind, children = node
        if kind == "rule":
            return any(self.check_group(group, element) for group in children)
        results = (self.evaluate(child, element) for child in children)
        if kind == "or":
            return any(results)
        if kind == "nor":
            return not any(results)
        return all(results)

    def check_group(self, atoms, element):
        property_atoms = {False: [], True: []}
        positioning_atoms = []
        for atom in atoms:
            if atom.key in PROPERTY_KEYS:
                property_atoms[atom.on_type].append(atom)
            elif atom.key in POSITIONING_KEYS:
                positioning_atoms.append(atom)
            elif not self.check_attribute(atom, element):
                return False

        for on_type, atoms_for_side in property_atoms.items():
            if atoms_for_side and not self.check_property(atoms_for_side, element, on_type):
                return False

        if positioning_atoms:
            return any(all(value_matches(facts[a.key], a) for a in positioning_atoms) for facts in self.positioning)
        return True

    def check_attribute(self, atom, element):
        if atom.key == "TypeObject":
            value = self.target(element, True)
        else:
            value = attribute_value(self.target(element, atom.on_type), atom.key)
        if atom.metric == "Value" and atom.key in PLACEHOLDER_ATTRIBUTES:
            return value is not None
        return value_matches(value, atom)

    def check_property(self, atoms, element, on_type):
        target = self.target(element, on_type)
        if target is None:
            return False
        names = {a.key: a.value for a in atoms if a.metric == "Value" and a.key in ("Set", "PropertyName")}
        properties = self.properties(target).get(names.get("Set"), {})
        if names.get("PropertyName") not in properties:
            return False

        nominal = properties[names["PropertyName"]]
        for atom in atoms:
            if atom.key != "Value":
                continue
            if atom.metric == "Exists" and (nominal is not None) != (atom.value.upper() == "TRUE"):
                return False
            if atom.metric == "Type" and (nominal is None or nominal.is_a().upper() != atom.value.upper()):
                return False
            if atom.metric == "Value" and (nominal is None or not value_matches(nominal.wrappedValue, atom)):
                return False

        # Where the rule requires a value, it must also be a valid one
        requires_value = any(a.key == "Value" and a.metric == "Exists" and a.value.upper() == "TRUE" for a in atoms)
        if requires_value and nominal is not None:
            if not in_range(nominal):
                return False
            allowed = self.allowed.get((names["Set"], names["PropertyName"]))
            if allowed and not is_allowed(nominal.wrappedValue, allowed):
                return False
        return True

    def value_of(self, concept, element):
        """The value the requirement looks at, as text, or None if there is none"""
        required = concept.required
        if required and required[0] == "property":
            _, pset, prop, ifc_type = required
            # The element's own value wins over its type's, as in the rules
            for target in (element, self.target(element, True)):
                properties = self.properties(target).get(pset, {}) if target is not None else {}
                if prop in properties:
                    nominal = properties[prop]
                    if nominal is None:
                        return None
                    text = display_value(nominal.wrappedValue)
                    if nominal.is_a().upper() != ifc_type.upper():
                        text += f" ({nominal.is_a()})"
                    return text
            return None

        keys = {atom.key for group in iter_groups(concept.rules) for atom in group}
        if required:
            return display_value(attribute_value(element, required[1]))
        if "PredefinedType" in keys:
            value = attribute_value(element, "PredefinedType")
            object_type = self.target(element, True)
            if value is None:
                value = attribute_value(object_type, "PredefinedType")
            if value is None and object_type is not None and "TypeObject" in keys:
                # The rule also accepts just having a type object
                value = f"type {object_type.Name or object_type.is_a()}"
            return display_value(value)
        if "TypeObject" in keys:
            object_type = self.target(element, True)
            return display_value(object_type.Name or object_type.is_a()) if object_type else None
        if keys & POSITIONING_KEYS:
            return next((facts["CRSName"] for facts in self.positioning if facts["CRSName"]), None)
        return None

    def target(self, element, on_type):
        if not on_type:
            return element
        try:
            return ifcopenshell.util.element.get_type(element)
        except AttributeError:
            return None

    def properties(self, target):
        """{pset name: {property name: nominal value entity or None}}, read straight from the file"""
        if target.id() not in self._properties:
            if target.is_a("IfcTypeObject"):
                definitions = target.HasPropertySets or ()
            else:
                definitions = []
                for rel in getattr(target, "IsDefinedBy", ()):
                    if rel.is_a("IfcRelDefinesByProperties"):
                        definition = rel.RelatingPropertyDefinition
                        definitions += definition if isinstance(definition, tuple) else [definition]

            psets = {}
            for pset in definitions:
                if pset.is_a("IfcPropertySet"):
                    values = psets.setdefault(pset.Name, {})
                    for prop in pset.HasProperties:
                        values[prop.Name] = nominal_value(prop)
            self._properties[target.id()] = psets
        return self._properties[target.id()]


def nominal_value(prop):
    if prop.is_a("IfcPropertySingleValue"):
        value = prop.NominalValue
    elif prop.is_a("IfcPropertyEnumeratedValue") and prop.EnumerationValues:
        value = prop.EnumerationValues[0]
    else:
        return None
    if value is None or value.wrappedValue in (None, ""):
        return None
    return value


def attribute_value(target, key):
    if target is None:
        return None
    try:
        value = getattr(target, key)
    except AttributeError:
        return None
    # NOTDEFINED on an element means "use the type's PredefinedType"
    if value in ("", "NOTDEFINED"):
        return None
    return value


def display_value(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def value_matches(value, atom):
    if atom.metric == "Exists":
        return (value is not None) == (atom.value.upper() == "TRUE")
    if value is None:
        return False
    if atom.is_regex:
        return re.search(atom.value, str(value)) is not None
    return str(value) == atom.value


def positioning_facts(model):
    """One dict per representation context, used by the georeferencing rules"""
    facts = []
    for context in model.by_type("IfcGeometricRepresentationContext"):
        parent = getattr(context, "ParentContext", None)
        operations = getattr(context, "HasCoordinateOperation", None) or getattr(parent, "HasCoordinateOperation", None) or ()
        operation = operations[0] if operations else None
        crs = operation.TargetCRS if operation else None
        facts.append({
            "ContextType": context.ContextType,
            "ContextIdentifier": context.ContextIdentifier,
            "HasGlobalPosition": operation,
            "CRSName": crs.Name if crs else None,
        })
    return facts


def in_range(nominal):
    """IFC's own limits on measure types"""
    ifc_type = nominal.is_a().upper()
    value = nominal.wrappedValue
    if "POSITIVE" in ifc_type:
        return value > 0
    if "NORMALISEDRATIO" in ifc_type:
        return 0 <= value <= 1
    return True


def load_allowed_values(path):
    """{(pset, property): [allowed text or re.Pattern]} from allowed_values.csv; {} if there is no file"""
    allowed = {}
    if not path.exists():
        return allowed
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            name = (row.get("Property") or "").strip()
            if name.startswith("#") or "." not in name:
                continue
            values = []
            for value in (row.get("Allowed values") or "").split(";"):
                value = value.strip()
                if value.startswith("re:"):
                    try:
                        values.append(re.compile(value[3:]))
                    except re.error as error:
                        sys.exit(f"{path.name}: invalid pattern '{value[3:]}' for {name} ({error})")
                elif value:
                    values.append(value)
            if values:
                allowed[tuple(name.split(".", 1))] = values
    return allowed


def is_allowed(value, allowed):
    text = str(value)
    return any(a.fullmatch(text) if isinstance(a, re.Pattern) else text == a for a in allowed)


def validate(model, concepts, allowed=None):
    checker = Checker(model, allowed)
    results = []
    for concept in concepts:
        for element in checker.applicable_elements(concept):
            results.append(Result(concept, element, checker.evaluate(concept.rules, element), checker.value_of(concept, element)))
    return results


# ---------- Output ----------

def print_summary(results):
    totals = {}
    for result in results:
        passed, total = totals.get(result.concept, (0, 0))
        totals[result.concept] = (passed + result.passed, total + 1)

    print()
    for concept, (passed, total) in totals.items():
        status = "OK  " if passed == total else "FAIL"
        print(f"{status} {concept.group:<30} {concept.label:<50} {total - passed}/{total} failed")

    failed = sum(not r.passed for r in results)
    print(f"\n{len(results) - failed} of {len(results)} checks passed ({len(totals)} requirements on objects in the model)")


def export_csv(results, filename):
    with open(filename, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["GlobalId", "IfcType", "Name", "Group", "Requirement", "Value", "Validation"])
        writer.writeheader()
        for result in results:
            writer.writerow({
                "GlobalId": getattr(result.element, "GlobalId", ""),
                "IfcType": result.element.is_a(),
                "Name": getattr(result.element, "Name", ""),
                "Group": result.concept.group,
                "Requirement": result.concept.label,
                "Value": "" if result.value is None else result.value,
                "Validation": "PASSED" if result.passed else "FAILED",
            })


def dashboard_data(results, model, model_path, phase):
    requirements = {}
    objects = {}
    values = {}
    # One row per check, as indexes into the lists above to keep the file small:
    # [object, requirement, passed (1/0), value (-1 if missing)]
    checks = []
    for result in results:
        concept, element = result.concept, result.element
        required = concept.required
        is_property = required is not None and required[0] == "property"
        requirement = requirements.setdefault(concept, {
            "group": concept.group,
            "label": concept.label,
            "index": len(requirements),
            "pset": required[1] if is_property else None,
            "type": required[3] if is_property else None,
            "passed": 0,
            "failed": 0,
        })
        requirement["passed" if result.passed else "failed"] += 1

        obj = objects.setdefault(element.id(), {
            "id": getattr(element, "GlobalId", ""),
            "cls": element.is_a(),
            "name": getattr(element, "Name", None) or "",
            "index": len(objects),
            "total": 0,
            "failed": [],
        })
        obj["total"] += 1
        if not result.passed and concept.label not in obj["failed"]:
            obj["failed"].append(concept.label)

        value = -1 if result.value is None else values.setdefault(result.value, len(values))
        checks.append([obj["index"], requirement["index"], int(result.passed), value])

    return {
        "model": Path(model_path).name,
        "schema": model.schema,
        "phase": phase,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "requirements": list(requirements.values()),
        "objects": list(objects.values()),
        "values": list(values),
        "checks": checks,
    }


def export_dashboard(results, model, model_path, phase, filename):
    if not DASHBOARD_TEMPLATE.exists():
        print(f"Dashboard not saved: {DASHBOARD_TEMPLATE.name} must be in the same folder as this script")
        return
    data = json.dumps(dashboard_data(results, model, model_path, phase))
    # "</" would end the <script> block early if an object name contained "</script>"
    html = DASHBOARD_TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/null", data.replace("</", "<\\/"))
    Path(filename).write_text(html, encoding="utf-8")
    print(f"Dashboard saved as {filename}")


# ---------- Copying old status values ----------

def find_old_status(model):
    """Objects with the pre-SIMBA 2.1 NOSSB_Process.DesignedStatus but no NONS_Process.ProcessStatus.
    Returns [(element, value)]; values from the element's type count too"""
    found = []
    for element in model.by_type("IfcObject"):
        old = ifcopenshell.util.element.get_pset(element, "NOSSB_Process", "DesignedStatus")
        new = ifcopenshell.util.element.get_pset(element, "NONS_Process", "ProcessStatus")
        if old not in (None, "") and new in (None, ""):
            found.append((element, str(old)))
    return found


def migrate_designed_status(model):
    to_copy = find_old_status(model)
    if not to_copy:
        return False
    print(f"\n{len(to_copy)} object(s) have the old NOSSB_Process.DesignedStatus but no NONS_Process.ProcessStatus")
    if not ask_yes_no("Copy DesignedStatus into ProcessStatus?"):
        return False
    for element, value in to_copy:
        write_property(model, element, "NONS_Process", "ProcessStatus", "IFCLABEL", value)
    print(f"Copied on {len(to_copy)} object(s). The old NOSSB_Process values are kept.")
    return True


def write_property(model, element, pset_name, prop_name, ifc_type, value):
    # Always written on the element itself, so other objects sharing its type aren't changed
    pset = ifcopenshell.api.pset.add_pset(model, product=element, name=pset_name)
    ifcopenshell.api.pset.edit_pset(model, pset=pset, properties={prop_name: model.create_entity(ifc_type, value)})


# ---------- Prompts ----------

def choose_exchange_requirement(rule_files):
    """Pick a phase across all rule files. Returns (concepts required for it, phase name)"""
    options = []
    for path in rule_files:
        exchange_requirements, concepts = load_rules(path)
        for uuid, name in exchange_requirements.items():
            options.append((name, uuid, concepts))
    if not options:
        sys.exit("No mvdXML rules found")

    print("Exchange requirements:")
    name, uuid, concepts = options[ask_number("Validate against", [o[0] for o in options])]
    return [c for c in concepts if uuid in c.required_by], name


def ask_number(prompt, options):
    for i, option in enumerate(options, 1):
        print(f"  {i}. {option}")
    while True:
        answer = input(f"{prompt} (1-{len(options)}): ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1


def ask_option(prompt, options):
    while True:
        answer = input(f"{prompt} ({'/'.join(options)}) ").strip().lower()
        for option in options:
            if answer and option.startswith(answer):
                return option


def ask_yes_no(prompt):
    return ask_option(prompt, ["yes", "no"]) == "yes"


def save_file(model, model_path):
    if ask_yes_no("Save as new file?"):
        default = f"{Path(model_path).stem}_updated"
        new_name = input(f"Input new name [{default}]: ").strip().removesuffix(".ifc") or default
        model.write(f"{new_name}.ifc")
        print(f"File saved as {new_name}.ifc")
    print("Thank you for using the program")


if __name__ == "__main__":
    main()
