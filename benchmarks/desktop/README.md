# Desktop benchmark

Compare CodeLithe and the ChatGPT desktop app (using Codex with local workspaces) on the same Linux machine. The benchmark covers app startup, idle resource use, one active conversation, and two concurrent conversations. Each coding task uses a fresh copy of a small Python repository.

## Prerequisites

- A graphical Linux session with cgroup v2, a running systemd user manager, Python 3 and Git.
- The ChatGPT Linux desktop app installed with its `chatgpt` launcher on `PATH`.
- CodeLithe's dependencies available to the Python interpreter used for this benchmark. CodeLithe runs from the current checkout.

## Run

From the repository root, open CodeLithe with its separate benchmark profile and sign in. Quit it after signing in. Quit any other instances of both apps, including the ChatGPT window you might be reading this from.

```sh
python3 benchmarks/desktop/benchmark.py setup
```

Run each app in turn and follow the terminal prompts. For ChatGPT, select **Codex** and use local workspaces. During the parallel phase, open one local chat for each printed folder and submit both prepared prompts back to back.

```sh
python3 benchmarks/desktop/benchmark.py run chatgpt
python3 benchmarks/desktop/benchmark.py run codelithe
python3 benchmarks/desktop/benchmark.py report
```

Use the same window size, model, reasoning effort, and comparable approval settings for both apps where possible. Select model and effort in the app. The optional `--model` and `--effort` arguments record those selections in the report. Use `--warmup` to label the first run for each app and alternate apps across repeated runs. For a quick trial, add `--idle-seconds 10`. Regular runs default to 60 seconds of idle sampling.

The report command prints the path to `combined.json`, which contains the measurements and test outcomes. The temporary results, test repositories, and separate CodeLithe sign-in profile live outside this checkout under `codelithe-desktop-benchmark-<uid>` in the system temporary directory. Share the results file only.

## Measurements

| Metric | How it is measured |
| --- | --- |
| Startup and completion time | Manual terminal markers include reaction time and window switches. |
| CPU | Total CPU time for the app's systemd scope. Percentages are normalized to one logical core. |
| RAM | Median and observed peak PSS across readable processes in the scope. The report flags samples with missing PSS data. |
| Disk | Block read and write bytes from cgroup counters. Where those counters are unavailable, sampled per-process values provide a lower bound and can miss short-lived processes. |
| Task result | The runner checks that the original tests are intact and pass after each coding task. |

Run `python3 benchmarks/desktop/benchmark.py self-test` to check the resource counters and fixture without opening either desktop app.
