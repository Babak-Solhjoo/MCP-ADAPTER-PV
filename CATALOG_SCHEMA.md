# Catalog schema

Every supported application has a folder `mcp_adapter/catalogs/<software_id>/` containing:

* `meta.json` – one object describing the application and how to automate it.
* one or more chunk files `NN_<slug>.json` (e.g. `03_linear_algebra.json`) – each holds **one category** and its tools.
  Keep each chunk file at about 40 tools and **never more than 50** (enforced by tests) so files stay reviewable.

Software ids: `matlab`, `mathematica`, `comsol`, `photoshop`, `orcad`, `altium`, `proteus`, `vivado`,
`autocad`, `hfss`, `simulink`, `drawio`, `feko`, `eagle`.

## `meta.json`

```json
{
  "id": "vivado",
  "name": "AMD Vivado Design Suite",
  "vendor": "AMD (Xilinx)",
  "version_reference": "2024.2",
  "homepage": "https://www.amd.com/en/products/software/adaptive-socs-and-fpgas/vivado.html",
  "docs_root": "https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands",
  "description": "One paragraph: what the software is used for.",
  "automation": {
    "summary": "How an external program drives this application (CLI batch mode, scripting language, COM/Java/Python API).",
    "cli": [
      {"command": "vivado -mode batch -source script.tcl", "description": "Run a Tcl script headless and exit."}
    ],
    "apis": [
      {"name": "Tcl", "description": "Built-in Tcl interpreter; every GUI action has a Tcl equivalent.", "example": "open_project my.xpr\nlaunch_runs impl_1 -to_step write_bitstream"}
    ],
    "file_formats": [
      {"extension": ".xpr", "description": "Vivado project file"}
    ]
  }
}
```

## Chunk file

```json
{
  "category": "Synthesis & Implementation",
  "description": "One or two sentences about this category.",
  "tools": [
    {
      "name": "launch_runs",
      "kind": "tcl-command",
      "description": "Launch synthesis or implementation runs (2–4 sentences: what it does, when to use it, key options).",
      "usage": "launch_runs <run_names> [-jobs N] [-to_step write_bitstream]",
      "parameters": [
        {"name": "-jobs", "description": "Number of parallel jobs."}
      ],
      "example": "launch_runs impl_1 -to_step write_bitstream -jobs 8\nwait_on_run impl_1",
      "notes": "Optional pitfalls, related tools, performance tips.",
      "docs": "https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/launch_runs"
    }
  ]
}
```

Field rules:

| field | required | notes |
|---|---|---|
| `name` | yes | exact identifier as used in the application (function name, command, menu path, block name) |
| `kind` | yes | one of: `function`, `command`, `tcl-command`, `menu`, `tool`, `panel`, `block`, `api`, `script-object`, `system-variable`, `filter`, `adjustment`, `physics-interface`, `study`, `mesh`, `boundary`, `excitation`, `shortcut`, `library`, `format`, `solver`, `result`, `reference`, `example` (a ready-made model or project that ships with the application) |
| `description` | yes | plain English, 2–4 sentences, states what it does and when to use it |
| `usage` | yes | syntax / how to invoke (code signature, menu path, keyboard shortcut, or Tcl form) |
| `parameters` | no | list of `{name, description}` for the important arguments/options |
| `example` | yes | a short, realistic, runnable example (code or step list) |
| `notes` | no | pitfalls, related tools, alternatives |
| `docs` | no | official documentation URL when known; omit if unsure rather than inventing |

Guidelines:

* Prefer official documentation as the source of truth; use `py scripts/tavily_search.py "<query>"` to look things up.
* Never invent functions. If unsure whether something exists, look it up or leave it out.
* Cover the breadth of the product: core features **and** the major toolboxes/modules/add-ons.
* Group by the product's own documentation structure where possible.
* All JSON must be valid UTF-8 and parse with `json.load`.
