# Wiring power models + DSENT into a Garnet/Ruby gem5 config script

Notes for whoever (agent or human) adapts another gem5 config script -- a
different topology, protocol, CPU count, workload, whatever -- to work with
this repo's power tooling (`scripts/run_minor_ruby_power.py` and
`scripts/garnet_power_from_m5out.py`). Everything below was found the hard
way while getting a `RiscvMinorCPU` + Ruby (`MI_example`) + Garnet run
working; it applies to any classic (`configs/deprecated/example/se.py`
-style) config, not just that one.

Reference numbers from the known-good repro (RISCV, `RiscvMinorCPU`, Ruby
`MI_example` protocol, Garnet, 1x1 Mesh_XY, running the stock
`tests/test-progs/hello/bin/riscv/linux/hello` binary):

```
system.cpu.power_model.dynamicPower               0.004335 W
system.cpu.power_model.staticPower                0.005000 W
system.ruby.l1_cntrl0.power_model.dynamicPower     0.000087 W
system.ruby.l1_cntrl0.power_model.staticPower      0.000500 W

DSENT (router, 1 router, 4 ports incl. loopback):
  Total dynamic power: 2.43262e-09 W
  Total leakage power: 0.0205403  W
  Area: ~3.4e-08 m^2 (buffer+crossbar+switch-alloc+other)
DSENT (per link direction, x4 links x2 directions):
  Dynamic power: 3.87476e-10 W
  Leakage power: 2.77357e-05 W
```

These are the actual, verified outputs of both scripts against a real run --
not placeholders. The CPU/cache power numbers come from `MathExprPowerModel`
expressions shaped like real CMOS equations (`P_dyn = C*V^2*f`,
`P_static = V*I_leak0*2^((T-25)/10)`, see `scripts/run_minor_ruby_power.py`)
but with made-up constants (no chip characterization backs them) -- so the
*orders of magnitude* and *how they respond* to voltage/frequency/activity/
temperature are meaningful, the absolute numbers aren't. The DSENT numbers
are DSENT's real 45nm electrical model output, not illustrative.

## 1. gem5's native power model only attaches to `ClockedObject`s

`power_model` / `power_state` params live on `ClockedObject`
(`src/sim/ClockedObject.py`), not on plain `SimObject`. This matters because:

- `BaseCPU` is a `ClockedObject` -> attach directly.
- Ruby's cache *storage* object, `RubyCache`/`CacheMemory`
  (`src/mem/ruby/structures/RubyCache.py`), is a plain `SimObject` -- it has
  **no** `power_model` param. Trying to set `.power_model` on it raises an
  `AttributeError`.
- The Ruby *controller* that owns a `RubyCache` (e.g.
  `MI_example_L1Cache_Controller`, generically any `RubyController` /
  `AbstractController` subclass, `src/mem/ruby/slicc_interface/Controller.py`)
  **is** a `ClockedObject`. Attach the power model there instead, and write
  expressions that reach into `<controller>.cacheMemory.<stat>` for the
  actual numbers.
- To find cache-holding controllers generically across protocols (not just
  `MI_example`), filter on `isinstance(obj, m5.objects.RubyController) and
  hasattr(obj, "cacheMemory")` -- every protocol's L1/L2 controller SLICC
  class exposes a `cacheMemory` param, but directory/DMA controllers don't.

## 2. `PowerModel.subsystem` needs an explicit `SubSystem`

`PowerModel.subsystem` (`src/sim/power/PowerModel.py`) defaults to a
`Parent.any` proxy that walks up the object tree looking for a `SubSystem`
ancestor. gem5's own example (`configs/example/arm/fs_power.py`) only works
because `fs_bigLITTLE.py` wraps each CPU cluster in an actual `SubSystem`
object. A plain `se.py`-style system has no `SubSystem` anywhere, so the
proxy fails to resolve at `m5.instantiate()` time:

```
AttributeError: Can't resolve proxy 'any' of type 'SubSystem' from 'system.cpu.power_model'
```

Fix: create one `SubSystem` yourself and pass it explicitly to every
`PowerModel(...)` you construct:

```python
system.power_subsystem = m5.objects.SubSystem()
...
obj.power_model = SomePowerModel(obj.path(), subsystem=system.power_subsystem)
```

## 3. `obj.path()` must be called *after* `Root()` is constructed

Before a `System` is attached to a `Root` SimObject, `SimObject.path()`
returns a placeholder like `<orphan System>.cpu` instead of `system.cpu`.
`MathExprPowerModel`'s expression grammar only accepts
`[A-Za-z0-9.$\]+` as variable-name characters (`src/sim/mathexpr.cc`), so the
`<`, `>` and space in that placeholder make the whole expression
unparseable, and gem5 aborts at instantiate time with:

```
panic: panic condition !root occurred: Invalid expression
```

