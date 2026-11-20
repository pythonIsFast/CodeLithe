# Desktop benchmark

Compare CodeLithe and the ChatGPT desktop app (using Codex with local workspaces) on the same Linux machine. The benchmark covers app startup, idle resource use, and one active conversation. Each coding task uses a fresh copy of a small Python repository.

## Prerequisites

- A graphical Linux session with cgroup v2, a running systemd user manager, Python 3 and Git.
- The ChatGPT Linux desktop app installed with its `chatgpt` launcher on `PATH`.
- CodeLithe's dependencies available to the Python interpreter used for this benchmark. CodeLithe runs from the current checkout.

## Run

From the repository root, open CodeLithe with its separate benchmark profile and sign in. Quit it after signing in. Before measuring ChatGPT, quit all running ChatGPT windows through its app menu so the benchmark can launch its own process.

```sh
python3 benchmarks/desktop/benchmark.py setup
```

Run each app in turn and follow the terminal prompts. For ChatGPT, select **Codex** and use the printed folder as a local workspace. Enter `s` at the coding-task prompt to keep just the startup and idle measurements.

```sh
python3 benchmarks/desktop/benchmark.py run chatgpt
python3 benchmarks/desktop/benchmark.py run codelithe
python3 benchmarks/desktop/benchmark.py report
```

Use the same window size, model, reasoning effort, and comparable approval settings for both apps where possible. Select model and effort in the app. The optional `--model` and `--effort` arguments record those selections in the report. Use `--warmup` to label the first run for each app and alternate apps across repeated runs. For a quick trial, add `--idle-seconds 10`. Regular runs default to 60 seconds of idle sampling.

The report command prints the path to `combined.json`, which contains the measurements and test outcomes. The temporary results, app logs, test repositories, and separate CodeLithe sign-in profile live outside this checkout under `codelithe-desktop-benchmark-<uid>` in the system temporary directory. Share the results file only.

## Recorded ChatGPT runs

The [recorded results](results/chatgpt-26.1111.21308.json) contain one complete ChatGPT Desktop run and a second startup-only run on Zorin OS 18.1. The coding task passed. The selected model and reasoning effort were unrecorded.

| Phase | Duration | Median PSS | CPU (one logical core) |
| --- | ---: | ---: | ---: |
| Startup, first run | 6.05 s | 425.3 MiB | 111.05% |
| Startup, second run | 13.62 s | 687.9 MiB | 80.49% |
| Idle | 60.04 s | 916.0 MiB | 9.59% |
| One active chat | 29.65 s | 1,036.7 MiB | 32.04% |

## Measurements

| Metric | How it is measured |
| --- | --- |
| Startup and completion time | Manual terminal markers include reaction time and window switches. |
| CPU | Total CPU time for the app's systemd scope. Percentages are normalized to one logical core. |
| RAM | Median and observed peak PSS across readable processes in the scope. The report flags samples with missing PSS data. |
| Disk | Block read and write bytes from cgroup counters. Where those counters are unavailable, sampled per-process values provide a lower bound and can miss short-lived processes. |
| Task result | The runner checks that the original tests are intact and pass after each coding task. |

Run `python3 benchmarks/desktop/benchmark.py self-test` to check the resource counters and fixture without opening either desktop app.
