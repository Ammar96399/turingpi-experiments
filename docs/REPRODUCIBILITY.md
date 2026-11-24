# Reproducibility and lab configuration

This repository contains experimental artifacts for the **PACE** and **HeliosAdapt** papers listed below. The same contributions are presented in **Chapters 6 and 7**, respectively, of Ammar Kazem's doctoral thesis.

PACE provides infrastructure-level node consolidation; HeliosAdapt runs above it and adapts application service quality to the available solar energy and battery budget. Both contributions share the Turing Pi/k0s testbed and experimental infrastructure. See the [thesis-to-code map](../README.md#relationship-to-the-thesis) for the relevant components.

The code captures an evolving experimental lab, not a hermetic artifact. This document records known gaps; it is not a verified reproduction report or a figure-by-figure reproduction guide for either chapter.

## Publications and citation

**When using this code, its experimental setup, or its results in research, please cite the corresponding paper.** For work using both layers, please cite both PACE and HeliosAdapt. Please also cite the thesis when using its extended methodology, analysis, or discussion. Ready-to-copy entries for all three references are available in [CITATION.bib](../CITATION.bib).

This is a scholarly citation request, not an additional condition of the GPL software license.

1. **PACE.** Ammar Kazem, Guillaume Pierre, and Laurent Longuevergne. “PACE: Dynamic Node Consolidation for Near-Energy-Proportionality in Edge Clusters.” _Future Generation Computer Systems_, volume 186, January 2027. DOI: [10.1016/j.future.2026.108737](https://doi.org/10.1016/j.future.2026.108737).
2. **HeliosAdapt.** Ammar Kazem, Guillaume Pierre, and Laurent Longuevergne. “HeliosAdapt: Service Quality Adaptation for Solar-Powered Edge Clusters.” _Proceedings of the 14th IEEE International Conference on Cloud Engineering (IC2E)_, 2026. Manuscript: [HAL hal-05725218](https://inria.hal.science/hal-05725218).
3. **Thesis.** Ammar Kazem. _Edge Computing for Environmental Observatories: Processing Environmental Data under a Finite and Variable Solar Energy Budget_. Doctoral thesis, Université de Rennes, 2026. Chapter 6: PACE; Chapter 7: HeliosAdapt.

The paper metadata above follows the bibliography of the supplied thesis manuscript. Paper citations identify the research contributions, not an immutable version of this repository; record the software release or commit used for your experiments separately.

## Execution model

1. `site.yml` installs k0s on Linux controller/worker hosts.
2. `monitoring.yml` installs Prometheus and experiment scrape configuration. Existing Grafana templates do not imply Grafana is deployed.
3. Application roles build images on nodes and import them into k0s containerd. Jobs use local images rather than a fully managed public registry.
4. A local Docker Compose RabbitMQ instance carries trace-driven work to applications.
5. Node control uses Prometheus, Kubernetes administration, root SSH to the Turing Pi BMC, and `tpi` power commands.
6. Additional roles provide simulated time, InfluxDB-backed measurements, solar/battery models, PuLP/CBC optimization, and a Kopf scheduling operator.

The original hardware mapping includes two RK1 modules, one CM4, and a network VISA instrument measuring current at an assumed fixed 12 V supply. A Python `pyvisa` install alone does not supply an instrument backend. Helm installation and the custom descheduler image assume ARM64. Download support for amd64 does not imply the full experiment stack is portable.

## Required configuration review

| Area                      | Current limitation / action                                                                                                                                                                                                                                                                                                             |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Bootstrap inventory       | The sample now uses neutral aliases and documentation addresses. Set `node_labels` per host; `site.yml` no longer infers the RK1 worker from a personal hostname. Existing private inventories must explicitly label RK1 workers.                                                                                                       |
| Cluster access            | Fetched admin kubeconfig may retain a loopback API address. Configure a reachable endpoint covered by the API certificate's SANs.                                                                                                                                                                                                       |
| k0s configuration         | `k0s.yaml` and `roles/prereq/templates/k0s.yaml.j2` still contain original lab addresses; custom-config writing is disabled in the prereq role. They are not portable inputs to `site.yml`.                                                                                                                                             |
| Paths                     | Several roles/scripts still hardcode home directories, build paths, or Conda locations. Review `roles/deploy_applications_prereq/tasks/main.yml`, `roles/get_request`, `roles/get_limit`, `roles/solar_energy_model/tasks/main.yml`, `roles/workloads_injector/files/workload_injector.py`, and `playbooks/analyze_scale_up_timing.py`. |
| Services                  | InfluxDB is not provisioned. Set its URL/org/bucket and protected token, start time proxy before measurement clients, and confirm RabbitMQ/Prometheus reachability. Main scenario playbooks do not currently start the proxy themselves.                                                                                                |
| Time proxy                | Starting requires `influxdb_token`; the role defaults to reset mode unless `reset_time_proxy: false` is provided. Create `results_dir` before background-process redirection. Default binding is loopback; remote lab clients need an explicit, firewall-restricted override. Reset can delete existing experiment data.                |
| Network/hardware mappings | `roles/node_control/files/node_control.py`, workload injector/application code, the CRD creator/operator, and the timing demo embed endpoints, Kubernetes node names, BMC IDs, or service IPs. Inventory changes alone are insufficient.                                                                                                |
| Scenario routing          | Event-driven orchestration selects long-term observatory data in places. Nepal deployment/injection selections differ, and application code fixes its AMQP exchange. Reconcile these before comparing results.                                                                                                                          |
| Helper deployment         | Application deployment invokes remote `get_request.py`/`get_limit.py`; prerequisite setup does not reliably place these helpers at the expected paths.                                                                                                                                                                                  |
| Degradation stack         | `test.yml` comments out several prerequisite roles used downstream. Do not treat it as a complete clean-cluster bootstrap.                                                                                                                                                                                                              |
| Demo                      | `event_driven_simulation_demo.yml` references four missing `*_demo` roles. It is not runnable as checked in.                                                                                                                                                                                                                            |
| Monitoring                | Namespace replacement is destructive; persistence is disabled, and configured VictoriaMetrics remote-write does not provision its destination.                                                                                                                                                                                          |
| Repeat runs               | Background-process lifecycle/cleanup is incomplete. Node control may leave old controllers running; inspect and stop prior processes before another run.                                                                                                                                                                                |
| Legacy roles              | `roles/airgap` installs k3s, not k0s, and is not used by `site.yml`. Firewall settings also retain legacy group/network assumptions.                                                                                                                                                                                                    |

`one_minute` is wall-clock seconds per simulated minute. Keep experiment IDs fixed and unique in each private inventory/run configuration; timestamp expressions can be reevaluated across tasks. Record trace date ranges, conversion factors, solver settings, battery parameters, sampling intervals, and workload configuration with every result.

## Safe and destructive entry points

- **Hardware-free:** Python/YAML parsing, stubbed unit tests, Ansible `--syntax-check`, and plotting existing CSVs to new output paths.
- **Changes hosts:** `site.yml` installs services and changes host prerequisites.
- **Replaces monitoring:** `monitoring.yml` deletes the monitoring namespace.
- **Runs experiments:** scenario playbooks and `test.yml` delete/recreate workloads and may affect data, scheduling, and physical power.
- **Explicit hardware power-off:** `playbooks/scale_up_timing_demo.yml` includes fixed hardware targets. The batch runner needs `--playbook playbooks/scale_up_timing_demo.yml` when invoked from the repository root; its CLI options do not override every embedded address.
- **Destroys the cluster:** `reset.yml` stops/resets k0s, removes data/binaries, modifies networking and mounts, and reboots nodes.

Ansible check mode is not a safety guarantee for shell commands or hardware-control workflows. Do not use it as a substitute for code review and isolated hardware.

## Evidence required for a research release

1. Resolve the configuration/wiring gaps above on a fresh, authorized lab setup.
2. Record hardware, OS/kernel/firmware, Python/package/collection versions, container digests, k0s/Helm/chart versions, and exact sanitized run configuration.
3. Verify service readiness and expected measurement/CSV fields, not merely a zero playbook exit code.
4. Execute a short smoke experiment, stop its background processes, then demonstrate a repeat run without stale state.
5. Include provenance and licenses for input traces, plus figure-generation commands and uncertainty/statistical methods.
6. Archive the exact software release used for reproduction and record its persistent identifier, if available, alongside the paper citations above.
