# Performance report

Measured locally on Windows using disposable SQLite and Chromium, not production telemetry. Repeated browser runs differed materially due to warm imports/cache/background load. No controlled before/after speed claim is justified.

| Measurement | Observation | Interpretation |
|---|---|---|
| Baseline complete regression suite |398.03s | Different workload than expanded suite; not user latency |
| Intermediate expanded passing suite |275.83s,3997 tests | Not a valid product-speed improvement comparison |
| Final full regression suite |245.92s,4020 passed /24 skipped | Different workload/runtime conditions; not a controlled speed comparison |
| Initial passing hub browser navigation |4324ms to networkidle | Includes shared assets/local startup effects |
| Expanded passing browser navigation |1572ms to networkidle | Single local run, not P50/P95 or controlled improvement |
| Final delivery browser navigation |4198ms to networkidle | Single local run; variation reinforces the need for controlled measurements |
| Live original-blueprint check |27ms initially,18ms on final repeat,503 | Configuration refusal, not AI generation latency |
| Original course end-to-end generation |Not measured with real provider | Browser worker generations are fixtures |
| AI tutor latency under real provider |Not measured | Protected concept path is deterministic; no model benchmark |
| Throughput/concurrency/memory |Not measured | Needs production-like PostgreSQL and load harness |

Bounded behavior matters operationally: progress polls read saved data; course generation runs outside HTTP lesson polling; one lesson per worker step; evidence context is limited to20 records; list endpoints have explicit limits; token reservations and leases cap repeated work. These are implementation properties, not measured scalability.

Remaining measurement work: cold/warm asset waterfall and bundle baseline, authenticated tutor P50/P95 by provider, real six-lesson generation latency/cost, cancellation under load, DB query plans and tenant-scale lists, device battery/offline timing, and a school-network profile. Set rollout targets only after those baselines exist. No arbitrary success percentage or latency improvement is claimed.
