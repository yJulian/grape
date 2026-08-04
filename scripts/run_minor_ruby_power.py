# Copyright (c) 2012-2013 ARM Limited
# Copyright (c) 2006-2008 The Regents of The University of Michigan
# All rights reserved.
#
# (License text identical to the upstream file this is forked from:
#  ext/gem5/configs/deprecated/example/se.py)
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution;
# neither the name of the copyright holders nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""se.py (SE-mode run script), forked to wire up gem5's native power model.

gem5's MathExprPowerModel only attaches to ClockedObject SimObjects. The
classic memory system's Cache is one; Ruby's cache *storage* SimObject
(RubyCache/CacheMemory) is not -- it's a plain SimObject held by a cache
controller (e.g. MI_example_L1Cache_Controller), and it's the *controller*
(a RubyController, which is a ClockedObject) that can actually carry a
power_model. So: any BaseCPU gets a CPU power model, and any RubyController
that holds a cache (has a "cacheMemory" param) gets a cache power model
whose expression reads that cache's own stats (m_demand_accesses).

The power *equations* are standard CMOS textbook ones (P_dyn = C*V^2*f,
P_static = V*I_leak0*2^((T-25)/10), see the "Power model constants" block
below for the full reasoning) -- but the constants that go into them
(effective capacitance per event, leakage current at 25 degC) are still
made up, same spirit as gem5's own configs/example/arm/fs_power.py, not
calibrated to real silicon.

Everything else is se.py unmodified, except addToPath, which se.py resolves
relative to its own location inside ext/gem5/configs/deprecated/example/;
here it's rebased onto $GEM5_HOME since this script lives outside that tree
(ext/ is kept a pristine, readonly gem5 checkout -- see top-level README).

Usage (after `source activate_environment.sh`):
  gem5 scripts/run_minor_ruby_power.py --cpu-type=RiscvMinorCPU --caches \\
      --ruby --network=garnet --topology=Mesh_XY --mesh-rows=1 \\
      -c ext/gem5/tests/test-progs/hello/bin/riscv/linux/hello
