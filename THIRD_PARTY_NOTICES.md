# Third-party material and provenance

Original project source code and associated software documentation are covered by the [GNU General Public License, version 3 or later (GPL-3.0-or-later)](LICENSE). Third-party material retains its applicable upstream terms; the project license does not override those terms. This inventory is preliminary, not a legal opinion or a complete attribution audit.

## Bundled source with an existing notice

`roles/event_driven_simulation/event_driven_simulation_prereq/files/log_cpu_usage.py` contains the 2019 Scott C. Lowe copyright, redistribution conditions, and disclaimer. Retain the complete notice already present in that source file. Review its conditions when redistributing source or binary forms. Do not remove its attribution as part of personal-data cleanup.

## Traces, generated data, and figures

The repository includes workload CSVs under `roles/workloads_injector/files/`, solar-generation CSVs, timing/cluster logs under `playbooks/`, power-usage data, and generated plots. Their presence in Git is not proof of redistribution permission.

Before publication, record for each dataset family:

- Original source, retrieval date, dataset version, and upstream license/terms.
- Whether the data are original measurements, third-party traces, or derived outputs.
- Required citations, permissions, and transformation/generation steps.
- Any infrastructure identifiers or sensitive information removed from a public copy.

Solar model code accesses PVGIS data. Verify the applicable data/API attribution and redistribution terms rather than assuming a source-code license covers the resulting datasets. Dataset filenames or hashes alone are not adequate provenance.

## Dependencies and upstream automation

Python packages, Ansible collections, charts, container base images, and downloaded tools retain their upstream licenses. Review the provenance, applicable licenses, and GPL compatibility of imported/adapted Ansible roles and other source before redistribution; the single bundled notice above is not evidence that all remaining code is original. Preserve applicable notices and obtain coauthor/institutional permission as needed.
