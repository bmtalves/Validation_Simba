# SIMBA 2.1 Validation Tool

Checks an IFC model against Statsbygg's SIMBA 2.1 requirements for architects (ARK) and reports what is missing or wrong.

The requirements are read from Statsbygg's mvdXML files in the `Simba Rules` folder. The tool has no property names written into it, so a new release of the mvdXML files can be dropped in without changing the code.

## What it does

1. **Validates** the model against one delivery phase: B3.1 Skisseprosjekt, B3.2 Forprosjekt, B4.1 Detaljprosjekt or B5.1 Ferdigstillelse.
2. **Reports** the results as a CSV file, an HTML dashboard, or both.
3. **Copies old status values:** if the model still uses `NOSSB_Process.DesignedStatus`, the tool offers to copy it into the new `NONS_Process.ProcessStatus`, and then saves the result as a new IFC file. The original file is never changed.

The tool doesn't let you edit other values. Missing or wrong information has to be fixed in the authoring tool, using the report to find it.

## Installation

```bash
git clone https://github.com/bmtalves/Validation_Simba.git
cd Validation_Simba
pip install ifcopenshell pytest
```

## Requirements

- Python 3.10 or newer
- [IfcOpenShell](https://ifcopenshell.org/) (tested with 0.7.10)
- pytest (only for running the tests)

## Usage

Run from this folder:

```bash
python Validation_Simba.py model.ifc
```

The tool lists every phase found in the mvdXML files and asks which to check against:

```
Exchange requirements:
  1. B3.1.1 Outline conceptual design (B3.1 Skisseprosjekt) .1
  ...
  10. B5.1.1 Handover (B5.1 Ferdigstillelse) .1
Validate against (1-10):
```

To use one specific rules file instead of all four:

```bash
python Validation_Simba.py model.ifc rules.mvdxml
```

### The steps

| Step | Prompt | Notes |
|---|---|---|
| Old status values | `Copy DesignedStatus into ProcessStatus?` | Shown only if the model has `NOSSB_Process.DesignedStatus`. Objects that already have a ProcessStatus are skipped, and the old values are kept. |
| Summary | – | One `OK` / `FAIL` line per requirement with the number of failing objects. |
| Report | `Export a report? (csv/html dashboard/both/no)` | Files are named `validation <model> <phase>.csv` / `.html`. |
| Save | `Save as new file?` | Only shown if status values were copied. Default name is `<model>_updated.ifc`. |

You can answer prompts with the first letter (`y`, `n`, `c`, `h`, `b`).

## Allowed values

The Statsbygg mvdXML files only require text properties such as `ProcessStatus` or `FireRating` to be filled in. They don't say which values are allowed. You can add those lists yourself in `allowed_values.csv`:

```csv
Property,Allowed values,Source
NONS_Process.ProcessStatus,000;100;125;150;175;200;225;250;275;300;325;350;375;400;425;450;475;500;600,MMI codes
NONS_Process.DuplicateOwnedBy,ARK;RIB;RIV;RIE;LARK,Discipline codes
NONS_Thermal.ThermalTransmittanceReq,"re:\d+([.,]\d+)?",U-value as a number
```

- **Format:** one property per line as `PsetName.PropertyName`, with allowed values separated by `;`.
- **Patterns:** `re:` starts a pattern (a Python regular expression) that the whole value must match. Put a cell in double quotes if it contains a comma.
- **Comments:** lines starting with `#` are ignored.
- **Shipped lists:** `NONS_Process.ProcessStatus` is limited to MMI codes, following SIMBA 2.1 Veiledning B.3.1. A primary code is a whole hundred (e.g. 200 Ferdig konsept, 300 Underlag for detaljering, 400 Produksjonsunderlag). A secondary code adds x25 etablert for kontroll, x50 kontrollert or x75 akseptert, e.g. `250`. The other lines are commented-out examples.
- **Validation:** a value that isn't on the list fails the requirement, just like a missing value. This applies whether the value is on the object or on its type.

Measures are also checked against IFC's own limits: `IFCPOSITIVELENGTHMEASURE` must be above 0 and `IFCNORMALISEDRATIOMEASURE` between 0 and 1.

## The dashboard

`validation <model> <phase>.html` is a single file that works offline and can be shared as it is. It shows:

- **Checks passed:** the overall pass rate.
- **Summary tiles:** failing requirements, objects with issues, object groups with failures, and property sets with failures.
- **By object group** and **By property set:** the share of checks passed, worst first. Hover a bar for details.
- **Requirements:** a table you can filter by status and group, search, and sort.
- **Objects with most issues:** each object's class, name, GlobalId and failing requirements, with filters for class and name/GlobalId.

It follows the system light/dark mode and has its own theme toggle.

## What is checked

Each mvdXML file has *concept roots* (object groups such as `200 Wall`), each with *concepts* (requirements). A requirement is checked when it is marked **mandatory** for the chosen phase.

| Rule | Example |
|---|---|
| Property exists with the right value type | `Pset_WallCommon.IsExternal` as `IFCBOOLEAN` |
| Property value is allowed | values listed in `allowed_values.csv`; measures within IFC's limits |
| Attribute is filled in | `Name`, `Description`, `LongName`, `LandTitleNumber` |
| Predefined type is set | `PredefinedType` on the object or its type |
| Object has a type | `IfcWallType` assigned |
| Georeferencing | `IfcMapConversion` with an `EPSG:` CRS on the Body context |

### How the rules are interpreted

- **Inherited properties:** a property on the object's type counts, unless the object has the same property itself. In that case the object's own value is the one checked, so a wrong type or an empty value on the object fails even if the type is correct. This follows the `nor` rules in the mvdXML.
- **Example values:** some Statsbygg rules contain example values, e.g. project name `'1234567'` or building name `'Building name'`. For free-text attributes these are treated as "must be filled in", not "must equal this text".
- **`NOTDEFINED`:** a PredefinedType of `NOTDEFINED` counts as not set, so the type's PredefinedType is used, as IFC intends.
- **Subgroups:** groups such as *Covering Ceiling*, *Covering Flooring* and *Transport Element Elevator* only apply to objects with that PredefinedType. An object can be checked under both the general group and its subgroup.
- **Site requirements:** the rules only apply the site checks when the model is georeferenced with `EPSG:5555`, so models without it get no site checks.
- **Model schema:** SIMBA 2.1 requires IFC4. Other schemas are still validated, with a warning.

## Files

```
Validation_Simba/
├── Validation_Simba.py           the tool
├── dashboard_template.html       layout of the HTML dashboard (must stay next to Validation_Simba.py)
├── allowed_values.csv            your lists of allowed values (optional)
├── test_validation_simba.py      tests
├── README.md
└── Simba Rules/
    ├── statsbygg-template-simba-2-1-…_b3-1_architect-ark.mvdxml   B3.1 Skisseprosjekt
    ├── statsbygg-template-simba-2-1-…_b3-2_architect-ark.mvdxml   B3.2 Forprosjekt
    ├── statsbygg-template-simba-2-1-…_b4-1_architect-ark.mvdxml   B4.1 Detaljprosjekt
    └── statsbygg-template-simba-2-1-…_b5-1_architect-ark.mvdxml   B5.1 Ferdigstillelse
```

Every `*.mvdxml` file in `Simba Rules` (or next to `Validation_Simba.py`) is picked up automatically.

## Tests

```bash
python -m pytest test_validation_simba.py
```

The tests build small IFC4 models in memory and cover:

- reading the rules
- property inheritance from types
- wrong value types
- PredefinedType
- copying old status values
- the dashboard data
- allowed values and measure limits
- finding the rules files

## Limitations

- **Allowed values have to be added by hand:** Statsbygg's mvdXML files contain no value lists, so text properties are only checked against the lists you put in `allowed_values.csv`.
- **Old date not copied:** `NOSSB_Process.DesignedStatusDate` has no equivalent in SIMBA 2.1.
- **Rule syntax:** only the mvdXML rule syntax used in the Statsbygg files is supported, not the full mvdXML standard.
