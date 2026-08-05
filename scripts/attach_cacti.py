"""Attach an in-simulation Cacti model to every cache in a gem5 system.

This is the in-simulation counterpart of scripts/cacti_from_m5out.py: instead
of parsing a finished run's config.json and shelling out to the Cacti binary,
it hangs a CactiCache SimObject (gem5_extras/cacti, compiled into gem5 via
scons EXTRAS) off every cache before the simulation starts. gem5 then calls
Cacti in-process and reports its numbers -- area, access/cycle time,
per-access energy, leakage -- as stats of that cache, together with the energy
they imply for the accesses the run actually performed:

    system.cpu.dcache.cacti.area                 0.043467   # mm^2
    system.cpu.dcache.cacti.totalEnergy          0.000014   # J
    system.cpu.dcache.cacti.averagePower         0.045003   # W

Import it from a gem5 config script (it runs inside gem5's Python, so `m5` has
to be importable) and call attach_cacti(system) after the system is built and
rooted, but before m5.instantiate().
"""

import os

import m5
from m5.util import warn

try:
    from m5.objects import CactiCache
except ImportError:
    # gem5 built without EXTRAS pointing at gem5_extras/cacti.
    CactiCache = None

GEM5_DEFAULT_BLOCK_SIZE = 64

# Classic caches keep one stat group per memory command, each with an
# "accesses" formula (see BaseCache::CacheCmdStats). Commands the run never
# issues simply stay at zero.
CLASSIC_READ_STATS = [
    "ReadReq.accesses",
    "ReadExReq.accesses",
    "ReadSharedReq.accesses",
    "ReadCleanReq.accesses",
]
CLASSIC_WRITE_STATS = [
    "WriteReq.accesses",
    "WriteLineReq.accesses",
    "WritebackDirty.accesses",
    "WritebackClean.accesses",
]

# Ruby's CacheMemory counts array reads and writes directly, but only for
# protocols that model the tag and data arrays as separate resources; the rest
# only count whole demand accesses, which is what m_demand_accesses is for
# (see the fallback_access_stats parameter).
RUBY_READ_STATS = ["numDataArrayReads"]
RUBY_WRITE_STATS = ["numDataArrayWrites"]
RUBY_FALLBACK_STATS = ["m_demand_accesses"]


def _int_param(obj, name):
    """Value of obj.name as an int, or None if it isn't set to one.

    Parameters can be unset, or set to a proxy that only resolves later
    (Parent.any, ...), so this has to tolerate more than a missing attribute.
    """
    try:
        value = getattr(obj, name)
    except AttributeError:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def find_caches(root):
    """Every SimObject under root that looks like a cache.

    gem5 identifies caches by type in inconsistent ways (classic "Cache",
    Ruby "RubyCache", protocol-specific controller types, ...), but both the
    classic and the Ruby cache-storage object always carry a size *and* an
    associativity, so that combination is the detection signal -- the same
    heuristic scripts/cacti_from_m5out.py uses on config.json.
    """
    caches = []
    for obj in root.descendants():
        size = _int_param(obj, "size")
        assoc = _int_param(obj, "assoc")
        if size and assoc:
            caches.append(obj)
    return caches


def _block_size(cache, system):
    for name in ("block_size", "blk_size"):
        value = _int_param(cache, name)
        if value:
            return value
    return _int_param(system, "cache_line_size") or GEM5_DEFAULT_BLOCK_SIZE


def _is_a(obj, type_name):
    """isinstance() against a gem5 type that this build may not have."""
    cls = getattr(m5.objects, type_name, None)
    return cls is not None and isinstance(obj, cls)


def _access_stats(cache):
    """The stat names holding this cache's access counts.

    Returns (read stats, write stats, fallback stats).
    """
    if _is_a(cache, "RubyCache"):
        return RUBY_READ_STATS, RUBY_WRITE_STATS, RUBY_FALLBACK_STATS
    if _is_a(cache, "BaseCache"):
        return CLASSIC_READ_STATS, CLASSIC_WRITE_STATS, []
    # Something cache-shaped that is neither: model its geometry, but don't
    # guess at stat names that probably don't exist.
    return [], [], []


def attach_cacti(
    system,
    tech_nm=32.0,
    temperature=360,
    cache_type="cache",
    access_mode="normal",
    ports=1,
    cacti_home=None,
    template_cfg=None,
    child_name="cacti",
):
    """Give every cache under `system` a CactiCache model.

    The Cacti-side assumptions gem5's timing-only cache model has no opinion
    about (process node, temperature, ...) are the same knobs
    scripts/cacti_from_m5out.py exposes, with the same defaults, so both paths
    report the same numbers for the same cache.

    Returns the list of attached CactiCache objects.
    """
    if CactiCache is None:
        warn(
            "this gem5 was built without the Cacti integration; "
            "re-source activate_environment.sh to rebuild it. Skipping."
        )
        return []

    if cacti_home is None:
        cacti_home = os.environ.get("CACTI_HOME", "")
    if not cacti_home:
        warn(
            "$CACTI_HOME is not set, so Cacti cannot be run from inside gem5. "
            "Source activate_environment.sh. Skipping."
        )
        return []

    attached = []
    for cache in find_caches(system):
        if hasattr(cache, child_name):
            continue

        read_stats, write_stats, fallback_stats = _access_stats(cache)
        model = CactiCache(
            cacti_home=cacti_home,
            template_cfg=template_cfg or "",
            size=_int_param(cache, "size"),
            assoc=_int_param(cache, "assoc"),
            block_size=_block_size(cache, system),
            banks=_int_param(cache, "dataArrayBanks") or 1,
            rw_ports=ports,
            tech_nm=tech_nm,
            temperature=temperature,
            cache_type=cache_type,
            access_mode=access_mode,
            stat_target=cache,
            read_access_stats=read_stats,
            write_access_stats=write_stats,
            fallback_access_stats=fallback_stats,
        )
        # An implicit child assignment, so the model's stats are dumped as
        # "<cache>.cacti.*", right below the cache's own.
        setattr(cache, child_name, model)
        attached.append(model)

    return attached
