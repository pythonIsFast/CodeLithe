#!/usr/bin/env python3
"""Interactive Linux desktop benchmark. Never reads chats or credentials."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CGROUP = Path("/sys/fs/cgroup")
DATA = Path(tempfile.gettempdir()) / f"codelithe-desktop-benchmark-{os.getuid()}"
PROFILE = DATA / "codelithe-profile"
RESULTS = DATA / "results"
PROMPT = (HERE / "prompt.txt").read_text().strip()
FIXTURE = HERE / "fixture"
TEST_TEMPLATE = FIXTURE / "interval_cases.py"
MIB = 1024 ** 2
SAMPLE_INTERVAL = 0.25
CLOCK_TICKS = os.sysconf("SC_CLK_TCK")


def cpu_usage(directory):
    fields = dict(line.split() for line in (directory / "cpu.stat").read_text().splitlines())
    return int(fields["usage_usec"])


def disk_usage(directory):
    try:
        lines = (directory / "io.stat").read_text().splitlines()
    except FileNotFoundError:
        return None
    devices = {}
    for line in lines:
        name, *values = line.split()
        counters = dict(value.split("=") for value in values)
        devices[name] = (int(counters["rbytes"]), int(counters["wbytes"]))
    return devices


def process_sample(directory):
    files = [directory / "cgroup.procs", *directory.glob("**/cgroup.procs")]
    pids = set()
    for path in files:
        try:
            pids.update(path.read_text().split())
        except FileNotFoundError:
            continue
    pss_kib = 0
    measured = 0
    missing = 0
    process_io = {}
    fallback_io = not (directory / "io.stat").exists()
    for pid in pids:
        process = Path("/proc") / pid
        try:
            with (process / "smaps_rollup").open() as stream:
                for line in stream:
                    if line.startswith("Pss:"):
                        pss_kib += int(line.split()[1])
                        measured += 1
                        break
                else:
                    missing += 1
        except OSError:
            missing += process.exists()
        if fallback_io:
            try:
                stat_fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
                started_ticks = int(stat_fields[19])
                io = dict(line.split(": ") for line in (process / "io").read_text().splitlines())
                process_io[(pid, started_ticks)] = (int(io["read_bytes"]), int(io["write_bytes"]))
            except OSError:
                continue
    return {
        "pss_mib": pss_kib / 1024 if measured else None,
        "pss_missing": missing,
        "cgroup_mib": int((directory / "memory.current").read_text()) / MIB,
        "proc_io": process_io,
    }


def boundary(directory):
    at = time.monotonic()
    boot_ticks = int(time.clock_gettime(time.CLOCK_BOOTTIME) * CLOCK_TICKS)
    io = disk_usage(directory)
    return {
        "at": at,
        "boot_ticks": boot_ticks,
        "cpu_us": cpu_usage(directory),
        "io": io,
        "proc_io": process_sample(directory)["proc_io"] if io is None else {},
    }


def measure(directory, seconds=None):
    samples = []
    if seconds is None:
        # Poll the terminal in canonical mode. The app can keep focus while
        # the scope is sampled, then Enter ends the phase.
        import select

        while not select.select([sys.stdin], [], [], 0)[0]:
            samples.append(process_sample(directory))
            if select.select([sys.stdin], [], [], SAMPLE_INTERVAL)[0]:
                break
        if not sys.stdin.readline():
            raise EOFError("Terminal closed")
    else:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            samples.append(process_sample(directory))
            time.sleep(max(0, min(SAMPLE_INTERVAL, deadline - time.monotonic())))
    end = boundary(directory)
    samples.append(process_sample(directory))
    return end, samples


def summary(start, end, samples):
    duration = end["at"] - start["at"]
    cpu_seconds = (end["cpu_us"] - start["cpu_us"]) / 1_000_000
    pss = [sample["pss_mib"] for sample in samples if sample["pss_mib"] is not None]
    first_io = start["io"]
    last_io = end["io"]
    if first_io is not None and last_io is not None:
        io = {}
        for device in sorted(first_io.keys() | last_io.keys()):
            old_read, old_write = first_io.get(device, (0, 0))
            read, write = last_io.get(device, (old_read, old_write))
            io[device] = {"read_bytes": read - old_read, "write_bytes": write - old_write}
        disk = {"disk_method": "cgroup_io_stat", "disk_by_device": io}
    else:
        seen = start["proc_io"].copy()
        read_bytes = write_bytes = 0
        for counters in [*(sample["proc_io"] for sample in samples), end["proc_io"]]:
            for key, (read, write) in counters.items():
                default = (0, 0) if key[1] >= start["boot_ticks"] else (read, write)
                old_read, old_write = seen.get(key, default)
                read_bytes += max(0, read - old_read)
                write_bytes += max(0, write - old_write)
                seen[key] = (read, write)
        disk = {
            "disk_method": "proc_io_sampled_lower_bound",
            "disk_process_lower_bound": {"read_bytes": read_bytes, "write_bytes": write_bytes},
        }
    return {
        "seconds": round(duration, 3),
        "cpu_seconds": round(cpu_seconds, 3),
        "cpu_pct_one_core": round(100 * cpu_seconds / max(duration, 0.001), 2),
        "pss_median_mib": round(statistics.median(pss), 1) if pss else None,
        "pss_peak_mib": round(max(pss), 1) if pss else None,
        "samples": len(samples),
        "pss_incomplete_samples": sum(
            sample["pss_missing"] > 0 or sample["pss_mib"] is None for sample in samples
        ),
        "cgroup_memory_peak_sample_mib": round(max(s["cgroup_mib"] for s in samples), 1),
        **disk,
    }


def command_for(app):
    if app == "chatgpt":
        launcher = shutil.which("chatgpt")
        if not launcher:
            raise RuntimeError("The ChatGPT desktop launcher (chatgpt) is not on PATH.")
        return [launcher]
    private_directory(PROFILE)
    return [
        sys.executable, str(REPO / "run.py"), "--data-dir", str(PROFILE), "--port", "10584",
    ]


def live_chatgpt_pids():
    pids = []
    for process in Path("/proc").glob("[0-9]*"):
        try:
            executable = (process / "exe").readlink()
        except OSError:
            continue
        if executable.name == "ChatGPT" or executable.name == "ChatGPT (deleted)":
            pids.append(process.name)
    return pids


def preflight():
    if not (CGROUP / "cgroup.controllers").is_file():
        raise RuntimeError("This benchmark requires Linux cgroup v2.")
    subprocess.run(
        ["systemctl", "--user", "show-environment"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
    )


def private_directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"Expected a private directory owned by the current user: {path}")


def launch(command, log_path=None):
    unit = f"desktop-benchmark-{uuid4().hex[:12]}.scope"
    log = log_path.open("wb") if log_path is not None else nullcontext(subprocess.DEVNULL)
    with log as output:
        process = subprocess.Popen(
            [
                "systemd-run", "--user", "--scope", "--quiet", "--unit", unit,
                "--property=CPUAccounting=yes", "--property=MemoryAccounting=yes",
                "--property=IOAccounting=yes", *command,
            ],
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
        )
    return unit, process


def scope_path(unit, process, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["systemctl", "--user", "show", "--property=ControlGroup", "--value", unit],
            capture_output=True, text=True, check=False,
        )
        group = result.stdout.strip()
        if group.startswith("/") and group != "/":
            candidate = CGROUP / group.lstrip("/")
            if (candidate / "cpu.stat").exists():
                return candidate
        if process.poll() is not None:
            raise RuntimeError(f"The app exited while starting in {unit}. Quit any other instances first.")
        time.sleep(0.05)
    raise RuntimeError(f"The scope {unit} did not appear within {timeout} seconds.")


def fixture(destination):
    destination.mkdir()
    shutil.copyfile(FIXTURE / "intervals.py", destination / "intervals.py")
    shutil.copyfile(TEST_TEMPLATE, destination / "test_intervals.py")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (
        ["init", "--quiet"],
        ["add", "intervals.py", "test_intervals.py"],
        ["-c", "user.name=Benchmark", "-c", "user.email=benchmark@example.invalid",
         "-c", "commit.gpgSign=false", "commit", "--quiet", "-m", "Create benchmark fixture"],
    ):
        subprocess.run(["git", *args], cwd=destination, env=env, check=True)
    return destination


def verify(folders):
    results = []
    for directory in folders:
        passed = False
        try:
            intact = (directory / "test_intervals.py").read_bytes() == TEST_TEMPLATE.read_bytes()
            if intact:
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "unittest", "-q"],
                    cwd=directory, capture_output=True, timeout=15,
                )
                passed = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        results.append({"fixture": directory.name, "passed": passed})
    return results


def version(app):
    command = (
        ["dpkg-query", "-W", "-f=${Version}", "chatgpt"]
        if app == "chatgpt" else ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"]
    )
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run(args):
    preflight()
    if args.app == "chatgpt" and live_chatgpt_pids():
        raise RuntimeError("ChatGPT is still running. Quit it from the app menu or press Ctrl+Q.")
    private_directory(RESULTS)
    workspace = Path(tempfile.mkdtemp(prefix=f"{args.app}-", dir=RESULTS))
    project = fixture(workspace / "single")
    result = {
        "app": args.app, "version": version(args.app), "model": args.model,
        "effort": args.effort, "warmup": args.warmup, "idle_target_seconds": args.idle_seconds,
        "phases": {},
    }
    report = workspace / "result.json"
    log_path = workspace / "launch.log"

    def record(name, start, end, samples):
        result["phases"][name] = summary(start, end, samples)
        report.write_text(json.dumps(result, indent=2) + "\n")
        print(f"Saved {name}", flush=True)

    command = command_for(args.app)
    print(f"Result file: {report}")
    print(f"Local app log for troubleshooting: {log_path}")
    input("Press Enter to launch the app. Return here once the composer is usable. ")
    started = {
        "at": time.monotonic(),
        "boot_ticks": int(time.clock_gettime(time.CLOCK_BOOTTIME) * CLOCK_TICKS),
        "cpu_us": 0, "io": {}, "proc_io": {},
    }
    unit, process = launch(command, log_path)
    try:
        group = scope_path(unit, process)
        print("Once the composer is usable, return to the terminal and press Enter.", flush=True)
        end, samples = measure(group)
        if args.app == "chatgpt":
            pids = live_chatgpt_pids()
            if not pids or not any(
                group.relative_to(CGROUP).as_posix() in (Path("/proc") / pid / "cgroup").read_text()
                for pid in pids
            ):
                raise RuntimeError("The ChatGPT process could not be matched to the benchmark scope.")
        record("startup_manual", started, end, samples)

        input("Wait until the app is fully loaded and idle, then press Enter to start measuring. ")
        print(f"Leave the app untouched for {args.idle_seconds} seconds.", flush=True)
        start = boundary(group)
        end, samples = measure(group, args.idle_seconds)
        record("idle", start, end, samples)

        print(f"\nOpen a new local chat for this folder: {project}")
        print(f"Select the model and reasoning effort, then paste this prompt:\n{PROMPT}\n")
        choice = input("Press Enter to start measuring or type s to skip the coding task. ")
        if choice.strip().lower() != "s":
            start = boundary(group)
            print("Submit the prompt in the app. Return here and press Enter when the chat finishes.", flush=True)
            end, samples = measure(group)
            record("single", start, end, samples)
            result["phases"]["single"]["verification"] = verify([project])
            report.write_text(json.dumps(result, indent=2) + "\n")
            print("Tests:", result["phases"]["single"]["verification"])

        print(f"\nBenchmark complete. To share this run, use: {report}")
        input("Quit the app normally, then press Enter. ")
    finally:
        if process.poll() is None:
            print(f"The app is still running. Quit it before the next run ({unit}).")


def show_report():
    private_directory(RESULTS)
    reports = sorted(RESULTS.glob("*/result.json"))
    runs = []
    for path in reports:
        item = json.loads(path.read_text())
        item["phases"].pop("parallel", None)  # Omit the retired phase from older reports too.
        runs.append(item)
        marker = " (warm-up)" if item["warmup"] else ""
        print(f"\n{item['app']} {item['version']}{marker} | {path}")
        for name, phase in item["phases"].items():
            precision = " (partial PSS)" if phase["pss_incomplete_samples"] else ""
            status = phase.get("verification", [])
            test_status = f" | Tests {sum(case['passed'] for case in status)}/{len(status)}" if status else ""
            print(
                f"  {name:15} {phase['seconds']:7.2f} s | "
                f"CPU {phase['cpu_pct_one_core']:6.2f} % | "
                f"PSS Median/Peak {phase['pss_median_mib']}/{phase['pss_peak_mib']} MiB"
                f"{precision}{test_status}"
            )
            for device, counters in phase.get("disk_by_device", {}).items():
                print(
                    f"    I/O {device}   read {counters['read_bytes'] / MIB:.2f} MiB "
                    f"write {counters['write_bytes'] / MIB:.2f} MiB"
                )
            if counters := phase.get("disk_process_lower_bound"):
                print(
                    f"    I/O lower bound   read {counters['read_bytes'] / MIB:.2f} MiB "
                    f"write {counters['write_bytes'] / MIB:.2f} MiB"
                )
    bundle = RESULTS / "combined.json"
    bundle.write_text(json.dumps(runs, indent=2) + "\n")
    print(f"\nCombined results: {bundle}")


def self_test():
    preflight()
    with tempfile.TemporaryDirectory(dir=DATA) as temporary:
        probe = """
