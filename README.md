# gem5 + Cacti Architecture Modeling

## Purpose

gem5 already estimates power for its objects via its own built-in power
model. The goal of this project is to additionally wire **Cacti** directly
into the gem5 simulation itself, so that area/power/timing estimates for
objects like caches can be computed with Cacti's circuit-level models
*during* (or immediately after) a gem5 run — alongside gem5's native stats —
instead of only as a separate, external post-processing step.

## Current state

This repo is currently the stepping-stone stage toward that goal:

- `ext/cacti` — [HewlettPackard/cacti](https://github.com/HewlettPackard/cacti), readonly HTTPS submodule
- `ext/gem5` — [gem5/gem5](https://github.com/gem5/gem5) (`stable` branch), readonly HTTPS submodule
- `activate_environment.sh` — sourceable setup script that builds and caches both tools
- `scripts/cacti_from_m5out.py` — finds every cache in a completed run's `m5out/config.json`
  and runs Cacti on each one *externally*, as a stand-in for the eventual in-simulation
  integration

## Usage

```sh
source activate_environment.sh
```

First run builds Cacti (~10s, cached in `.tools/cacti/`) and `gem5.opt` for the RISCV
ISA (much longer, cached via a commit stamp in `.tools/`, built in-place in
`ext/gem5/build/RISCV/`). Later sources are no-ops unless the respective submodule
commit changed.

This exposes:

- `cacti` — shell function wrapping the built Cacti binary (handles a Cacti quirk
  where it must be run from its own directory, see the comment in the script)
- `gem5` — shell function wrapping `ext/gem5/build/RISCV/gem5.opt`
- `$CACTI_HOME`, `$GEM5_HOME`, `$GEM5_BIN`

Run a simulation as usual (produces an `m5out/` directory), then get Cacti estimates
for every cache it used:

```sh
python3 scripts/cacti_from_m5out.py m5out
```

See `python3 scripts/cacti_from_m5out.py --help` for options (technology node,
temperature, cache type, access mode, ...).

## Roadmap

- [x] Cacti buildable standalone
- [x] gem5 buildable standalone (RISCV/opt)
- [x] Post-hoc Cacti analysis of a completed gem5 run's caches
- [ ] Call Cacti directly from gem5's C++ code so Cacti-derived numbers show up
      next to gem5's own stats, without a separate script step

## Repo layout

- `ext/` — pristine, readonly clones of external projects (git submodules)
- `.tools/` — generated build state (gitignored): Cacti's build copy, gem5's build-commit stamp
- `cacti_runs/` — generated Cacti configs/results (gitignored)
- `m5out/` — gem5 simulation output (gitignored)
- `scripts/` — helper tooling
