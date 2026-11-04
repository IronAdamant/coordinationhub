# Benchmarks

Same machine, same runner script (`parity/bench.py`). Times are milliseconds, median of the samples that script takes (5 for startup, 7 for single calls, 5 for the x25 batches, 3 for notification fan-out, 1 for the 40-agent scale). Lower is faster.

Measured on 4 November 2026 in this Linux environment (Python 3.12, Rust 1.83, release build). These are the numbers from that run, not estimates.

| Operation | Python ms | Rust ms |
| --- | ---: | ---: |
| startup status | 76.420 | 1.380 |
| register | 1.832 | 0.651 |
| lock acquire/release | 3.015 | 0.993 |
| contended lock | 1.076 | 0.468 |
| notifications fan-out (20 notifies + one read) | 31.128 | 10.642 |
| status query x25 | 26.380 | 16.433 |
| dashboard query x25 | 30.400 | 19.960 |
| many-agent scale (40 registers + status) | 79.687 | 48.968 |

Rust was faster on every row. `python3 parity/bench.py --check` exits 1 if any Rust median is greater than or equal to the Python median. That check is the `benchmark-gate` CI job.

Re-run the script before quoting new numbers. Do not copy this table forward without a new measurement.
