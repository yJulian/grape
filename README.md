# gem5 + Cacti Architecture Modeling

## Purpose

gem5 already estimates power for its objects via its own built-in power
model, but it isn't wired up by default, and gem5's model alone doesn't cover
everything (e.g. Garnet's on-chip network isn't a "cache", so DSENT still
does better there, and cache circuit-level numbers from Cacti are more
detailed than a hand-written power expression). The goal of this project is
to make gem5-based architecture power/area modeling for a RISC-V
MinorCPU + Ruby (+ optionally Garnet) system easy to get real numbers out
of -- both by wiring gem5's own power model into a running simulation, and
by driving external tools (Cacti, DSENT) against a completed run -- working
toward eventually calling Cacti directly from gem5's C++ code so its numbers
show up next to gem5's own stats without a separate script step.

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
- `scripts/cacti_from_m5out.py` — finds every cache in a completed run's `m5out/config.json`
  and runs Cacti on each one *externally*, as a stand-in for the eventual in-simulation
  integration.
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

First run builds Cacti (~10s, cached in `.tools/cacti/`), `gem5.opt` for the RISCV
ISA (much longer, cached via a commit stamp in `.tools/`, built in-place in
`ext/gem5/build/RISCV/`), and DSENT (~10s, a patched copy cached in `.tools/dsent/`).
Later sources are no-ops unless the respective submodule commit changed.

This exposes:

- `cacti` — shell function wrapping the built Cacti binary (handles a Cacti quirk
  where it must be run from its own directory, see the comment in the script)
- `gem5` — shell function wrapping `ext/gem5/build/RISCV/gem5.opt`
- `$CACTI_HOME`, `$GEM5_HOME`, `$GEM5_BIN`, `$DSENT_MODULE_DIR`, `$DSENT_CONFIG_DIR`

### One-shot: run + every applicable power/area report

```sh
python3 scripts/run_gem5_and_report.py scripts/run_minor_ruby_power.py -- \
    --cpu-type=RiscvMinorCPU --caches --ruby --network=garnet --topology=Mesh_XY --mesh-rows=1 \
    -c ext/gem5/tests/test-progs/hello/bin/riscv/linux/hello
```

Everything before `--` is the orchestrator's own options (`--outdir`, `--skip-dsent`,
`--skip-cacti`, ...; see `--help`); `--` and everything after it goes to the gem5 config
script untouched. This runs the simulation, then automatically runs DSENT (if the network
was Garnet) and Cacti (if there are caches), and writes a combined report to
`m5out/power_report.txt` as well as printing it.

### Running the steps individually

Run a simulation with a real power model wired up in place of gem5's own `se.py`:

```sh
gem5 --outdir=m5out scripts/run_minor_ruby_power.py \
    --cpu-type=RiscvMinorCPU --caches --ruby --network=garnet --topology=Mesh_XY --mesh-rows=1 \
    -c ext/gem5/tests/test-progs/hello/bin/riscv/linux/hello
```

Then, as needed:

```sh
# Router/link power+area for a Garnet run
python3 scripts/garnet_power_from_m5out.py m5out

# Cacti estimates for every cache the run used
python3 scripts/cacti_from_m5out.py m5out
```

See `python3 scripts/cacti_from_m5out.py --help` for its options (technology node,
temperature, cache type, access mode, ...).

## Roadmap

- [x] Cacti buildable standalone
- [x] gem5 buildable standalone (RISCV/opt)
- [x] Post-hoc Cacti analysis of a completed gem5 run's caches
- [x] Native gem5 power model wired up for Minor CPU + Ruby caches
- [x] Garnet NoC power/area via DSENT
- [x] One-shot run+report orchestrator
- [ ] Call Cacti directly from gem5's C++ code so Cacti-derived numbers show up
      next to gem5's own stats, without a separate script step

## Repo layout

- `ext/` — pristine, readonly clones of external projects (git submodules)
- `.tools/` — generated build state (gitignored): Cacti's build copy, gem5's build-commit
  stamp, DSENT's patched build copy
- `cacti_runs/` — generated Cacti configs/results (gitignored)
- `m5out/` — gem5 simulation output (gitignored)
- `scripts/` — helper tooling
