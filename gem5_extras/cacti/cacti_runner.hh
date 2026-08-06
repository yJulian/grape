/*
 * Thin, gem5-free wrapper around Cacti's C++ API.
 *
 * Cacti's headers declare a pile of unqualified globals with very generic
 * names (Wire, Component, Parameter, g_ip, ...), so they are deliberately kept
 * out of every gem5 translation unit: only cacti_runner.cc includes them, and
 * everything else talks to Cacti through the POD structs below.
 */

#ifndef __GRAPE_CACTI_RUNNER_HH__
#define __GRAPE_CACTI_RUNNER_HH__

#include <cstdint>
#include <string>

namespace grape
{
namespace cacti
{

/** One Cacti run's inputs. Anything left at its default is taken from the
 * template .cfg instead, so this only has to cover what gem5 actually knows
 * about a cache. */
struct Request
{
    /** Directory Cacti is run from. It loads its technology data through
     * paths relative to the process's cwd ("tech_params/32nm.dat"), so this
     * has to be the directory those files live in. */
    std::string cactiHome;
    /** Cacti .cfg providing defaults for every parameter not set below. */
    std::string templateCfg;

    uint64_t sizeBytes = 0;
    uint32_t lineBytes = 0;
    uint32_t assoc = 0;
    uint32_t banks = 1;

    uint32_t rwPorts = 1;
    uint32_t readPorts = 0;
    uint32_t writePorts = 0;

    double techNm = 32.0;
    uint32_t temperatureK = 360;

    /** Output/input bus width in bits; 0 keeps the template's value. */
    uint32_t outputWidthBits = 0;

    /** "cache", "ram" or "cam". */
    std::string cacheType = "cache";
    /** "normal", "sequential" or "fast". */
    std::string accessMode = "normal";
};

/** One Cacti run's results, in SI units (Cacti's own internal units) except
 * for the areas, which are in mm^2/mm as in Cacti's printed report. */
struct Result
{
    double accessTimeSeconds = 0.0;
    double cycleTimeSeconds = 0.0;

    double areaMm2 = 0.0;
    double heightMm = 0.0;
    double widthMm = 0.0;

    /** Energy of one read/write access, in Joules. */
    double readEnergyJoules = 0.0;
    double writeEnergyJoules = 0.0;

    /** Leakage of a single bank, in Watts. */
    double leakageWattsPerBank = 0.0;
    double gateLeakageWattsPerBank = 0.0;
};

/**
 * Run Cacti and return what it found.
 *
 * Cacti runs in a forked child, because it responds to an input it cannot
 * model -- a cache below ~4KB, for instance -- by printing a message and
 * calling exit(), and because it finds its technology files relative to the
 * working directory, which the simulator should not have to change. Both
 * stay contained in the child; the caller only ever sees a result or a
 * failure.
 *
 * @return true on success; on failure, error explains why.
 */
bool run(const Request &req, Result &res, std::string &error);

} // namespace cacti
} // namespace grape

#endif // __GRAPE_CACTI_RUNNER_HH__
