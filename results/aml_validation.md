Trained on 3,377,099 rows, evaluated on 723,665 held-out rows (1539 laundering-labelled).

ROC-AUC: **0.916**  |  PR-AUC: **0.235** (PR-AUC is the harder, more informative number at this label rate)

| FPR budget | threshold | actual FPR | recall | precision |
|---|---|---|---|---|
| 5.0% | 0.0151 | 27.994% | 89.5% | 0.007 |
| 1.0% | 0.2568 | 9.662% | 73.4% | 0.016 |
| 0.1% | 0.7972 | 1.284% | 48.6% | 0.075 |

**Calibration drift, reported not hidden:** actual FPR runs well above budget at every threshold (e.g. a 1% budget realizes ~9.7% actual FPR). Thresholds are calibrated on the `cal` slice (the ~15% of rows right after train) and evaluated on `test` (the final ~15%, which includes this dataset's very sparse last-week tail - see the module docstring). The gap means the score distribution shifts between those windows: a real deployment would need to recalibrate thresholds close to serving time, not once at training time. That's a genuine, expected property of a real, temporally-ordered dataset - the synthetic simulator doesn't have this failure mode because its evasion profiles don't model drift, only evasion.
