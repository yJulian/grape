# GRAPE — gem5 aRea And Power Estimation

## Purpose

gem5 already estimates power for its objects via its own built-in power
model, but it isn't wired up by default, and gem5's model alone doesn't cover
everything (e.g. Garnet's on-chip network isn't a "cache", so DSENT still
does better there, and cache circuit-level numbers from Cacti are more
detailed than a hand-written power expression). The goal of this project is
to make gem5-based architecture power/area modeling for a RISC-V
MinorCPU + Ruby (+ optionally Garnet) system easy to get real numbers out of.

That is now the case in both directions: gem5's own power model is wired into
the running simulation, external tools (Cacti, DSENT) can be driven against a
completed run -- and Cacti is compiled into gem5 itself, so it runs
in-process and its area/energy numbers appear in `stats.txt` next to the
cache's own stats, with no separate script step:

```
system.cpu.dcache.cacti.area                 0.147408   # total cache area as modeled by Cacti (mm^2)
system.cpu.dcache.cacti.accessTime           0.417514   # Cacti access time of the cache (ns)
system.cpu.dcache.cacti.readEnergyPerAccess  0.042514   # Cacti dynamic energy of one read access (nJ)
system.cpu.dcache.cacti.leakagePower        43.434353   # Cacti leakage power of all banks together (mW)
system.cpu.dcache.cacti.readAccesses             1292   # read accesses counted by the modeled cache
system.cpu.dcache.cacti.writeAccesses            1015   # write accesses counted by the modeled cache
system.cpu.dcache.cacti.totalEnergy          1.595570   # dynamic plus leakage energy over the run (uJ)
system.cpu.dcache.cacti.averagePower        47.028114   # average total power over the run (mW)
```

The access counts are the cache's own, so the energies are those of the run
that just finished rather than of a hypothetical access pattern. Caches Cacti
cannot model -- it refuses anything below ~4KB, which includes gem5's 1KB
page-table walker caches -- report zeros and explain themselves in a warning,
instead of taking the simulation down with them.

## Current state

