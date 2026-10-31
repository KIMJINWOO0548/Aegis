# Signed window combination: frozen offline protocol

2026-10-20. User explicitly permits custom combinations/new approaches. This is an engineering experiment using existing statistics, not a claim of scientific novelty. Runtime/API/archived raw data are unchanged.

## Formula frozen before held-out execution

Keep `flow-review-dev-1` as an unmodified baseline. Add independent signed windows W={50,100,200}ms. At each sample, the context BEFORE that sample is the previous1s/maximum256 valid residuals. Require5samples and200ms span, use median b and unscaled MAD m. Each candidate window freezes the prior context belonging to its FIRST sample, not the current sample or a future point.

Require n>=3, observed span>=0.8W, largest sample interval<=W/2. Emit a candidate on false→true transition per window/sign when abs(mean(error)−b)>max(0.5L/min,4×max(0.1L/min,1.4826m)/sqrt(n)). All outputs remain UNRESOLVED. The emission timestamp is the actual current sample time; window start is support, never a backdated detection. Constants do not imply calibrated probabilities, particularly under autocorrelation.

The experimental path resets on invalid quality/range/incomplete batch, explicit discontinuity, >1.5s gap, mode/state changes, and setpoint anchor movement>=0.5L/min. Duplicate/older observations are ignored. Context and transition memory are bounded. Existing raw candidates are NEVER removed or fed into experimental baseline state.

Group the union of raw and accumulation candidates when consecutive emissions are<=100ms apart, capped at1second from group start. Retain every child ID. Group count is navigation compression only. Report individual raw/accumulation/union counts, duplicate emissions and new onset recall separately.

## One held-out run

No tuning. Previously used seeds11/97/2027 remain regression only. Register previously unused seeds49001..49020 before execution. For each of8families, observe4seconds at100Hz. Families: independent noise, independent noise+pulse, alternating burst noise, alternating burst+pulse, AR(1)noise rho0.5, AR(1)noise rho0.9, AR(1)rho0.9+pulse, and an identical-observation artifact counterpart of the last family. Noise sigma0.9L/min, burst alternates4positive/4negative samples with0.9L/min amplitude plus0.5sigma noise between2..3s. Pulse onset2.23s, amplitude1.5L/min; sign alternates by seed and duration cycles50/100/200ms. Clean truth/cause stays in the harness, never detector input. First1second is warmup, so background rates use the eligible observed duration outside truth intervals.

Compare raw; raw+each window; raw+all windows; grouping of all. Event recovery requires a NEW emission inside the truth interval. Report late emissions separately. Compare onsets/recall/delay, additional background candidates/minute, burst candidate increase, group count and unchanged raw candidates. Identical public observations must produce identical unresolved outputs for both causes.

Acceptance is conjunctive: recover>=1 baseline-missed true pulse; preserve every baseline candidate; add<=1 background candidate/minute in EACH independent/AR noise-only family; add<=10% to baseline alternating-burst candidate count. Failure means publish and stop promotion. Do not retune on these seeds; any next threshold revision needs a fresh reserved set. These experiment gates are not hardware requirements.

Cross-sensor lag evidence is deliberately deferred until this additive detection experiment earns its cost. Neither missing sensor support nor common-mode sensor changes establish cause. Existing live socket/process failures stay separately documented.

Primary references: [NIST MAD](https://www.itl.nist.gov/div898/software/dataplot/refman2/auxillar/mad.htm), [NIST CUSUM](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/cusum.htm), [NIST autocorrelated process monitoring](https://www.nist.gov/publications/statistical-process-monitoring-autocorrelated-data). This prototype uses finite signed windows rather than a CUSUM recursion, to keep support and duration inspectable.
