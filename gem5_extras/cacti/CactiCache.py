# Cacti area/power model for a gem5 cache.
#
# Attach one of these to any cache (classic or Ruby) and Cacti's numbers for
# that cache -- area, access/cycle time, per-access energy, leakage -- show up
# in stats.txt under the cache, together with the energy those imply for the
# accesses the run actually performed.
#
# scripts/attach_cacti.py builds these automatically for every cache in a
# configuration, which is normally easier than instantiating them by hand.

from m5.params import *
from m5.SimObject import SimObject


class CactiCache(SimObject):
    type = "CactiCache"
    cxx_class = "gem5::CactiCache"
    cxx_header = "cacti_cache.hh"

    # Where Cacti lives. Empty means "$CACTI_HOME", which
    # activate_environment.sh exports. Cacti reads its technology data
    # ("tech_params/32nm.dat") relative to the working directory, so this has
    # to be a full Cacti build directory, not just its binary.
    cacti_home = Param.String("", "Cacti build directory (default: $CACTI_HOME)")
    # Cacti has far more knobs than gem5 knows about; the ones not set below
    # come from this config file. Empty means <cacti_home>/cache.cfg, the
    # same template scripts/cacti_from_m5out.py starts from.
    template_cfg = Param.String(
        "", "Cacti .cfg supplying defaults (default: <cacti_home>/cache.cfg)"
    )

    # Array geometry. These mirror the cache being modeled.
    size = Param.MemorySize("32KiB", "Cache data array size")
    assoc = Param.Unsigned(2, "Associativity")
    block_size = Param.Unsigned(64, "Block (line) size in bytes")
    banks = Param.Unsigned(1, "Number of banks")

    rw_ports = Param.Unsigned(1, "Read/write ports")
    read_ports = Param.Unsigned(0, "Exclusive read ports")
    write_ports = Param.Unsigned(0, "Exclusive write ports")
    output_width = Param.Unsigned(
        0, "Output/input bus width in bits (0: keep the template's value)"
    )

    # Circuit-level assumptions gem5's timing-only cache model has no opinion
    # about, so they have to be stated somewhere.
    tech_nm = Param.Float(32.0, "Technology node in nm")
    temperature = Param.Unsigned(360, "Operating temperature in Kelvin")
    cache_type = Param.String("cache", "Cacti array type: cache, ram or cam")
    access_mode = Param.String(
        "normal", "Cacti tag/data access mode: normal, sequential or fast"
    )

    # Where the access counts come from. The stat names are resolved against
    # stat_target when the simulation starts and summed at every dump, so
    # Cacti's per-access energies turn into the energy of this run.
    stat_target = Param.SimObject(
        NULL, "Object whose stats hold this cache's access counts"
    )
    read_access_stats = VectorParam.String(
        [], "stat_target-relative names of stats counting read accesses"
    )
    write_access_stats = VectorParam.String(
        [], "stat_target-relative names of stats counting write accesses"
    )
    # Ruby's cache controllers only count numDataArrayReads/Writes if their
    # protocol models the tag and data arrays as separate resources; the
    # simpler protocols (MI_example, ...) leave them at zero and count whole
    # accesses only. Those undifferentiated counts land here and are charged
    # at read energy, but only when the split counts above found nothing --
    # otherwise a protocol that does model both would be counted twice.
    fallback_access_stats = VectorParam.String(
        [],
        "stat_target-relative names of stats counting accesses of unknown "
        "direction, used (as reads) only if the read/write stats are all zero",
    )
