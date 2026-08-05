#!/usr/bin/env python3
"""Run a gem5 config script end-to-end and report every power/area number
this repo knows how to get out of the result, in one shot.

Runs gem5 on the given config script, then automatically:
  - pulls any native power_model.dynamicPower/staticPower stats straight out
    of stats.txt (only present if the config script wired up a power model,
    e.g. scripts/run_minor_ruby_power.py does)
  - pulls the in-simulation Cacti stats out of stats.txt, if the config script
    attached CactiCache models (scripts/attach_cacti.py, which
    scripts/run_minor_ruby_power.py calls by default)
  - runs scripts/garnet_power_from_m5out.py (DSENT) if the run used Garnet
  - runs scripts/cacti_from_m5out.py if the run has caches but no
    in-simulation Cacti stats (the two compute the same numbers; the external
    step is the fallback for configs that don't attach the models)

Requires the environment to be active (`source activate_environment.sh` from
the repo root) -- gem5 to run the sim, and DSENT/Cacti for whichever of the
two post-hoc steps apply.

The combined report is both printed to stdout and written to
<outdir>/power_report.txt.

Usage:
  python3 scripts/run_gem5_and_report.py <gem5-config-script> [wrapper options] \\
      -- <args passed through to the config script>

Example:
  python3 scripts/run_gem5_and_report.py scripts/run_minor_ruby_power.py \\
      -- --cpu-type=RiscvMinorCPU --caches --ruby --network=garnet \\
         --topology=Mesh_XY --mesh-rows=1 \\
         -c ext/gem5/tests/test-progs/hello/bin/riscv/linux/hello

Everything before "--" is this wrapper's own options (see --help); "--" and
everything after it goes to the gem5 config script untouched, so its own
flags (--cpu-type, --ruby, --network, -c, ...) never collide with this
wrapper's.
"""

import argparse
import configparser
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GARNET_SCRIPT = REPO_ROOT / "scripts" / "garnet_power_from_m5out.py"
CACTI_SCRIPT = REPO_ROOT / "scripts" / "cacti_from_m5out.py"

POWER_MODEL_RE = re.compile(
    r"^([\w.]+\.power_model)\.(dynamicPower|staticPower)\s+([0-9.eE+-]+)",
    re.MULTILINE,
)

# Stats of the CactiCache SimObjects attached by scripts/attach_cacti.py,
# which gem5 dumps as "<cache>.cacti.<stat>".
CACTI_STAT_RE = re.compile(
    r"^([\w.]+)\.cacti\.(\w+)\s+([0-9.eE+-]+)", re.MULTILINE
)

# (stat, label, unit): what to show for each cache, in this order. The stats
# are already in these units, see gem5_extras/cacti/cacti_cache.hh.
CACTI_REPORT_FIELDS = [
    ("area", "Area", "mm^2"),
    ("accessTime", "Access time", "ns"),
    ("cycleTime", "Cycle time", "ns"),
    ("readEnergyPerAccess", "Read energy/access", "nJ"),
    ("writeEnergyPerAccess", "Write energy/access", "nJ"),
    ("leakagePower", "Leakage power", "mW"),
    ("readAccesses", "Read accesses", ""),
    ("writeAccesses", "Write accesses", ""),
    ("dynamicEnergy", "Dynamic energy", "uJ"),
    ("leakageEnergy", "Leakage energy", "uJ"),
    ("totalEnergy", "Total energy", "uJ"),
    ("averagePower", "Average power", "mW"),
]


def split_passthrough_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv on the first "--" into (wrapper args, gem5-script args)."""
    if "--" in argv:
        idx = argv.index("--")
        return argv[:idx], argv[idx + 1 :]
    return argv, []


def run_gem5(gem5_bin: str, outdir: Path, gem5_script: Path, script_args: list[str]) -> int:
    cmd = [gem5_bin, f"--outdir={outdir}", str(gem5_script), *script_args]
    print("=== gem5 ===")
    print(f"$ {' '.join(cmd)}\n")
    # Streamed straight to the terminal (not captured) so simulation
    # progress/warnings show up live, same as running gem5 by hand.
    proc = subprocess.run(cmd)
    return proc.returncode


def find_power_model_stats(outdir: Path) -> dict[str, dict[str, float]]:
    stats_txt = outdir / "stats.txt"
    if not stats_txt.is_file():
        return {}
    results: dict[str, dict[str, float]] = {}
    for path, kind, value in POWER_MODEL_RE.findall(stats_txt.read_text()):
        results.setdefault(path, {})[kind] = float(value)
    return results


def find_cacti_stats(outdir: Path) -> dict[str, dict[str, float]]:
    """Cacti's in-simulation numbers, per cache, out of stats.txt."""
    stats_txt = outdir / "stats.txt"
    if not stats_txt.is_file():
        return {}
    results: dict[str, dict[str, float]] = {}
    for path, stat, value in CACTI_STAT_RE.findall(stats_txt.read_text()):
        results.setdefault(path, {})[stat] = float(value)
    return results