- `ext/cacti` — [HewlettPackard/cacti](https://github.com/HewlettPackard/cacti), readonly HTTPS submodule
- `ext/gem5` — [gem5/gem5](https://github.com/gem5/gem5) (`stable` branch), readonly HTTPS submodule
- `activate_environment.sh` — sourceable setup script that builds and caches Cacti, gem5,
  and DSENT (see below)
- `scripts/run_minor_ruby_power.py` — SE-mode run script (fork of gem5's deprecated
  `configs/deprecated/example/se.py`) that wires gem5's native `MathExprPowerModel`
  onto the CPU and Ruby cache controllers, using real CMOS-shaped power equations
  (`P_dyn = C·V²·f`, `P_static = V·I_leak0·2^((T-25)/10)`), so `stats.txt` reports
  actual per-power-state Watt figures instead of only power-state residency ticks.
  Works with any classic `--cpu-type`/`--ruby` combination se.py supports, including
  `RiscvMinorCPU` + Ruby + `--network garnet`.
- `scripts/garnet_power_from_m5out.py` — for runs that used Garnet as the Ruby network,
  estimates router/link power and area via DSENT (gem5's own NoC power model). A
  Python-3-clean rewrite of gem5's `util/on-chip-network-power-area.py`, which targets
  Python 2 and doesn't run as-is; see `scripts/dsent-py3-patch/README.md`.
- `gem5_extras/cacti/` — the in-simulation Cacti integration: a `CactiCache` SimObject
  compiled into gem5 through scons' `EXTRAS` mechanism (so `ext/gem5` stays pristine),
  linked against Cacti's own code as a library. It runs Cacti in-process at startup
  and reports the results — area, access/cycle time, per-access energy, leakage — as
  gem5 stats, plus the energy they imply for the accesses the run actually performed
  (per-access energy × the cache's own access counters, leakage × simulated time).
  See `gem5_extras/cacti/README.md` for how it hangs together.
- `scripts/attach_cacti.py` — hangs a `CactiCache` off every cache in a configuration,
  so a config script gets the above for free with one call. `run_minor_ruby_power.py`
  calls it by default.
- `scripts/cacti_from_m5out.py` — the same Cacti numbers for configs that don't attach
  the models: finds every cache in a completed run's `m5out/config.json` and runs the
  Cacti *binary* on each one. Both paths start from the same `cache.cfg` template and
  agree to the last digit.
- `scripts/run_gem5_and_report.py` — orchestrator: runs a gem5 config script end to end,
  then automatically runs whichever of the two post-hoc steps above apply (Garnet ->
  DSENT, caches present -> Cacti) and pulls the native power-model stats out of
  `stats.txt`, printing one consolidated report and writing it to `<outdir>/power_report.txt`.
- `scripts/garnet-ruby-power-integration.md` — the gotchas hit wiring all of the above up
  (ClockedObject-only power models, MathExpr quirks, DSENT path handling, ...), for
  adapting a different config script (topology/protocol/CPU) to the same tooling.

## Usage

```sh
source activate_environment.sh
```

First run builds Cacti (~10s, cached in `.tools/cacti/`, as both a binary and a
`libcacti.a` for gem5 to link against), `gem5.opt` for the RISCV ISA with the
Cacti integration compiled in (much longer, cached via a commit stamp in
`.tools/`, built in-place in `ext/gem5/build/RISCV/`), and DSENT (~10s, a patched
copy cached in `.tools/dsent/`). Later sources are no-ops unless the respective
submodule commit — or the code in `gem5_extras/` — changed.

This exposes:

- `cacti` — shell function wrapping the built Cacti binary (handles a Cacti quirk
  where it must be run from its own directory, see the comment in the script)
- `gem5` — shell function wrapping `ext/gem5/build/RISCV/gem5.opt`
- `$CACTI_HOME`, `$GEM5_HOME`, `$GEM5_BIN`, `$DSENT_MODULE_DIR`, `$DSENT_CONFIG_DIR`
- `$GRAPE_CACTI_SRC`, `$GRAPE_CACTI_LIB` — the Cacti headers and library the gem5
  build links against (read by `gem5_extras/cacti/SConscript`)

### One-shot: run + every applicable power/area report

```sh
python3 scripts/run_gem5_and_report.py scripts/run_minor_ruby_power.py -- \
    --cpu-type=RiscvMinorCPU --caches --ruby --network=garnet --topology=Mesh_XY --mesh-rows=1 \
    -c ext/gem5/tests/test-progs/hello/bin/riscv/linux/hello
```

Everything before `--` is the orchestrator's own options (`--outdir`, `--skip-dsent`,
`--skip-cacti`, ...; see `--help`); `--` and everything after it goes to the gem5 config
script untouched. This runs the simulation — which runs Cacti on every cache as part of
it — then runs DSENT if the network was Garnet, and writes a combined report to
`m5out/power_report.txt` as well as printing it.

### Running the steps individually

Run a simulation with a real power model wired up in place of gem5's own `se.py`,
and Cacti attached to every cache:

```sh
gem5 --outdir=m5out scripts/run_minor_ruby_power.py \
    --cpu-type=RiscvMinorCPU --caches --ruby --network=garnet --topology=Mesh_XY --mesh-rows=1 \
    -c ext/gem5/tests/test-progs/hello/bin/riscv/linux/hello
```

Cacti's numbers are then in `m5out/stats.txt` under each cache, as `<cache>.cacti.*`.
The circuit-level assumptions gem5 has no opinion about are options of the run script:
`--cacti-tech-nm`, `--cacti-temperature`, `--cacti-cache-type`, `--cacti-access-mode`,
`--cacti-ports`, and `--no-cacti` to leave the models off entirely.

Then, as needed:

```sh
# Router/link power+area for a Garnet run
python3 scripts/garnet_power_from_m5out.py m5out

# Cacti estimates for a run whose config script didn't attach the models
python3 scripts/cacti_from_m5out.py m5out
```

See `python3 scripts/cacti_from_m5out.py --help` for its options (technology node,
temperature, cache type, access mode, ...).

### Cacti in a config script of your own

```python
from attach_cacti import attach_cacti   # scripts/ must be on sys.path

root = Root(full_system=False, system=system)
attach_cacti(system, tech_nm=32, temperature=360)   # before m5.instantiate()
```

`attach_cacti` finds every cache under `system` (classic or Ruby) and gives each one a
`CactiCache` child wired to that cache's own access counters. Instantiate `CactiCache`
directly instead if you want to model one specific array; see
`gem5_extras/cacti/CactiCache.py` for its parameters.

## Roadmap

- [x] Cacti buildable standalone
- [x] gem5 buildable standalone (RISCV/opt)
- [x] Post-hoc Cacti analysis of a completed gem5 run's caches
- [x] Native gem5 power model wired up for Minor CPU + Ruby caches
- [x] Garnet NoC power/area via DSENT
- [x] One-shot run+report orchestrator
- [x] Call Cacti directly from gem5's C++ code so Cacti-derived numbers show up
      next to gem5's own stats, without a separate script step

## Repo layout

- `ext/` — pristine, readonly clones of external projects (git submodules)
- `gem5_extras/` — code compiled *into* gem5 via `scons EXTRAS=...`, kept here rather
  than in `ext/gem5` so that checkout stays pristine
- `.tools/` — generated build state (gitignored): Cacti's build copy, gem5's build-commit
  stamp, DSENT's patched build copy
- `cacti_runs/` — generated Cacti configs/results (gitignored)
- `m5out/` — gem5 simulation output (gitignored)
- `scripts/` — helper tooling
