#include "cacti_cache.hh"

#include <cstdlib>

#include "base/logging.hh"
#include "base/statistics.hh"
#include "base/stats/info.hh"
#include "base/trace.hh"
#include "debug/Cacti.hh"
#include "sim/core.hh"

namespace gem5
{

namespace
{

/** Cacti needs a directory to run from and a .cfg to take its remaining
 * defaults from; both can be left to the environment set up by
 * activate_environment.sh. */
std::string
resolveCactiHome(const std::string &fromParams)
{
    if (!fromParams.empty())
        return fromParams;

    const char *env = std::getenv("CACTI_HOME");
    return env ? std::string(env) : std::string();
}

} // anonymous namespace

CactiCache::CactiCache(const Params &p)
    : SimObject(p),
      statTarget(p.stat_target),

      ADD_STAT(area, statistics::units::Unspecified::get(),
               "total cache area as modeled by Cacti (mm^2)"),
      ADD_STAT(height, statistics::units::Unspecified::get(),
               "cache height as modeled by Cacti (mm)"),
      ADD_STAT(width, statistics::units::Unspecified::get(),
               "cache width as modeled by Cacti (mm)"),
      ADD_STAT(accessTime, statistics::units::Unspecified::get(),
               "Cacti access time of the cache (ns)"),
      ADD_STAT(cycleTime, statistics::units::Unspecified::get(),
               "Cacti random cycle time of the cache (ns)"),
      ADD_STAT(readEnergyPerAccess, statistics::units::Unspecified::get(),
               "Cacti dynamic energy of one read access (nJ)"),
      ADD_STAT(writeEnergyPerAccess, statistics::units::Unspecified::get(),
               "Cacti dynamic energy of one write access (nJ)"),
      ADD_STAT(leakagePowerPerBank, statistics::units::Unspecified::get(),
               "Cacti leakage power of a single bank (mW)"),
      ADD_STAT(gateLeakagePowerPerBank, statistics::units::Unspecified::get(),
               "Cacti gate leakage power of a single bank (mW)"),
      ADD_STAT(leakagePower, statistics::units::Unspecified::get(),
               "Cacti leakage power of all banks together (mW)"),

