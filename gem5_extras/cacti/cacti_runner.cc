#include "cacti_runner.hh"

#include <sys/wait.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
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

/** What the child process hands back to the parent. POD, written to a pipe
 * as raw bytes: both ends are the same binary. */
struct Payload
{
    bool ok;
    Result res;
    char error[256];
};

void
setError(Payload &payload, const std::string &message)
{
    payload.ok = false;
    std::snprintf(payload.error, sizeof(payload.error), "%s", message.c_str());
}

/** Runs Cacti in this process. Only ever called in the forked child, because
 * Cacti reacts to an input it cannot model by exiting. */
void
runHere(const Request &req, Payload &payload)
{
    // No need to change back: this is a child process that is about to exit.
    if (chdir(req.cactiHome.c_str()) != 0) {
        setError(payload, "could not change into Cacti's directory '" +
                              req.cactiHome + "': " + std::strerror(errno));
        return;
    }

    if (access(req.templateCfg.c_str(), R_OK) != 0) {
        setError(payload, "template config '" + req.templateCfg +
                              "' is not readable: " + std::strerror(errno));
        return;
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
        setError(payload, "Cacti rejected the cache configuration");
        return;
    }

    init_tech_params(ip->F_sz_um, false);
    Wire winit; // Do not delete this line: it initializes the wire models.

    uca_org_t fin_res;
    solve(&fin_res);

    Result &res = payload.res;
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
        setError(payload, "Cacti did not find a valid cache organization");
        return;
    }

    payload.ok = true;
}

/** Reads exactly len bytes, or returns false. */
bool
readAll(int fd, void *buffer, size_t len)
{
    char *at = static_cast<char *>(buffer);
    while (len) {
        const ssize_t got = read(fd, at, len);
        if (got <= 0)
            return false;
        at += got;
        len -= got;
    }
    return true;
}

} // anonymous namespace

bool
run(const Request &req, Result &res, std::string &error)
{
    // Cacti responds to an input it cannot model -- a cache below ~4KB, for
    // instance -- by printing a message and calling exit(), which would take
    // the whole simulation down with it. Its results are also computed by a
    // pile of 2008-vintage code that this project does not maintain. So it
    // runs in a forked child: whatever it does to that process, the parent
    // only sees a failed run and reports it as one.
    int fds[2];
    if (pipe(fds) != 0) {
        error = std::string("could not create a pipe: ") + std::strerror(errno);
        return false;
    }

    const pid_t pid = fork();
    if (pid < 0) {
        error = std::string("could not fork: ") + std::strerror(errno);
        close(fds[0]);
        close(fds[1]);
        return false;
    }

    if (pid == 0) {
        close(fds[0]);

        Payload payload;
        std::memset(&payload, 0, sizeof(payload));
        runHere(req, payload);

        const ssize_t written = write(fds[1], &payload, sizeof(payload));
        close(fds[1]);
        // _exit(), not exit(): gem5's atexit handlers belong to the parent.
        _exit(written == sizeof(payload) ? 0 : 1);
    }

    close(fds[1]);

    Payload payload;
    std::memset(&payload, 0, sizeof(payload));
    const bool complete = readAll(fds[0], &payload, sizeof(payload));
    close(fds[0]);

    int status = 0;
    while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {
    }

    if (!complete) {
        if (WIFSIGNALED(status)) {
            error = "Cacti was killed by signal " +
                    std::to_string(WTERMSIG(status));
        } else {
            error = "Cacti exited (status " +
                    std::to_string(WIFEXITED(status) ? WEXITSTATUS(status) : -1) +
                    ") without producing a result; its own message is above";
        }
        return false;
    }

    if (!payload.ok) {
        error = payload.error;
        return false;
    }

    res = payload.res;
    return true;
}

} // namespace cacti
} // namespace grape
