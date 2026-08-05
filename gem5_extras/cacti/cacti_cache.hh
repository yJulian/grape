/*
 * CactiCache: a Cacti area/power model attached to a gem5 cache.
 *
 * The SimObject runs Cacti in-process at startup and exposes its results,
 * plus the energy they imply for the access counts the attached cache
 * actually recorded, as ordinary gem5 stats. Point it at any cache (classic
 * or Ruby) with the array geometry that cache was configured with, and its
 * numbers land in stats.txt right next to the cache's own.
 */

#ifndef __GRAPE_CACTI_CACHE_HH__
#define __GRAPE_CACTI_CACHE_HH__

#include <string>
#include <vector>

#include "base/statistics.hh"
#include "cacti_runner.hh"
#include "params/CactiCache.hh"
#include "sim/sim_object.hh"

namespace gem5
{

class CactiCache : public SimObject
{
  public:
    PARAMS(CactiCache);

    CactiCache(const Params &p);

    /** Runs Cacti and fills in the geometry/circuit results. */
    void startup() override;

    /** Turns the cache's access counts into energy just before a dump. */
    void preDumpStats() override;

  private:
    /** Resolves the configured stat names against the target object. */
    void resolveAccessStats();

    /** Sums the current value of previously resolved stats. */
    double sumStats(const std::vector<const statistics::Info *> &infos) const;

    /** Object whose stats provide the access counts; may be null. */
    SimObject *const statTarget;

    std::vector<const statistics::Info *> readStats;
    std::vector<const statistics::Info *> writeStats;
    std::vector<const statistics::Info *> fallbackStats;

    /** What Cacti reported for this cache's geometry, in SI units. */
    grape::cacti::Result result;

    /** Number of banks, for scaling Cacti's per-bank leakage. */
    unsigned banks = 1;

    /*
     * The stat values, in the units they are reported in.
     *
     * gem5 writes stats.txt with six digits after the decimal point, so SI
     * would report an access time of 345 ps and a per-access energy of 36 pJ
     * as "0.000000". These are the units Cacti's own report uses (mm^2, ns,
     * nJ, mW), which keeps the numbers legible and directly comparable with a
     * standalone "cacti -infile ..." run; each stat's description says which
     * unit it is in.
     */
    struct Reported
    {
        double area = 0.0;                     // mm^2
        double height = 0.0;                   // mm
        double width = 0.0;                    // mm
        double accessTime = 0.0;               // ns
        double cycleTime = 0.0;                // ns
        double readEnergyPerAccess = 0.0;      // nJ
        double writeEnergyPerAccess = 0.0;     // nJ
        double leakagePowerPerBank = 0.0;      // mW
        double gateLeakagePowerPerBank = 0.0;  // mW
        double leakagePower = 0.0;             // mW

        double readAccesses = 0.0;
        double writeAccesses = 0.0;
        double dynamicReadEnergy = 0.0;  // uJ
        double dynamicWriteEnergy = 0.0; // uJ
        double dynamicEnergy = 0.0;      // uJ
        double leakageEnergy = 0.0;      // uJ
        double totalEnergy = 0.0;        // uJ
        double dynamicPower = 0.0;       // mW
        double averagePower = 0.0;       // mW
    } reported;

    /*
     * Every stat is a statistics::Value bound to one of the doubles above
     * rather than a Scalar that gets assigned: the Cacti results are
     * constants for the whole run, and a Scalar would be zeroed by a stats
     * reset (m5.stats.reset(), --warmup-insts, ...) halfway through it.
     */
    statistics::Value area;
    statistics::Value height;
    statistics::Value width;
    statistics::Value accessTime;
    statistics::Value cycleTime;
    statistics::Value readEnergyPerAccess;
    statistics::Value writeEnergyPerAccess;
    statistics::Value leakagePowerPerBank;
    statistics::Value gateLeakagePowerPerBank;
    statistics::Value leakagePower;

    statistics::Value readAccesses;
    statistics::Value writeAccesses;
    statistics::Value dynamicReadEnergy;
    statistics::Value dynamicWriteEnergy;
    statistics::Value dynamicEnergy;
    statistics::Value leakageEnergy;
    statistics::Value totalEnergy;
    statistics::Value dynamicPower;
    statistics::Value averagePower;
};

} // namespace gem5

#endif // __GRAPE_CACTI_CACHE_HH__
