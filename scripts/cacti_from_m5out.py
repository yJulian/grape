#!/usr/bin/env python3
"""Find every cache in a gem5 m5out/config.json and run Cacti on each one.

Requires the Cacti environment to be active (`source activate_environment.sh`
from the repo root), which sets $CACTI_HOME.
"""

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACTI_TEMPLATE = REPO_ROOT / "ext" / "cacti" / "cache.cfg"

GEM5_DEFAULT_BLOCK_SIZE = 64


@dataclass
class CacheInfo:
    path: str
    type: str
    size: int
    assoc: int
    block_size: int
    banks: int


def find_caches(config: dict) -> list[CacheInfo]:
    """Walk config.json and collect every SimObject that looks like a cache.

    gem5 identifies caches by name/type in inconsistent ways (classic "Cache",
    Ruby "RubyCache", protocol-specific controller types, ...), but both the
    classic and Ruby cache-storage objects always carry both "size" and
    "assoc" params, so that combination is used as the detection signal
    instead of matching on "type".
    """
    caches = []

    def block_size_from_context(stack: list[dict]) -> int:
        for ctx in reversed(stack):
            if ctx.get("block_size_bytes"):
                return ctx["block_size_bytes"]
            if ctx.get("cache_line_size"):
                return ctx["cache_line_size"]
        return GEM5_DEFAULT_BLOCK_SIZE

    def walk(obj, path: str, stack: list[dict], inside_cache: bool):
        if isinstance(obj, dict):
            size, assoc = obj.get("size"), obj.get("assoc")
            is_cache = isinstance(size, int) and isinstance(assoc, int)
            # A classic cache holds a tags object, which in turn holds an
            # indexing policy, and all three repeat the same geometry -- as
            # does a CactiCache model of the cache, if the run attached one
            # (see scripts/attach_cacti.py). Only the outermost of those is
            # the cache; counting the rest would report it several times
            # over, each time with its full area and energy.
            if is_cache and not inside_cache:
                block_size = obj.get("block_size") or obj.get("blk_size") or block_size_from_context(stack)
                banks = obj.get("dataArrayBanks") or 1
                caches.append(CacheInfo(
                    path=path,
                    type=obj.get("type", "unknown"),
                    size=size,
                    assoc=assoc,
                    block_size=block_size,
                    banks=banks,
                ))
            stack = stack + [obj]
            for key, value in obj.items():
                walk(value, f"{path}.{key}" if path else key, stack,
                     inside_cache or is_cache)
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                walk(value, f"{path}[{i}]", stack, inside_cache)

    walk(config, "", [config], False)
    return caches


# Maps CLI/derived option name -> regex matching the *active* (uncommented)
# template line to overwrite. Each pattern captures the "-key ... " prefix in
# group 1 so the value can be swapped in without hand-writing a whole cfg.
CFG_PATTERNS = {
    "size": (re.compile(r'^(-size \(bytes\)\s+)\d+'), "{}"),
    "block_size": (re.compile(r'^(-block size \(bytes\)\s+)\d+'), "{}"),
    "assoc": (re.compile(r'^(-associativity\s+)\d+'), "{}"),
    "banks": (re.compile(r'^(-UCA bank count\s+)\d+'), "{}"),
    "technology": (re.compile(r'^(-technology \(u\)\s+)[\d.]+'), "{}"),
    "temperature": (re.compile(r'^(-operating temperature \(K\)\s+)\d+'), "{}"),
    "ports": (re.compile(r'^(-read-write port\s+)\d+'), "{}"),
    "cache_type": (re.compile(r'^(-cache type\s+)"[^"]*"'), '"{}"'),
    "access_mode": (re.compile(r'^(-access mode \(normal, sequential, fast\) - )"[^"]*"'), '"{}"'),
}


def render_config(overrides: dict) -> str:
    lines = CACTI_TEMPLATE.read_text().splitlines()
    out = []
    for line in lines:
        if line.startswith("//"):
            out.append(line)
            continue
        for key, (pattern, value_fmt) in CFG_PATTERNS.items():
            if key not in overrides:
                continue
            m = pattern.match(line)
            if m:
                line = m.group(1) + value_fmt.format(overrides[key])
                break
        out.append(line)
    return "\n".join(out) + "\n"


METRIC_PATTERNS = {
    "Access time (ns)": r"Access time \(ns\):\s*([\d.eE+-]+)",
    "Cycle time (ns)": r"Cycle time \(ns\):\s*([\d.eE+-]+)",
    "Dynamic read energy/access (nJ)": r"Total dynamic read energy per access \(nJ\):\s*([\d.eE+-]+)",
    "Dynamic write energy/access (nJ)": r"Total dynamic write energy per access \(nJ\):\s*([\d.eE+-]+)",
    "Leakage power/bank (mW)": r"Total leakage power of a bank \(mW\):\s*([\d.eE+-]+)",
    "Gate leakage power/bank (mW)": r"Total gate leakage power of a bank \(mW\):\s*([\d.eE+-]+)",
}
AREA_PATTERN = re.compile(r"Cache height x width \(mm\):\s*([\d.eE+-]+) x ([\d.eE+-]+)")


