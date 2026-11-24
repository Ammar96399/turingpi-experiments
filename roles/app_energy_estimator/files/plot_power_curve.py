#!/usr/bin/env python3
"""Plot cluster power consumption vs load."""

import csv
import matplotlib.pyplot as plt
from pathlib import Path

def plot_power_curve():
    """Read CSV and plot power vs load."""
    csv_path = Path(__file__).parent / "power_usage.csv"
    
    # Read data
    loads = []
    powers = []
    
    with open(csv_path, 'r') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        for row in reader:
            loads.append(float(row[0]))
            powers.append(float(row[1]))
    
    # Create plot
    plt.figure(figsize=(12, 6))
    plt.plot(loads, powers, 'b-', linewidth=2, label='Power Consumption')
    plt.scatter(loads, powers, color='red', s=30, alpha=0.6, zorder=5)
    
    # Add vertical lines to show transition points
    plt.axvline(x=2208.0, color='gray', linestyle='--', alpha=0.5, label='2×RK1 at 100%')
    
    plt.xlabel('Cluster Load (bogos)', fontsize=12)
    plt.ylabel('Power Consumption (watts)', fontsize=12)
    plt.title('Turing Pi Cluster Power Consumption vs Processing Load', fontsize=14, fontweight='bold')
    plt.grid(True, alpha=0.3)
    plt.legend()
    
    # Annotate key points
    plt.annotate('2×RK1 only', xy=(1104, powers[11]), xytext=(1104, powers[11] + 1),
                arrowprops=dict(arrowstyle='->', color='black', lw=1))
    plt.annotate('Full cluster', xy=(2644, powers[-1]), xytext=(2500, powers[-1] + 0.5),
                arrowprops=dict(arrowstyle='->', color='black', lw=1))
    
    plt.tight_layout()
    
    # Save plot
    output_path = Path(__file__).parent / "power_curve.png"
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"Plot saved to {output_path}")
    
    # Show statistics
    print(f"\nStatistics:")
    print(f"  Min power: {min(powers):.2f} W at {loads[powers.index(min(powers))]} bogos")
    print(f"  Max power: {max(powers):.2f} W at {loads[powers.index(max(powers))]} bogos")
    print(f"  Power range: {max(powers) - min(powers):.2f} W")
    
    plt.show()

if __name__ == "__main__":
    plot_power_curve()
