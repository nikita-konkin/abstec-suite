# absolTEC model extraction report

Input CSV: N:\abstec-suite\experiments\absoltec_experiments_combined_2026_001.csv
Samples used: 209

## Samples per site

- aksu: 209

## Feature basis

f = [1, e, t, c, e2, t2, c2, e_t, e_c, t_c]
where e=elevation_cutoff, t=time_step_hours, c=correction_coefficient

## Target: iv_non_zero_count

- R2: 0.938682
- MAE: 2.481237
- RMSE: 2.672853
- Equation: iv_non_zero_count = 65.064203 - 0.123875*e - 66.556445*t - 37.985396*c + 0.010646*e2 + 24.182212*t2 + 21.982894*c2 + 0.155076*e_t - 0.174961*e_c + 0.221791*t_c

## Target: iv_max

- R2: 0.731902
- MAE: 3.415734
- RMSE: 4.550556
- Equation: iv_max = 121.712933 + 0.151636*e - 6.173841*t - 153.804751*c + 0.020430*e2 - 19.795616*t2 + 60.272958*c2 + 0.180865*e_t - 0.697757*e_c + 25.824624*t_c

## Target: dcb_entry_count

- R2: 0.765491
- MAE: 0.335995
- RMSE: 0.448333
- Equation: dcb_entry_count = 43.167266 + 0.246885*e + 0.567508*t - 4.232223*c - 0.022771*e2 - 0.062949*t2 + 2.629589*c2 - 0.011919*e_t - 0.026511*e_c - 0.423072*t_c

