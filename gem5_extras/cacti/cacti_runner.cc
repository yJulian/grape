#include "cacti_runner.hh"

#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <new>

// Cacti's own headers. Nothing from gem5 may be included here, see
// cacti_runner.hh.
#include "Ucache.h"
#include "cacti_interface.h"
#include "parameter.h"

namespace grape
{
namespace cacti
{

namespace
{

/**
 * Build an InputParameter the way Cacti's own command-line path does.
 *
 * InputParameter's constructor only initializes seven of its ~120 fields and
 * parse_cfg() only fills in what the .cfg mentions, so the rest is whatever
 * happened to be in memory. Cacti's binary gets zeros there by accident
 * (it heap-allocates a fresh InputParameter), and it does depend on them:
 * zeroing the storage but skipping the constructor, for instance, clears
 * cl_vertical and silently changes the design Cacti settles on. Zeroing
 * first and then constructing in place reproduces the binary's state
 * exactly, which is what makes these numbers match a standalone
 * "cacti -infile ..." run on an equivalent config.
 */
InputParameter *
freshInputParameter()
{
    alignas(InputParameter) static char storage[sizeof(InputParameter)];
    std::memset(storage, 0, sizeof(storage));
    return new (storage) InputParameter();
}

unsigned
accessModeCode(const std::string &mode)
{
    if (mode == "sequential")
        return 1;
    if (mode == "fast")
        return 2;
    return 0; // normal
}

class ScopedChdir
{
  public:
    explicit ScopedChdir(const std::string &dir) : valid(false)
    {
        if (!getcwd(saved, sizeof(saved)))
            return;
        valid = chdir(dir.c_str()) == 0;
    }

    ~ScopedChdir()
    {
        if (valid && chdir(saved) != 0) {
            // Nothing sensible left to do here; the caller already has its
            // results and the process is about to run a simulation from the
            // wrong directory, so make the failure loud rather than silent.
            std::perror("cacti_runner: could not restore working directory");
        }
    }

    bool ok() const { return valid; }

  private:
    char saved[4096];
    bool valid;
};

} // anonymous namespace

bool
run(const Request &req, Result &res, std::string &error)
{
    ScopedChdir cwd(req.cactiHome);
    if (!cwd.ok()) {
        error = "could not change into Cacti's directory '" + req.cactiHome +
                "': " + std::strerror(errno);
        return false;
    }

    if (access(req.templateCfg.c_str(), R_OK) != 0) {
        error = "template config '" + req.templateCfg +
                "' is not readable: " + std::strerror(errno);
        return false;
    }

    InputParameter *ip = freshInputParameter();
    // parse_cfg() and everything below it reads the input through this
    // global rather than through "this".
    g_ip = ip;
    ip->parse_cfg(req.templateCfg);

    // Everything gem5 knows about the cache overrides the template.
    ip->cache_sz = req.sizeBytes;
    ip->line_sz = req.lineBytes;
    ip->assoc = req.assoc;
    ip->nbanks = req.banks;
    ip->num_rw_ports = req.rwPorts;
    ip->num_rd_ports = req.readPorts;
    ip->num_wr_ports = req.writePorts;
    ip->F_sz_nm = req.techNm;
    ip->F_sz_um = req.techNm / 1000.0;
    ip->temp = req.temperatureK;
    ip->access_mode = accessModeCode(req.accessMode);
    ip->is_cache = req.cacheType == "cache";
    ip->pure_ram = req.cacheType == "ram";
    ip->pure_cam = req.cacheType == "cam";
    if (req.outputWidthBits)
        ip->out_w = req.outputWidthBits;

    // Cacti prints its input echo and its report from the command-line
    // entry points only, but these two still control some of the chattier
    // debug output further down.
    ip->print_input_args = false;
    ip->print_detail_debug = false;

    if (!ip->error_checking()) {
        // error_checking() has already explained itself on stderr.
        error = "Cacti rejected the cache configuration";
        return false;
    }

    init_tech_params(ip->F_sz_um, false);
    Wire winit; // Do not delete this line: it initializes the wire models.

    uca_org_t fin_res;
    solve(&fin_res);

    res.accessTimeSeconds = fin_res.access_time;
    res.cycleTimeSeconds = fin_res.cycle_time;
    res.areaMm2 = fin_res.area / 1e6;      // Cacti reports um^2
    res.heightMm = fin_res.cache_ht / 1e3; // ... and um
    res.widthMm = fin_res.cache_len / 1e3;
    res.readEnergyJoules = fin_res.power.readOp.dynamic;
    res.writeEnergyJoules = fin_res.power.writeOp.dynamic;
    res.leakageWattsPerBank = fin_res.power.readOp.leakage;
    res.gateLeakageWattsPerBank = fin_res.power.readOp.gate_leakage;

    fin_res.cleanup();
    g_ip = nullptr;

    if (res.accessTimeSeconds <= 0.0 || res.areaMm2 <= 0.0) {
        error = "Cacti did not find a valid cache organization";
        return false;
    }

    return true;
}

} // namespace cacti
} // namespace grape
