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
| Summary | – | One `OK` / `FAIL` line per requirement with the number of failed and missing objects. |
| Report | `Export a report? (csv/html dashboard/both/no)` | Files are named `validation <model> <phase>.csv` / `.html`. The CSV has one row per object and requirement, with the value found and its status. |
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

## Statuses

Every check gets one of three statuses:

| Status | Meaning |
|---|---|
| **Passed** | The value is there and valid. |
| **Failed** | There is a value, but it is wrong: the wrong value type (e.g. text instead of a boolean), not in `allowed_values.csv`, or outside IFC's limits. |
| **Missing** | There is no value at all: the property, attribute, type or georeferencing is not in the model. |

The CSV's `Validation` column shows `PASSED`, `FAILED` or `MISSING`. The dashboard shows them in green, red and yellow, each with its own icon.

## The dashboard

`validation <model> <phase>.html` is a single file that can be shared as it is. Everything except the 3D model works offline. It shows:

- **Checks passed:** the overall pass rate, with a donut chart and legend showing how many checks passed, failed and are missing. Hover a slice for its count and share.
- **Summary tiles:** failing requirements, objects with issues, object groups with failures, and property sets with failures.
- **By object group** and **By property set:** the share of checks passed, worst first. Hover a bar for details.
- **3D model:** drop the IFC file onto the card, or click *Choose IFC file*, to see the model coloured by result: green if all its checks pass, red if any fail, yellow if any are missing, grey for objects with no requirement. Drag to rotate, right-drag to pan and scroll to zoom.
  - The file is read and triangulated **in your browser** by [IFClite](https://github.com/louistrue/ifc-lite) (Rust/WebAssembly, MPL-2.0) and drawn with [three.js](https://threejs.org). It is never uploaded. Only the two libraries are downloaded, from cdn.jsdelivr.net, so this part needs an internet connection.
  - Use the same IFC file the report was made from (the updated file from `Save as new file?` works too). Objects are matched by their number in the file (`#123=`), and the dashboard checks a few GlobalIds to make sure it is the right model.
  - Click an object, or a row in the Values or Objects table, to see all its checks and values. The selected object is drawn in blue on top of everything, so walls and slabs can't hide it.
  - *Colour by* switches from the worst result to a single requirement. Picking a requirement or a value in the Values section does the same, and greys out objects with other values.
  - *Only not passing* hides everything that passes.
  - The project, site, building, storeys, spaces and openings are not drawn.
  - The model is built by several background workers at once (one per spare CPU core, fewer for very big files to save memory) and appears while it loads. Files over 100 MB use a lower detail level for curved shapes such as pipes and railings. A 300 MB model takes about 25 seconds. The dashboard itself stays small, since it holds no geometry.
- **Requirements:** a table with the failed and missing count per requirement, which you can filter by status and group, search, and sort. A requirement shows as *Missing* when all its problems are missing values, and *Failing* when at least one value is wrong.
- **Values:** pick a requirement (it opens on `NONS_Process.ProcessStatus`) to see every value in the model, how many objects have it, and whether it passes. Objects with no value are grouped as *Missing*, and values of the wrong type show the type, e.g. `yes (IfcLabel)`. The list can be filtered by Passing, Failing or Missing. Click a value to list only its objects. The list shows each object's status, value, class, name, GlobalId and group, and can be filtered, searched and sorted.
- **Objects:** each object's class, name, GlobalId and failing requirements. It shows the objects with the most issues first; switch to *All objects* to list every object checked. You can filter by class and search by name or GlobalId.

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
- **3D model:** whoever opens the dashboard also needs the IFC file, and an internet connection for the viewer libraries. Large models need a computer with plenty of memory (each worker reads its own copy of the file, so a 300 MB model can use 1.5 GB or more while loading).
- **Rule syntax:** only the mvdXML rule syntax used in the Statsbygg files is supported, not the full mvdXML standard.