def parse_metrics(cacti_output: str) -> dict:
    metrics = {}
    for label, pattern in METRIC_PATTERNS.items():
        m = re.search(pattern, cacti_output)
        if m:
            metrics[label] = float(m.group(1))
    m = AREA_PATTERN.search(cacti_output)
    if m:
        metrics["Area h x w (mm)"] = (float(m.group(1)), float(m.group(2)))
    return metrics


def run_cacti(cacti_home: Path, cfg_path: Path) -> tuple[int, str]:
    import subprocess
    proc = subprocess.run(
        [str(cacti_home / "cacti"), "-infile", str(cfg_path.resolve())],
        cwd=cacti_home,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def safe_filename(cache_path: str) -> str:
    return re.sub(r"[^\w.-]", "_", cache_path.lstrip("."))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("m5out", nargs="?", default="m5out", type=Path,
                         help="Path to the gem5 m5out directory (default: ./m5out)")
    parser.add_argument("--outdir", type=Path, default=Path("cacti_runs"),
                         help="Directory for generated .cfg/.out files (default: cacti_runs)")
    parser.add_argument("--tech-nm", type=float, default=32,
                         help="Technology node in nm (default: 32). gem5's timing-only "
                              "cache model doesn't record a process node, so this is an "
                              "assumption independent of the m5out config.")
    parser.add_argument("--temperature", type=int, default=360,
                         help="Operating temperature in Kelvin (default: 360)")
    parser.add_argument("--cache-type", choices=["cache", "ram", "main memory"], default="cache",
                         help="Cacti array type (default: cache)")
    parser.add_argument("--access-mode", choices=["normal", "sequential", "fast"], default="normal",
                         help="Cacti tag/data access mode (default: normal)")
    parser.add_argument("--ports", type=int, default=1, help="Read/write ports (default: 1)")
    parser.add_argument("--cacti-home", type=Path, default=None,
                         help="Cacti build directory (default: $CACTI_HOME from activate_environment.sh)")
    parser.add_argument("--only", help="Only run caches whose config path contains this substring")
    parser.add_argument("--list", action="store_true", help="List detected caches and exit, without running cacti")
    parser.add_argument("--dry-run", action="store_true", help="Write .cfg files but don't invoke cacti")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print full cacti output, not just the summary")
    args = parser.parse_args()

    config_path = args.m5out / "config.json"
    if not config_path.is_file():
        parser.error(f"no config.json found at {config_path}")

    caches = find_caches(json.loads(config_path.read_text()))
    if args.only:
        caches = [c for c in caches if args.only in c.path]

    if not caches:
        print(f"No caches found in {config_path}" + (f" matching '{args.only}'" if args.only else ""), file=sys.stderr)
        return 1

    print(f"Found {len(caches)} cache(s) in {config_path}:")
    for c in caches:
        print(f"  {c.path}  ({c.type}: {c.size}B, {c.assoc}-way, {c.block_size}B line, {c.banks} bank(s))")
    print()

    if args.list:
        return 0

    cacti_home = args.cacti_home or (Path(os.environ["CACTI_HOME"]) if "CACTI_HOME" in os.environ else None)
    if cacti_home is None and not args.dry_run:
        parser.error("$CACTI_HOME is not set. Run 'source activate_environment.sh' first, "
                      "or pass --cacti-home, or use --dry-run.")

    args.outdir.mkdir(parents=True, exist_ok=True)

    exit_code = 0
    for cache in caches:
        overrides = {
            "size": cache.size,
            "block_size": cache.block_size,
            "assoc": cache.assoc,
            "banks": cache.banks,
            "technology": args.tech_nm / 1000,
            "temperature": args.temperature,
            "ports": args.ports,
            "cache_type": args.cache_type,
            "access_mode": args.access_mode,
        }
        cfg_text = render_config(overrides)
        cfg_path = args.outdir / f"{safe_filename(cache.path)}.cfg"
        cfg_path.write_text(cfg_text)

        print(f"=== {cache.path} -> {cfg_path.name} ===")
        if args.dry_run:
            continue

        returncode, output = run_cacti(cacti_home, cfg_path)
        out_path = cfg_path.with_suffix(".out")
        out_path.write_text(output)

        if returncode != 0:
            exit_code = 1
            print(f"  cacti failed (exit {returncode}), see {out_path}")
            print("  " + "\n  ".join(output.strip().splitlines()[-10:]))
            print()
            continue

        if args.verbose:
            print(output)
        else:
            metrics = parse_metrics(output)
            for label, value in metrics.items():
                if label == "Area h x w (mm)":
                    print(f"  {label}: {value[0]:.6g} x {value[1]:.6g}")
                else:
                    print(f"  {label}: {value:.6g}")
            print(f"  (full output: {out_path})")
        print()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