      ADD_STAT(readAccesses, statistics::units::Count::get(),
               "read accesses counted by the modeled cache"),
      ADD_STAT(writeAccesses, statistics::units::Count::get(),
               "write accesses counted by the modeled cache"),
      ADD_STAT(dynamicReadEnergy, statistics::units::Unspecified::get(),
               "read energy over the simulated period (uJ)"),
      ADD_STAT(dynamicWriteEnergy, statistics::units::Unspecified::get(),
               "write energy over the simulated period (uJ)"),
      ADD_STAT(dynamicEnergy, statistics::units::Unspecified::get(),
               "dynamic energy over the simulated period (uJ)"),
      ADD_STAT(leakageEnergy, statistics::units::Unspecified::get(),
               "leakage energy over the simulated period (uJ)"),
      ADD_STAT(totalEnergy, statistics::units::Unspecified::get(),
               "dynamic plus leakage energy over the simulated period (uJ)"),
      ADD_STAT(dynamicPower, statistics::units::Unspecified::get(),
               "average dynamic power over the simulated period (mW)"),
      ADD_STAT(averagePower, statistics::units::Unspecified::get(),
               "average total power over the simulated period (mW)")
{
    area.scalar(reported.area);
    height.scalar(reported.height);
    width.scalar(reported.width);
    accessTime.scalar(reported.accessTime);
    cycleTime.scalar(reported.cycleTime);
    readEnergyPerAccess.scalar(reported.readEnergyPerAccess);
    writeEnergyPerAccess.scalar(reported.writeEnergyPerAccess);
    leakagePowerPerBank.scalar(reported.leakagePowerPerBank);
    gateLeakagePowerPerBank.scalar(reported.gateLeakagePowerPerBank);
    leakagePower.scalar(reported.leakagePower);

    readAccesses.scalar(reported.readAccesses);
    writeAccesses.scalar(reported.writeAccesses);
    dynamicReadEnergy.scalar(reported.dynamicReadEnergy);
    dynamicWriteEnergy.scalar(reported.dynamicWriteEnergy);
    dynamicEnergy.scalar(reported.dynamicEnergy);
    leakageEnergy.scalar(reported.leakageEnergy);
    totalEnergy.scalar(reported.totalEnergy);
    dynamicPower.scalar(reported.dynamicPower);
    averagePower.scalar(reported.averagePower);
}

void
CactiCache::startup()
{
    SimObject::startup();

    const Params &p = params();

    grape::cacti::Request req;
    req.cactiHome = resolveCactiHome(p.cacti_home);
    fatal_if(req.cactiHome.empty(),
             "%s: no Cacti installation to use. Set the cacti_home parameter "
             "or $CACTI_HOME (source activate_environment.sh).",
             name());
    req.templateCfg = p.template_cfg.empty() ? req.cactiHome + "/cache.cfg"
                                             : p.template_cfg;

    req.sizeBytes = p.size;
    req.lineBytes = p.block_size;
    req.assoc = p.assoc;
    req.banks = p.banks;
    req.rwPorts = p.rw_ports;
    req.readPorts = p.read_ports;
    req.writePorts = p.write_ports;
    req.techNm = p.tech_nm;
    req.temperatureK = p.temperature;
    req.outputWidthBits = p.output_width;
    req.cacheType = p.cache_type;
    req.accessMode = p.access_mode;

    DPRINTF(Cacti,
            "running Cacti: %lluB, %u-way, %uB lines, %u bank(s), %gnm, %uK\n",
            (unsigned long long)req.sizeBytes, req.assoc, req.lineBytes,
            req.banks, req.techNm, req.temperatureK);

    // A cache Cacti cannot model is not a reason to abandon the simulation:
    // gem5's page-table walker caches are 1KB, below anything Cacti will
    // produce an organization for, and a run whose caches are otherwise fine
    // should still finish. Those models report zeros, and say why.
    std::string error;
    if (!grape::cacti::run(req, result, error)) {
        warn("%s: Cacti could not model this %lluB %u-way cache with %uB "
             "lines (%s); its stats will be zero.",
             name(), (unsigned long long)req.sizeBytes, req.assoc,
             req.lineBytes, error);
        return;
    }

    banks = req.banks;

    reported.area = result.areaMm2;
    reported.height = result.heightMm;
    reported.width = result.widthMm;
    reported.accessTime = result.accessTimeSeconds * 1e9;
    reported.cycleTime = result.cycleTimeSeconds * 1e9;
    reported.readEnergyPerAccess = result.readEnergyJoules * 1e9;
    reported.writeEnergyPerAccess = result.writeEnergyJoules * 1e9;
    reported.leakagePowerPerBank = result.leakageWattsPerBank * 1e3;
    reported.gateLeakagePowerPerBank = result.gateLeakageWattsPerBank * 1e3;
    reported.leakagePower = reported.leakagePowerPerBank * banks;

    DPRINTF(Cacti,
            "Cacti result: %g mm^2, %g ns access, %g nJ read, %g mW leakage\n",
            reported.area, reported.accessTime, reported.readEnergyPerAccess,
            reported.leakagePower);

    resolveAccessStats();
}

void
CactiCache::resolveAccessStats()
{
    if (!statTarget) {
        warn_if(!params().read_access_stats.empty() ||
                    !params().write_access_stats.empty() ||
                    !params().fallback_access_stats.empty(),
                "%s: access stats configured but no stat_target to read them "
                "from; only Cacti's static numbers will be reported.",
                name());
        return;
    }

    auto resolve = [this](const std::vector<std::string> &names,
                          std::vector<const statistics::Info *> &out) {
        for (const auto &statName : names) {
            const statistics::Info *info = statTarget->resolveStat(statName);
            if (!info) {
                warn("%s: %s has no stat '%s'; it will not be counted.",
                     name(), statTarget->name(), statName);
                continue;
            }
            out.push_back(info);
        }
    };

    resolve(params().read_access_stats, readStats);
    resolve(params().write_access_stats, writeStats);
    resolve(params().fallback_access_stats, fallbackStats);
}

double
CactiCache::sumStats(const std::vector<const statistics::Info *> &infos) const
{
    double total = 0.0;
    for (const auto *info : infos) {
        if (const auto *s = dynamic_cast<const statistics::ScalarInfo *>(info))
            total += s->total();
        else if (const auto *v =
                     dynamic_cast<const statistics::VectorInfo *>(info))
            total += v->total();
        else
            warn_once("%s: stat '%s' is of a kind that cannot be summed.",
                      name(), info->name);
    }
    return total;
}

void
CactiCache::preDumpStats()
{
    SimObject::preDumpStats();

    reported.readAccesses = sumStats(readStats);
    reported.writeAccesses = sumStats(writeStats);

    // A protocol that doesn't tell reads and writes apart still counts its
    // accesses somewhere; charge those at read energy rather than reporting
    // an idle cache. See fallback_access_stats in CactiCache.py.
    if (reported.readAccesses == 0.0 && reported.writeAccesses == 0.0)
        reported.readAccesses = sumStats(fallbackStats);

    // Everything below is computed in SI and converted once, at the end.
    const double dynamicReadJoules =
        reported.readAccesses * result.readEnergyJoules;
    const double dynamicWriteJoules =
        reported.writeAccesses * result.writeEnergyJoules;
    const double dynamicJoules = dynamicReadJoules + dynamicWriteJoules;

    const double leakageWatts = result.leakageWattsPerBank * banks;
    const double seconds = curTick() / (double)sim_clock::Frequency;
    const double leakageJoules = leakageWatts * seconds;
    const double totalJoules = dynamicJoules + leakageJoules;

    reported.dynamicReadEnergy = dynamicReadJoules * 1e6;
    reported.dynamicWriteEnergy = dynamicWriteJoules * 1e6;
    reported.dynamicEnergy = dynamicJoules * 1e6;
    reported.leakageEnergy = leakageJoules * 1e6;
    reported.totalEnergy = totalJoules * 1e6;

    reported.dynamicPower =
        seconds > 0.0 ? dynamicJoules / seconds * 1e3 : 0.0;
    reported.averagePower = seconds > 0.0 ? totalJoules / seconds * 1e3 : 0.0;
}

} // namespace gem5
