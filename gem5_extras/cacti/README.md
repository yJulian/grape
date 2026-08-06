# Cacti inside gem5

`CactiCache` is a gem5 SimObject that runs Cacti in-process and reports its
results as gem5 stats. Attach one to a cache (`scripts/attach_cacti.py` does it
for every cache in a configuration) and `stats.txt` gains, under that cache:

| stat | what it is | unit |
| --- | --- | --- |
| `area`, `height`, `width` | Cacti's cache geometry | mm², mm |
| `accessTime`, `cycleTime` | Cacti's timing for the array | ns |
| `readEnergyPerAccess`, `writeEnergyPerAccess` | Cacti's per-access dynamic energy | nJ |
| `leakagePowerPerBank`, `gateLeakagePowerPerBank`, `leakagePower` | Cacti's leakage (`leakagePower` = per bank × banks) | mW |
| `readAccesses`, `writeAccesses` | what the cache itself counted | — |
| `dynamicReadEnergy`, `dynamicWriteEnergy`, `dynamicEnergy` | accesses × per-access energy | µJ |
| `leakageEnergy` | `leakagePower` × simulated seconds | µJ |
| `totalEnergy` | dynamic + leakage | µJ |
| `dynamicPower`, `averagePower` | the run's average power | mW |

The first four rows are Cacti's own output for the array's geometry; the rest
are what that implies for the run gem5 just simulated.

The units are Cacti's own reporting units rather than SI, because gem5 writes
`stats.txt` with six digits after the decimal point: an access time of 345 ps
and a per-access energy of 36 pJ would both come out as `0.000000` in seconds
and joules. Each stat's description in `stats.txt` names its unit.

## How it is built

gem5 is built with `scons EXTRAS=<repo>/gem5_extras/cacti`, which makes scons
read this directory's `SConscript` as if it were part of `src/`
(`activate_environment.sh` passes it). That keeps `ext/gem5` a pristine
checkout: nothing here is a patch against gem5.

Cacti is linked in as a static library. `activate_environment.sh` builds
`libcacti.a` from Cacti's sources — the same objects as its binary, minus its
`main()`, compiled `-fPIC` — and exports `$GRAPE_CACTI_SRC` (headers) and
`$GRAPE_CACTI_LIB` (the library) for the `SConscript` to pick up.

- `cacti_runner.{hh,cc}` — the only translation unit that sees Cacti's headers.
  They declare a lot of unqualified globals with very generic names (`Wire`,
  `Component`, `g_ip`, ...) that have no business being visible to the rest of
  gem5, and Cacti is from 2008 and does not survive gem5's warning flags, so
  this file is compiled with warnings off and talks to the rest of the world
  through POD structs.
- `cacti_cache.{hh,cc}`, `CactiCache.py` — the SimObject.

## Getting the same numbers as the Cacti binary

`cacti_runner.cc` does not call Cacti's file-based entry point: that one parses
a `.cfg`, prints a full report to stdout, and `exit()`s the process on a bad
input, none of which is wanted inside a simulator. It uses the same internals
Cacti's own `main()` does (`parse_cfg` → `error_checking` → `init_tech_params`
→ `solve`), with gem5's cache geometry overriding what the template `.cfg`
said.

Two details make the results match `cacti -infile ...` exactly rather than
approximately:

1. **`InputParameter` has to be zeroed *and* constructed.** Its constructor
   initializes 7 of ~120 fields and `parse_cfg()` only fills in what the `.cfg`
   mentions; the rest is whatever was in memory. Cacti's binary gets zeros
   there by accident, and it depends on them — zeroing without running the
   constructor clears `cl_vertical`, and Cacti then settles on a different
   cache organization (2% off on access time, 40% off on the aspect ratio).
   Zeroing the storage and then constructing in place reproduces the binary's
   state exactly.
2. **Cacti must be run from its own directory.** It loads technology data
   through paths relative to the process's cwd (`tech_params/32nm.dat`), so the
   runner `chdir()`s into `$CACTI_HOME` for the duration of the call and back
   out afterwards — the same quirk the `cacti` shell wrapper in
   `activate_environment.sh` works around.

Both this and `scripts/cacti_from_m5out.py` start from Cacti's own
`cache.cfg` as the template, so the in-simulation and external paths report
identical numbers for identical caches.

## Where the access counts come from

Cacti gives energy per access; the cache knows how many accesses there were.
`stat_target` names the object to read them from and `read_access_stats` /
`write_access_stats` name the stats, which are resolved by name
(`statistics::Group::resolveStat`) when the simulation starts and summed at
every dump. Scalars, vectors and formulas all work, so a classic cache's
per-command `ReadReq.accesses` formulas and a Ruby cache's plain
`numDataArrayReads` counter are both usable.

Ruby's simpler protocols (MI_example, ...) never touch
`numDataArrayReads`/`numDataArrayWrites` — they don't model the tag and data
arrays as separate resources — and only count whole accesses. Those go in
`fallback_access_stats`, which is charged at read energy, but only if the
read/write stats found nothing.

The stats are `statistics::Value`s bound to member variables rather than
`Scalar`s that get assigned, so that a stats reset partway through a run
(`m5.stats.reset()`, `--warmup-insts`) doesn't wipe Cacti's constants.
