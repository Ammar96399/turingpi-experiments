#!/usr/bin/env python3
"""Generate aggregated Turing Pi cluster power usage CSV from individual node data."""

import csv
from pathlib import Path

# Node specs
RK1_COUNT = 2
RK1_POWER = 1104
CM4_COUNT = 1
CM4_POWER = 436
TOTAL_POWER = RK1_COUNT * RK1_POWER + CM4_COUNT * CM4_POWER

# Individual node power data at each load level (0-10 steps)
RK1_DATA = {
  # 0: 3.005175,
  1: 3.170206,
  2: 3.432241,
  3: 4.051837,
  4: 4.388823,
  5: 4.759147,
  6: 5.617137,
  7: 5.842942,
  8: 6.047473,
  9: 6.250205,
  10: 6.573066,
}

CM4_DATA = {
  # 0: 3.215359,
  1: 3.505433,
  2: 3.658173,
  3: 3.962440,
  4: 4.130222,
  5: 4.317277,
  6: 4.487903,
  7: 4.659803,
  8: 4.929325,
  9: 5.159339,
  10: 5.336310,
}


def generate_cluster_data():
    """Generate cluster-level power data for all load combinations."""
    rows = [["cluster_load_percent", "total_power_watts"]]
    
    rk1_bogos_steps = RK1_POWER/10
    cm4_bogos_steps = CM4_POWER/10
    rows.append([0,0])
    for rk1_steps in range(1,22):
      if (rk1_steps%11) != 0:
        rows.append([
          str(round(rk1_bogos_steps * (rk1_steps - int(rk1_steps / 11)), 2)), 
          str(round(RK1_DATA[(rk1_steps%11)] - 3.005175 + (int(rk1_steps / 11)) * (RK1_DATA[10] - 3.005175), 2))
        ])
    
    for cm4_steps in range(1,11): 
      if (cm4_steps%11) != 0:
        rows.append([
            str(round(2208.0 + cm4_bogos_steps * cm4_steps, 2)), 
            str(round(CM4_DATA[(cm4_steps%11)] - 3.215359 + RK1_COUNT * (RK1_DATA[10] - 3.005175), 2))
        ])
  
    return rows

def main():
    """Generate and write power CSV."""
    rows = generate_cluster_data()
    
    csv_path = Path(__file__).parent / "power_usage.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    
    print(f"Generated {csv_path} with {len(rows) - 1} data points")
    print(rows)


if __name__ == "__main__":
    main()