import os, sys, time
data = bytearray(8 * 1024 * 1024)
time.sleep(0.4)
with open(sys.argv[1], "wb", buffering=0) as file:
    file.write(data)
    os.fsync(file.fileno())
time.sleep(2)
"""
        unit, process = launch([sys.executable, "-c", probe, str(Path(temporary) / "probe.dat")])
        directory = scope_path(unit, process)
        start = boundary(directory)
        end, samples = measure(directory, 1.0)
        result = summary(start, end, samples)
        writes = (
            sum(device["write_bytes"] for device in result["disk_by_device"].values())
            if result["disk_method"] == "cgroup_io_stat"
            else result["disk_process_lower_bound"]["write_bytes"]
        )
        assert result["cpu_seconds"] > 0
        assert result["pss_peak_mib"] >= 8
        assert writes >= 8 * MIB
        print(f"Measured CPU, PSS, and at least {writes / MIB:.1f} MiB of write I/O.")
        process.wait(timeout=4)
        directory = fixture(Path(temporary) / "fixture")
        assert verify([directory]) == [{"fixture": "fixture", "passed": False}]
        (directory / "intervals.py").write_text(
            "def merge_intervals(intervals):\n"
            "    merged = []\n"
            "    for start, end in sorted(intervals):\n"
            "        if start > end: raise ValueError()\n"
            "        if merged and start <= merged[-1][1]:\n"
            "            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))\n"
            "        else:\n"
            "            merged.append((start, end))\n"
            "    return merged\n"
        )
        assert verify([directory]) == [{"fixture": "fixture", "passed": True}]
    print("Verified that the fixture fails before the fix and passes afterward.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="Open CodeLithe with a separate profile for sign-in")
    commands.add_parser("self-test", help="Check the measurements with a small test process")
    commands.add_parser("report", help="Show and combine recorded runs")
    bench = commands.add_parser("run", help="Start an interactive benchmark run")
    bench.add_argument("app", choices=("chatgpt", "codelithe"))
    bench.add_argument("--model", help="Record the model selected in the app")
    bench.add_argument("--effort", help="Record the reasoning effort selected in the app")
    bench.add_argument("--idle-seconds", type=int, default=60)
    bench.add_argument("--warmup", action="store_true", help="Mark this run as a warm-up")
    args = parser.parse_args()
    try:
        private_directory(DATA)
        if args.command == "setup":
            subprocess.run(command_for("codelithe"), check=True)
        elif args.command == "report":
            show_report()
        elif args.command == "self-test":
            self_test()
        else:
            if args.idle_seconds < 1:
                parser.error("--idle-seconds must be at least 1.")
            if not sys.stdin.isatty():
                parser.error("run requires an interactive terminal.")
            run(args)
    except (OSError, EOFError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Benchmark stopped. {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "\nBenchmark canceled. Completed phases were saved.\n")


if __name__ == "__main__":
    main()