def format_cacti_stats(cacti_stats: dict[str, dict[str, float]]) -> list[str]:
    lines = []
    total_energy = 0.0
    total_area = 0.0
    for path, values in sorted(cacti_stats.items()):
        lines.append(f"{path}:")
        for stat, label, unit in CACTI_REPORT_FIELDS:
            if stat not in values:
                continue
            suffix = f" {unit}" if unit else ""
            lines.append(f"  {label + ':':<22}{values[stat]:.6g}{suffix}")
        total_area += values.get("area", 0.0)
        total_energy += values.get("totalEnergy", 0.0)
        lines.append("")
    lines.append(
        f"All caches: {total_area:.6g} mm^2, {total_energy:.6g} uJ total energy"
    )
    return lines


def network_type(outdir: Path) -> str | None:
    config_ini = outdir / "config.ini"
    if not config_ini.is_file():
        return None
    config = configparser.ConfigParser()
    config.read(config_ini)
    if not config.has_section("system.ruby.network"):
        return None
    return config.get("system.ruby.network", "type", fallback=None)


def run_subscript(script: Path, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("gem5_script", type=Path, help="gem5 config script to run")
    parser.add_argument(
        "--outdir", type=Path, default=Path("m5out"), help="gem5 output directory (default: m5out)"
    )
    parser.add_argument(
        "--gem5-bin", default=None, help="gem5 binary (default: $GEM5_BIN from activate_environment.sh)"
    )
    parser.add_argument("--skip-dsent", action="store_true", help="skip the DSENT/Garnet NoC step")
    parser.add_argument("--skip-cacti", action="store_true", help="skip the Cacti cache step")
    parser.add_argument(
        "--external-cacti",
        action="store_true",
        help="run cacti_from_m5out.py even when the run already reported "
             "Cacti stats from inside the simulation",
    )
    own_argv, script_argv = split_passthrough_args(sys.argv[1:])
    args = parser.parse_args(own_argv)

    gem5_bin = args.gem5_bin or os.environ.get("GEM5_BIN")
    if not gem5_bin:
        parser.error("$GEM5_BIN is not set. Run 'source activate_environment.sh' first, or pass --gem5-bin.")

    returncode = run_gem5(gem5_bin, args.outdir, args.gem5_script, script_argv)
    if returncode != 0:
        print(f"\ngem5 exited with code {returncode}; skipping post-processing.", file=sys.stderr)
        return returncode

    report_sections = []

    power_models = find_power_model_stats(args.outdir)
    section = ["## Native power model (from stats.txt)\n"]
    if power_models:
        for path, values in sorted(power_models.items()):
            dyn = values.get("dynamicPower")
            st = values.get("staticPower")
            section.append(f"{path}:")
            if dyn is not None:
                section.append(f"  dynamicPower: {dyn:.6g} W")
            if st is not None:
                section.append(f"  staticPower:  {st:.6g} W")
    else:
        section.append(
            "(none found -- the config script didn't wire up a power_model, "
            "or the run doesn't produce one; see scripts/run_minor_ruby_power.py "
            "for an example that does)"
        )
    report_sections.append("\n".join(section))

    cacti_stats = find_cacti_stats(args.outdir)
    section = ["\n## Cache power/area/timing (Cacti, from inside the simulation)\n"]
    if cacti_stats:
        section.extend(format_cacti_stats(cacti_stats))
    else:
        section.append(
            "(none found -- the config script didn't attach any CactiCache "
            "models; see scripts/attach_cacti.py)"
        )
    report_sections.append("\n".join(section))

    net_type = network_type(args.outdir)
    section = ["\n## Garnet NoC power/area (DSENT)\n"]
    if args.skip_dsent:
        section.append("(skipped: --skip-dsent)")
    elif net_type not in ("GarnetNetwork", "GarnetNetwork_d"):
        section.append(
            f"(skipped: network type is {net_type!r}, not Garnet -- nothing for DSENT to model)"
        )
    else:
        rc, output = run_subscript(GARNET_SCRIPT, [str(args.outdir)])
        print("=== DSENT (Garnet NoC) ===")
        print(output)
        section.append(output.strip() if rc == 0 else f"(garnet_power_from_m5out.py failed, exit {rc}):\n{output.strip()}")
    report_sections.append("\n".join(section))

    section = ["\n## Cache power/area/timing (Cacti, run externally)\n"]
    if args.skip_cacti:
        section.append("(skipped: --skip-cacti)")
    elif cacti_stats and not args.external_cacti:
        section.append(
            "(skipped: the simulation already ran Cacti itself, see above -- "
            "pass --external-cacti to run it again from outside)"
        )
    else:
        rc, output = run_subscript(CACTI_SCRIPT, [str(args.outdir)])
        print("=== Cacti (caches) ===")
        print(output)
        if rc == 0:
            section.append(output.strip())
        elif "No caches found" in output:
            section.append("(no caches found in this run)")
        else:
            section.append(f"(cacti_from_m5out.py failed, exit {rc}):\n{output.strip()}")
    report_sections.append("\n".join(section))

    report = (
        f"# Power/area report for {args.outdir}\n"
        f"# gem5 script: {args.gem5_script}"
        + (f" -- {' '.join(script_argv)}" if script_argv else "")
        + "\n\n"
        + "\n".join(report_sections)
        + "\n"
    )

    report_path = args.outdir / "power_report.txt"
    report_path.write_text(report)

    print("\n" + "=" * 70)
    print(report)
    print(f"(written to {report_path})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