This is silent about *why* -- there's no indication the path itself was the
problem. Symptom is a `mathexpr.cc:67` panic inside
`MathExprPowerModelParams::create()`. If you hit this, check the actual
`.path()` strings your expressions were built from (they're baked into the
`dyn`/`st` param strings at Python-construction time, before the panic).

Fix: build the system, create `root = Root(full_system=False, system=system)`
*first*, and only then walk `system.descendants()` and call `.path()` /
attach power models. Do this last, right before `Simulation.run(...)`.

## 4. Not every stat name in the C++ header is actually populated

`CacheMemory` (`src/mem/ruby/structures/CacheMemory.hh`) declares
`numDataArrayReads` / `numDataArrayWrites` stats, which look like the
obvious per-access driver for a cache power expression. In practice they
stay `0` for protocols (like `MI_example`) that don't model the data/tag
arrays as separate resources -- the expression evaluates fine (MathExpr
treats a stat as `0` if not written to, no error), it just always reports
`0` dynamic power, silently.

Use `m_demand_accesses` instead (`hits + misses`, a `statistics::Formula`
that every protocol populates) unless you've confirmed the protocol you're
using actually drives the data/tag-array counters.

## 5. `clock_period` (the automatic MathExpr variable) is in raw Ticks, not seconds

`MathExprPowerModel` exposes three automatic variables --  `voltage`, `temp`
(degC), and `clock_period` (`src/sim/power/mathexpr_powermodel.cc`). Despite
the name, `clock_period` is `ClockedObject::clockPeriod()`
(`src/sim/clocked_object.hh`), which returns a raw `Tick` count (e.g. `500`
for a 2 GHz clock when the global tick resolution is 1 ps), *not* a value in
seconds. Writing `some_rate_stat / clock_period` expecting a real per-second
rate silently produces a number ~1e9-1e12x too small (it doesn't error --
MathExpr just evaluates the wrong arithmetic), which rounds to `0.000000` in
`stats.txt`'s 6-decimal formatting and looks like the stat itself is zero.

Fix: also reference the `simFreq` stat (ticks/second, a root-level scalar --
same "no `system.` prefix" quirk as `simSeconds`, see `#7` below), and cancel
the tick units out explicitly:

```python
# instructions/second = ipc [instr/cycle] * simFreq [ticks/s] / clock_period [ticks/cycle]
self.dyn = f"{C_EFF} * voltage^2 * {cpu_path}.ipc * simFreq / clock_period"
```

For a whole-run total stat (like Ruby's `m_demand_accesses`) that isn't
already a per-cycle rate the way `ipc` is, dividing by `simSeconds` directly
is simpler and avoids this trap entirely -- see `RubyCachePowerOn` in
`scripts/run_minor_ruby_power.py`.

## 6. Building the config script outside `ext/gem5`

`ext/` is a pristine, readonly gem5 checkout (see top-level README) -- new
run scripts don't live in `ext/gem5/configs/`. `configs/deprecated/example/se.py`
resolves gem5's `configs/common`/`configs/ruby` modules via
`addToPath("../../")`, relative to its own location inside the gem5 tree.
A script living in this repo's `scripts/` instead needs:

```python
addToPath(os.path.join(os.environ["GEM5_HOME"], "configs"))
```

(`GEM5_HOME` is exported by `activate_environment.sh`.) Everything else
about forking `se.py` (or any other classic config script) is copy-paste;
see `scripts/run_minor_ruby_power.py` for a full worked example, including
the CLI options (`Options.addCommonOptions`, `Options.addSEOptions`,
`Ruby.define_options`) that give you `--cpu-type`, `--ruby`,
`--network garnet`, `--topology`, etc. for free.

## 7. DSENT specifics (only relevant if the run uses Garnet)

Covered in depth in `scripts/dsent-py3-patch/README.md` and the top of
`scripts/garnet_power_from_m5out.py`; summarized here:

- gem5's bundled `ext/gem5/ext/dsent/interface.cc` only builds against the
  Python 2 C API. `activate_environment.sh` builds a ported copy into
  `.tools/dsent/` (source: `scripts/dsent-py3-patch/interface.cc`) instead
  of patching the submodule in place.
- `router.cfg`/`electrical-link.cfg` (DSENT's bundled configs, in
  `ext/gem5/ext/dsent/configs/`) reference their tech-model file via a path
  relative to the **gem5 checkout root** (`ext/dsent/tech/...`), so DSENT
  must be `initialize()`d with the process's cwd set to `$GEM5_HOME`, not
  this repo's root.
- gem5 25.1's `config.ini` uses `type=GarnetNetwork` (not the
  `GarnetNetwork_d` gem5's own `util/on-chip-network-power-area.py` checks
  for) and names a link's two directions `network_links0`/`network_links1`
  (not `<link>.nls0`/`<link>.nls1`). `garnet_power_from_m5out.py` already
  handles both; if DSENT support gets added to some *other* script, don't
  copy the naming from gem5's own (stale) utility.
- The stat gem5's own utility greps for, `sim_seconds`, was renamed to
  `simSeconds` at some point; same trap.
