# PACE and HeliosAdapt — experimental artifacts

This repository contains the experimental code, infrastructure automation, workload configurations, and analysis tools accompanying the **PACE** and **HeliosAdapt** research papers. These contributions also form **Chapter 6 (PACE)** and **Chapter 7 (HeliosAdapt)** of Ammar Kazem's doctoral thesis, _Edge Computing for Environmental Observatories: Processing Environmental Data under a Finite and Variable Solar Energy Budget_ (Université de Rennes, 2026).

**Using this code in research? Please cite the relevant PACE and/or HeliosAdapt paper.** Cite both when using both contributions, and the thesis when drawing on its extended presentation. See [publications and citation](docs/REPRODUCIBILITY.md#publications-and-citation) for the papers first, followed by the thesis, or use the ready-to-copy [BibTeX entries](CITATION.bib).

The experiments use a Turing Pi edge cluster orchestrated with k0s. The repository includes Ansible bootstrap roles, Prometheus monitoring, trace-driven workloads, solar/battery models, scheduling components, and scale-up timing analysis.

**Status: research prototype, not a production distribution or a fully reproducible artifact yet.** Some experiments still embed lab-specific endpoints, hardware mappings, and paths. See [known limitations](docs/REPRODUCIBILITY.md) before attempting a run. Despite legacy `k3s` names, `site.yml` installs **k0s**.

## Relationship to the thesis

The two papers, also developed in the thesis chapters below, address complementary layers of energy-aware edge computing:

- **Chapter 6 — PACE: Dynamic Node Consolidation for Near Energy-Proportional Edge Clusters.** PACE (**Proportional Adaptive Cluster for Edge**) adapts the number of powered-on nodes to measured resource usage. Its experiments study infrastructure-level energy savings and workload performance through node consolidation, power control, and rebalancing.
- **Chapter 7 — HeliosAdapt: Service Quality Adaptation for Solar-Powered Edge Clusters.** HeliosAdapt adapts application service modes to a finite, variable solar energy budget. It combines battery state, solar forecasts, and mixed-integer optimization to balance quality of service and energy availability, including a survival mode under harsh energy constraints.

**HeliosAdapt runs on top of PACE:** PACE manages the active infrastructure, while HeliosAdapt manages application-level service quality. The chapters therefore share the physical testbed, monitoring, workload execution, and measurement infrastructure.

| Thesis contribution                    | Relevant repository components                                                                                                                                                                                                    |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **PACE — Chapter 6**                   | `roles/node_control/`; long-term and event-driven workload orchestration in `long_term_simulation.yml` and `event_driven_simulation.yml`; scale-up timing experiments and analysis in `playbooks/`                                |
| **HeliosAdapt — Chapter 7**            | `roles/lp_schedules/`, `roles/lp_schedules_operator/`, `roles/crd_creator/`, `roles/app_energy_estimator/`, `roles/solar_energy_model/`, `roles/time_proxy/`, and `roles/qos_monitoring/`; experimental integration in `test.yml` |
| **Shared experimental infrastructure** | `site.yml`, `monitoring.yml`, application deployment, workload injection, and system-measurement roles                                                                                                                            |

This is a map of the experimental implementation, **not a claim that each playbook reproduces a specific thesis figure or that all published results can be regenerated from the current checkout**. Filenames retain historical lab/scenario names, and some integration playbooks contain baseline settings or disabled prerequisites. Consult the [papers and thesis](docs/REPRODUCIBILITY.md#publications-and-citation) for the methodology and reported results, and the [reproducibility notes](docs/REPRODUCIBILITY.md) for the current execution limitations.

## What is included?

| Path                                                      | Purpose                                                |
| --------------------------------------------------------- | ------------------------------------------------------ |
| `site.yml`, `roles/k0s_*`                                 | Bootstrap a controller/worker and worker nodes         |
| `monitoring.yml`, `roles/monitoring`                      | Prometheus installation and experiment scraping        |
| `long_term_simulation.yml`, `event_driven_simulation.yml` | Trace-driven experiment orchestration                  |
| `nepal_scenario_simulation.yml`                           | Solar/battery and degradation scenario orchestration   |
| `roles/node_control`                                      | Node draining, scheduling, and Turing Pi power control |
| `roles/time_proxy`, `roles/solar_energy_model`            | Simulated time, InfluxDB measurements, energy models   |
| `roles/lp_schedules*`, `roles/crd_creator`                | Optimization and custom-resource scheduling            |
| `playbooks/`                                              | Scale-up timing experiment and plotting tools          |
| `roles/workloads_injector/files/`                         | Workload configurations and trace data                 |

## Requirements

- An Ansible control machine with Python 3.11 and SSH access to the target hosts. Python 3.11 is the CI baseline; use newer Python only when supported by your Ansible version.
- Linux target nodes with Python, systemd, and privilege escalation. The experiment setup assumes two RK1 modules and one CM4 on a Turing Pi; bootstrap download support alone does not establish full support for other hardware.
- Internet access for packages, k0s/Helm downloads, charts, container images, and solar data queries.
- For experiments: Docker Engine and Compose v2, a provisioned InfluxDB instance, a VISA-compatible measurement instrument/backend, BMC SSH access, and the lab-specific configuration described below. These are **not** all provisioned by `site.yml`.

## Controller setup

Run from the repository root:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
ansible-galaxy collection install -r requirements.yml
cp inventory-sample.yml inventory.yml
```

Edit the ignored `inventory.yml`: replace the documentation-only addresses, SSH user, per-node `node_labels`, filesystem paths, and experiment settings. `ansible_user` is the remote account; `local_user` identifies the local experiment account. Store secrets outside Git, preferably with Ansible Vault. Neither dependency manifest is a lockfile.

Validate without contacting or modifying the cluster:

```sh
python scripts/validate.py
python -m unittest discover -s tests -v
ansible-inventory -i inventory.yml --graph
ansible-playbook -i inventory.yml site.yml --syntax-check
ansible-playbook -i inventory.yml monitoring.yml --syntax-check
```

## Cluster bootstrap

**These commands change remote machines.** Review the roles and use dedicated lab nodes, not an existing production cluster.

```sh
ansible turing_pi_cluster -i inventory.yml -m ansible.builtin.ping
ansible-playbook -i inventory.yml site.yml
```

Use `--ask-become-pass` if your lab requires a sudo password. Bootstrap fetches an administrator kubeconfig to the ignored `artifacts/` directory. Keep it private. Its API address may need adjustment for access from the control machine; the address-rewrite task is currently disabled. Inspect it locally without sharing the embedded keys.

After verifying the address and certificate SANs:

```sh
kubectl --kubeconfig artifacts/k0s-kubeconfig.yml get nodes -o wide
```

## Monitoring and experiments

**Monitoring installation deletes and recreates the monitoring namespace**, losing any resources/data stored there:

```sh
ansible-playbook -i inventory.yml monitoring.yml
```

For local experiment services and analysis, also install:

```sh
python -m pip install -r requirements.txt
```

Read [reproducibility notes](docs/REPRODUCIBILITY.md) before invoking scenario playbooks. They are entry points for the original lab, **not verified clean-checkout reproduction commands**. They can delete workloads, replace RabbitMQ data, drain nodes, and physically power modules off. `test.yml` is also an experiment playbook, **not a safe test suite**. `reset.yml` destroys cluster state, changes networking, and reboots nodes.

A hardware-free analysis of the included timing CSV is available after installing the analysis dependencies:

```sh
python playbooks/plot_timeline.py \
  --csv playbooks/cluster_state_log_3.csv \
  --mode mean-std \
  --output results/timing.png \
  --stats-output results/timing.csv
```

Create `results/` first. Existing CSVs and plots are historical outputs, not evidence that the current checkout has been reproduced. Review their privacy and redistribution status before publication.

## Security and reuse

- [Security guidance](SECURITY.md) — credential handling and deployment boundaries.
- [Third-party material](THIRD_PARTY_NOTICES.md) — existing notices and provenance review.
- [Publications and citation](docs/REPRODUCIBILITY.md#publications-and-citation) — PACE, HeliosAdapt, and the thesis.

## License

Copyright (c) 2026 Ammar Kazem.

Original project source code and associated software documentation are licensed under the **GNU General Public License, version 3 or (at your option) any later version** ([GPL-3.0-or-later](LICENSE)). This software is free software: you may redistribute and/or modify it under those terms. It is distributed without any warranty, including the implied warranties of merchantability or fitness for a particular purpose.

GPL permits use, including commercial use. If you distribute covered modified versions or binaries, you must comply with its copyleft, notice-preservation, and corresponding-source requirements; see the full license for details.

**Research attribution:** please cite the relevant papers and, where applicable, the thesis using the references above. Academic citation is a scholarly request, not an additional license restriction; GPL does not require users to cite papers in publications.

Third-party code and dependencies retain their own licenses and notices. The project license does not relicense third-party traces or datasets, or the separately cited papers and thesis; see [third-party material](THIRD_PARTY_NOTICES.md) for provenance and redistribution considerations.

The paper references are provided above; a persistent identifier for the software artifact itself has not been assigned.