"""

import argparse
import os
import sys

import m5
from m5.defines import buildEnv
from m5.objects import *
from m5.params import NULL
from m5.util import (
    addToPath,
    fatal,
    warn,
)

from gem5.isas import ISA

addToPath(os.path.join(os.environ["GEM5_HOME"], "configs"))

from common import (
    CacheConfig,
    CpuConfig,
    MemConfig,
    ObjectList,
    Options,
    Simulation,
)
from common.Caches import *
from common.cpu2000 import *
from common.FileSystemConfig import config_filesystem
from ruby import Ruby


## Power model constants
##
## MathExprPowerModel only exposes three automatic variables -- "voltage",
## "temp" (degC) and "clock_period" (raw Tick count, *not* seconds -- see
## ClockedObject::clockPeriod() in src/sim/clocked_object.hh -- divide by
## the "simFreq" stat, ticks/second, to get a real period/frequency), see
## src/sim/power/mathexpr_powermodel.cc -- plus any stat reachable by its
## full path. No functions (exp/log/...) are available, just +-*/^ with
## parens, so the formulas below are standard CMOS textbook equations
## expressed in those primitives; the constants are still made-up (no real
## chip characterization backs them), just now dimensioned so the *shape* of
## the result is right: dynamic power tracks V^2*f and actual activity,
## static power tracks V and grows with temperature the way real subthreshold
## leakage does, instead of an arbitrary "3 * temp" number.
##
## Dynamic power: P_dyn = C_eff * V^2 * f
##   f is the actual event rate (instructions/s or accesses/s), not the raw
##   clock -- C_eff ("effective switched capacitance per event") already
##   folds in the activity factor alpha from the classic P=alpha*C*V^2*f,
##   since only committed instructions / demand accesses toggle gates.
##
## Static/leakage power: P_st = V * I_leak0 * 2^((T - 25) / 10)
##   The "leakage current roughly doubles every 10 degC" rule of thumb for
##   CMOS subthreshold leakage (exponential in temperature); 2^x is used
##   instead of e^x only because MathExpr has no exp(), the qualitative
##   behavior (and the choice of 10 degC as the doubling constant) is the
##   same idea. I_leak0 is the leakage current at 25 degC.

# ~20 pF effective switched capacitance/instruction -> ~20 pJ/instruction at
# 1V, in line with published energy-per-instruction figures for small
# in-order embedded cores.
CPU_C_EFF = "0.00000000002"
# 5 mA leakage current at 25 degC, 1V -> 5 mW static power at room temp for
# a single simple core (vs. the old placeholder's 75 W, which was leakage
# for a whole server-class chip, not one small in-order core).
CPU_I_LEAK0 = "0.005"

# ~0.5 pF effective switched capacitance/access -> ~0.5 pJ/access at 1V, in
# line with small L1 SRAM array access energies.
CACHE_C_EFF = "0.0000000000005"
# 0.5 mA leakage at 25 degC, 1V -> 0.5 mW static power: an order of
# magnitude below the core, roughly matching how much smaller an L1 cache's
# transistor count is.
CACHE_I_LEAK0 = "0.0005"

STATIC_POWER_EXPR = "voltage * {i_leak0} * 2^((temp - 25) / 10)"


class CpuPowerOn(MathExprPowerModel):
    def __init__(self, cpu_path, **kwargs):
        super().__init__(**kwargs)
        # f = ipc * simFreq / clock_period: ipc is instructions/cycle, and
        # simFreq/clock_period is cycles/second (both simFreq -- ticks/second
        # -- and clock_period -- ticks/cycle -- are in raw Tick units, see
        # ClockDomain::clockPeriod() in src/sim/clock_domain.hh, *not*
        # seconds, despite the "clock_period" automatic variable's name), so
        # the ticks cancel and this is instructions/second averaged over the
        # run (equivalent to committedInsts/simSeconds).
        self.dyn = (
            f"{CPU_C_EFF} * voltage^2 * {cpu_path}.ipc * simFreq / clock_period"
        )
        self.st = STATIC_POWER_EXPR.format(i_leak0=CPU_I_LEAK0)


class RubyCachePowerOn(MathExprPowerModel):
    def __init__(self, cache_path, **kwargs):
        super().__init__(**kwargs)
        # f = accesses / simSeconds: m_demand_accesses (hits + misses, see
        # CacheMemory.hh) is a whole-run total, not a per-cycle stat like
        # ipc, so it's normalized by the run's total simulated time instead
        # of the cache controller's clock_period to get an access rate.
        # CacheMemory does declare numDataArrayReads/numDataArrayWrites,
        # which would be the more precise driver, but those are only
        # populated by protocols that model the data/tag arrays as separate
        # resources -- MI_example doesn't, so they stay 0.
        self.dyn = (
            f"{CACHE_C_EFF} * voltage^2 * "
            f"{cache_path}.m_demand_accesses / simSeconds"
        )
        self.st = STATIC_POWER_EXPR.format(i_leak0=CACHE_I_LEAK0)


class PowerOff(MathExprPowerModel):
    dyn = "0"
    st = "0"


class CpuPowerModel(PowerModel):
    def __init__(self, cpu_path, **kwargs):
        super().__init__(**kwargs)
        self.pm = [CpuPowerOn(cpu_path), PowerOff(), PowerOff(), PowerOff()]


class RubyCachePowerModel(PowerModel):
    def __init__(self, cache_path, **kwargs):
        super().__init__(**kwargs)
        self.pm = [RubyCachePowerOn(cache_path), PowerOff(), PowerOff(), PowerOff()]


def attach_power_models(system):
    # PowerModel.subsystem defaults to a "Parent.any" proxy that looks for a
    # SubSystem ancestor (see src/sim/power/PowerModel.py) -- used by e.g.
    # configs/example/arm/fs_bigLITTLE.py, which wraps each cpu cluster in a
    # SubSystem. This system has no such wrapper, so the proxy is passed a
    # real SubSystem explicitly instead of relying on one being found.
    system.power_subsystem = m5.objects.SubSystem()

    for obj in system.descendants():
        if isinstance(obj, m5.objects.BaseCPU):
            obj.power_state.default_state = "ON"
            obj.power_model = CpuPowerModel(
                obj.path(), subsystem=system.power_subsystem
            )
        elif isinstance(obj, m5.objects.RubyController) and hasattr(
            obj, "cacheMemory"
        ):
            obj.power_state.default_state = "ON"
            obj.power_model = RubyCachePowerModel(
                f"{obj.path()}.cacheMemory", subsystem=system.power_subsystem
            )


def get_processes(args):
    """Interprets provided args and returns a list of processes"""

    multiprocesses = []
    inputs = []
    outputs = []
    errouts = []
    pargs = []

    workloads = args.cmd.split(";")
    if args.input != "":
        inputs = args.input.split(";")
    if args.output != "":
        outputs = args.output.split(";")
    if args.errout != "":
        errouts = args.errout.split(";")
    if args.options != "":
        pargs = args.options.split(";")

    idx = 0
    for wrkld in workloads:
        process = Process(pid=100 + idx)
        process.executable = wrkld
        process.cwd = os.getcwd()
        process.gid = os.getgid()

        if args.env:
            with open(args.env) as f:
                process.env = [line.rstrip() for line in f]

        if len(pargs) > idx:
            process.cmd = [wrkld] + pargs[idx].split()
        else:
            process.cmd = [wrkld]

        if len(inputs) > idx:
            process.input = inputs[idx]
        if len(outputs) > idx:
            process.output = outputs[idx]
        if len(errouts) > idx:
            process.errout = errouts[idx]

        multiprocesses.append(process)
        idx += 1

    if args.smt:
        cpu_type = ObjectList.cpu_list.get(args.cpu_type)
        assert ObjectList.is_o3_cpu(cpu_type), "SMT requires an O3CPU"
        return multiprocesses, idx
    else:
        return multiprocesses, 1


parser = argparse.ArgumentParser()
Options.addCommonOptions(parser)
Options.addSEOptions(parser)

if "--ruby" in sys.argv:
    Ruby.define_options(parser)

args = parser.parse_args()

multiprocesses = []
numThreads = 1

if args.bench:
    apps = args.bench.split("-")
    if len(apps) != args.num_cpus:
        print("number of benchmarks not equal to set num_cpus!")
        sys.exit(1)

    for app in apps:
        try:
            if ObjectList.cpu_list.get_isa(args.cpu_type) == ISA.ARM:
                exec(
                    "workload = %s('arm_%s', 'linux', '%s')"
                    % (app, args.arm_iset, args.spec_input)
                )
            else:
                exec(
                    "workload = %s(buildEnv['TARGET_ISA', 'linux', '%s')"
                    % (app, args.spec_input)
                )
            multiprocesses.append(workload.makeProcess())
        except:
            print(
                f"Unable to find workload for ISA: {app}",
                file=sys.stderr,
            )
            sys.exit(1)
elif args.cmd:
    multiprocesses, numThreads = get_processes(args)
else:
    print("No workload specified. Exiting!\n", file=sys.stderr)
    sys.exit(1)


(CPUClass, test_mem_mode, FutureClass) = Simulation.setCPUClass(args)
CPUClass.numThreads = numThreads

if args.smt and args.num_cpus > 1:
    fatal("You cannot use SMT with multiple CPUs!")

np = args.num_cpus
mp0_path = multiprocesses[0].executable
system = System(
    cpu=[CPUClass(cpu_id=i) for i in range(np)],
    mem_mode=test_mem_mode,
    mem_ranges=[AddrRange(args.mem_size)],
    cache_line_size=args.cacheline_size,
)

if numThreads > 1:
    system.multi_thread = True

system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)

system.clk_domain = SrcClockDomain(
    clock=args.sys_clock, voltage_domain=system.voltage_domain
)

system.cpu_voltage_domain = VoltageDomain()

system.cpu_clk_domain = SrcClockDomain(
    clock=args.cpu_clock, voltage_domain=system.cpu_voltage_domain
)

if args.elastic_trace_en:
    CpuConfig.config_etrace(CPUClass, system.cpu, args)

for cpu in system.cpu:
    cpu.clk_domain = system.cpu_clk_domain

if ObjectList.is_kvm_cpu(CPUClass) or ObjectList.is_kvm_cpu(FutureClass):
    if buildEnv["USE_X86_ISA"]:
        system.kvm_vm = KvmVM()
        system.m5ops_base = max(0xFFFF0000, Addr(args.mem_size).getValue())
        for process in multiprocesses:
            process.useArchPT = True
            process.kvmInSE = True
    else:
        fatal("KvmCPU can only be used in SE mode with x86")

if args.simpoint_profile:
    if not ObjectList.is_noncaching_cpu(CPUClass):
        fatal("SimPoint/BPProbe should be done with an atomic cpu")
    if np > 1:
        fatal("SimPoint generation not supported with more than one CPUs")

for i in range(np):
    if args.smt:
        system.cpu[i].workload = multiprocesses
    elif len(multiprocesses) == 1:
        system.cpu[i].workload = multiprocesses[0]
    else:
        system.cpu[i].workload = multiprocesses[i]

    if args.simpoint_profile:
        system.cpu[i].addSimPointProbe(args.simpoint_interval)

    if args.checker:
        system.cpu[i].addCheckerCpu()

    if args.bp_type:
        bpClass = ObjectList.bp_list.get(args.bp_type)
        system.cpu[i].branchPred = bpClass()

    if args.indirect_bp_type:
        indirectBPClass = ObjectList.indirect_bp_list.get(
            args.indirect_bp_type
        )
        system.cpu[i].branchPred.indirectBranchPred = indirectBPClass()

    system.cpu[i].createThreads()

if args.ruby:
    Ruby.create_system(args, False, system)
    assert args.num_cpus == len(system.ruby._cpu_ports)

    system.ruby.clk_domain = SrcClockDomain(
        clock=args.ruby_clock, voltage_domain=system.voltage_domain
    )
    for i in range(np):
        ruby_port = system.ruby._cpu_ports[i]
        system.cpu[i].createInterruptController()
        ruby_port.connectCpuPorts(system.cpu[i])
else:
    MemClass = Simulation.setMemClass(args)
    system.membus = SystemXBar()
    system.system_port = system.membus.cpu_side_ports
    CacheConfig.config_cache(args, system)
    MemConfig.config_mem(args, system)
    config_filesystem(system, args)

system.workload = SEWorkload.init_compatible(mp0_path)

if args.wait_gdb:
    system.workload.wait_for_remote_gdb = True

root = Root(full_system=False, system=system)

# SimObject.path() only resolves to "system.foo" (rather than a placeholder
# "<orphan System>.foo", which MathExpr can't parse -- it isn't a valid
# stat-path token) once the object is actually rooted, so this must run
# after Root() is constructed above.
attach_power_models(system)

Simulation.run(args, root, system, FutureClass)
