# App Energy Estimator

## Introduction
Because of the heterogeneous nature of the cluster on which I am conducting my experiments, it is not easy to estimate the energy consumption of a given application. This is primarly due to the fact that we cannot know on which type of hardware the app will be running. Addictionnaly, the RK1 model has a heterogeneous CPU with 4 performance core and 4 efficiency cores. So I had to come up with a simple solution for that problem.

## Proposed Solution
For the heterogeneous CPU, the solution is simple: 
- I used the following command to pin the process to core number 7 ```stress-ng --1 --cpu-ops 100000000 --timeout 10s --metrics-brief --taskset 7```. I did the same thing for all the other cores, and took the mean processing power. 

For the heterogeneous nature of the cluster, I did something similar too. I took the sum of the processing capabilities of 2 RK1 and 1 CM4. I then remove the idle power consumption, and connected the 3 together. I put the CM4 at the end because it is the one that will be powered off first. 

## Problems
Unless a workload needs more processing power that what 2 RK1 can provide, the estimation will always be equal to what an estimation on an RK1 alone is. 


**This is an update a couple days later (09/02/2026):** 
The energy consumed by the idle plateform cannot be neglacted and should be added to the energy consumption of the applications. 

| | Turing Pi | RK1 | CM4 | 
|---|---|---|---|
| **Power** | 3.73 W | 1.95 W | 2.17 W |
| **Energy Per Day** | 90 Wh | 47 Wh | 52 Wh |

*Table 1 - The power and energy consumption of the idle plateform*

In Table 1, we can see that the energy consumption of the idle nodes can be quite high. This is why I think it should be included in the energy consumption calculations. 
One way of doing that would be to divide the total energy consumption of 236 Wh per day, assuming that all the nodes are constantly running, between all the applications. So if we have a total of 23 applications the energy each one would get would be: 236/23 = 10.3 Wh per day. 

### Update of 16/02/2026

The way of distributing the static energy consumption that I used above is not good for the following reasons: 
- It assumes that we have 23 applications running constantly which is never the case
- This causes an underestimation of the static power consumption per application leading to a surplus in the energy consumption.

After discussing this issue with Guillaume and Laurent we came up with a different strategy. We will substract the static energy consumption for the energy budget before passing it to the solver. 
