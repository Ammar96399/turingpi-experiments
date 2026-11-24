import pandas as pd
import csv
from dateutil import parser

time_format = "%Y-%m-%dT%H:%M:%S.%fZ"
df = pd.read_csv(
    "/home/akazem/Documents/PhD/projects/energy_proportionality/cluster_configuration/turing_pi_emulation/playbooks/cluster_state_log_3.csv"
)
df = df.drop(columns=["error"])

df_result = pd.DataFrame()

for index, row in df.iterrows():
    boot_up = (
        parser.parse(row["boot_ping_ok_at"]) - parser.parse(row["requested_at"])
    ).total_seconds()
    node_join = (
        parser.parse(row["joined_at"]) - parser.parse(row["requested_at"])
    ).total_seconds()
    collect_data = (
        parser.parse(row["descheduler_launched_at"]) - parser.parse(row["requested_at"])
    ).total_seconds()
    descheduler = (
        parser.parse(row["descheduler_done_at"]) - parser.parse(row["requested_at"])
    ).total_seconds()
    pod_terminate_start = (
        parser.parse(row["source_pod_stop_started_at"])
        - parser.parse(row["requested_at"])
    ).total_seconds()
    pod_terminate_end = (
        parser.parse(row["source_pod_stop_finished_at"])
        - parser.parse(row["requested_at"])
    ).total_seconds()
    pod_setup_start = (
        parser.parse(row["target_pod_setup_started_at"])
        - parser.parse(row["requested_at"])
    ).total_seconds()
    pod_setup_end = (
        parser.parse(row["target_pod_ready_at"]) - parser.parse(row["requested_at"])
    ).total_seconds()
    df_result = pd.concat(
        [
            df_result,
            pd.DataFrame(
                {
                    "boot_up_start": [0],
                    "boot_up_end": [boot_up],
                    "join_start": [boot_up],
                    "join_end": [node_join],
                    "collect_data_start": [node_join],
                    "collect_data_end": [collect_data],
                    "descheduler_start": [collect_data],
                    "descheduler_end": [descheduler],
                    "pod_terminate_start": [pod_terminate_start],
                    "pod_terminate_end": [pod_terminate_end],
                    "pod_setup_start": [pod_setup_start],
                    "pod_setup_end": [pod_setup_end],
                    "rebalancing_start": [collect_data],
                    "rebalancing_end": [pod_terminate_end],
                    "unavailable_start": [pod_terminate_start],
                    "unavailable_end": [pod_setup_end],
                    "boot_up_duration": [boot_up],
                    "join_duration": [node_join - boot_up],
                    "collect_data_duration": [collect_data - node_join],
                    "descheduler_duration": [descheduler - collect_data],
                    "pod_terminate_duration": [pod_terminate_end - pod_terminate_start],
                    "pod_setup_duration": [pod_setup_end - pod_setup_start],
                    "rebalancing_duration": [pod_terminate_end - collect_data],
                    "unavailable_duration": [pod_setup_end - pod_terminate_start],
                }
            ),
        ],
        ignore_index=True,
    )

summary_df = pd.DataFrame({"mean": df_result.mean(), "std_dev": df_result.std()})


summary_df.to_csv("out.csv", index=True)
