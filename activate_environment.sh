#!/usr/bin/env bash
# Source this file to build (once, cached) and expose Cacti, gem5, and DSENT:
#   source activate_environment.sh

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "activate_environment.sh must be sourced, not executed: 'source activate_environment.sh'" >&2
    exit 1
fi

_aes_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_aes_cacti_src="${_aes_repo_root}/ext/cacti"
_aes_build_dir="${_aes_repo_root}/.tools/cacti"
_aes_stamp_file="${_aes_build_dir}/.built-commit"

if [[ ! -d "${_aes_cacti_src}" ]] || [[ -z "$(ls -A "${_aes_cacti_src}" 2>/dev/null)" ]]; then
    echo "Cacti submodule not found/initialized at ${_aes_cacti_src}." >&2
    echo "Run: git submodule update --init --recursive" >&2
    unset _aes_repo_root _aes_cacti_src _aes_build_dir _aes_stamp_file
    return 1
fi

_aes_current_commit="$(git -C "${_aes_cacti_src}" rev-parse HEAD 2>/dev/null)"

if [[ -f "${_aes_stamp_file}" ]] && [[ "$(cat "${_aes_stamp_file}")" == "${_aes_current_commit}" ]]; then
    echo "cacti already built for commit ${_aes_current_commit} (cached)."
else
    echo "Building cacti (${_aes_current_commit})..."
    rm -rf "${_aes_build_dir}"
    mkdir -p "${_aes_repo_root}/.tools"
    cp -r "${_aes_cacti_src}" "${_aes_build_dir}"
    rm -rf "${_aes_build_dir}/.git"
    if make -C "${_aes_build_dir}" opt && echo "${_aes_current_commit}" > "${_aes_stamp_file}"; then
        echo "cacti build complete."
    else
        echo "cacti build failed." >&2
        unset _aes_repo_root _aes_cacti_src _aes_build_dir _aes_stamp_file _aes_current_commit
        return 1
    fi
fi

# gem5 links Cacti in (see gem5_extras/cacti), which needs Cacti's code as a
# library rather than as a program: same objects as the binary, minus its
# main(), and -fPIC because gem5 links position-independent by default.
if [[ ! -f "${_aes_build_dir}/libcacti.a" ]] \
        || [[ "${_aes_build_dir}/libcacti.a" -ot "${_aes_build_dir}/cacti" ]]; then
    echo "Building libcacti.a..."
    if ! ( cd "${_aes_build_dir}" \
            && mkdir -p obj_lib \
            && ls *.cc | grep -v '^main\.cc$' \
                | xargs -P"$(nproc 2>/dev/null || echo 4)" -I{} sh -c \
                    'g++ -m64 -fPIC -O2 -g -Wno-unknown-pragmas -msse2 -mfpmath=sse \
                        -DNTHREADS=8 -c "$1" -o "obj_lib/$(basename "$1" .cc).o"' _ {} \
            && ar rcs libcacti.a obj_lib/*.o ); then
        echo "libcacti.a build failed." >&2
        unset _aes_repo_root _aes_cacti_src _aes_build_dir _aes_stamp_file _aes_current_commit
        return 1
    fi
    echo "libcacti.a build complete."
fi

export CACTI_HOME="${_aes_build_dir}"
# Consumed by gem5_extras/cacti/SConscript when gem5 is built below. The
# headers come from the pristine submodule, the library from the build copy;
# they are the same code.
export GRAPE_CACTI_SRC="${_aes_cacti_src}"
export GRAPE_CACTI_LIB="${_aes_build_dir}/libcacti.a"

# cacti loads its technology data (tech_params/*.dat) via a path relative to the
# process's cwd, not to the binary's location, so it must always be invoked with
# CACTI_HOME as the working directory or it segfaults (NULL FILE* into fscanf).
# This wrapper cds into CACTI_HOME first, rewriting a relative "-infile <path>"
# to stay valid after the cd.
cacti() (
    local orig_pwd="${PWD}" args=() prev=""
    for arg in "$@"; do
        if [[ "${prev}" == "-infile" && "${arg}" != /* ]]; then
            arg="${orig_pwd}/${arg}"
        fi
        args+=("${arg}")
        prev="${arg}"
    done
    cd "${CACTI_HOME}" || return 1
    ./cacti "${args[@]}"
)

unset _aes_cacti_src _aes_build_dir _aes_stamp_file _aes_current_commit

# --- gem5 (RISCV, opt) -------------------------------------------------
# Built in-place inside ext/gem5/build/: gem5's own .gitignore already
# excludes build/ and m5out/, so (unlike cacti) there's no need to keep the
# submodule checkout pristine by building a separate copy.

_aes_gem5_src="${_aes_repo_root}/ext/gem5"
_aes_gem5_isa="RISCV"
_aes_gem5_variant="opt"
_aes_gem5_bin="${_aes_gem5_src}/build/${_aes_gem5_isa}/gem5.${_aes_gem5_variant}"
_aes_gem5_stamp_file="${_aes_repo_root}/.tools/gem5.built-commit"
# Compiled into gem5 through scons' EXTRAS mechanism: the CactiCache
# SimObject, which calls Cacti in-process (see gem5_extras/cacti/SConscript).
_aes_gem5_extras="${_aes_repo_root}/gem5_extras/cacti"

if [[ ! -d "${_aes_gem5_src}" ]] || [[ -z "$(ls -A "${_aes_gem5_src}" 2>/dev/null)" ]]; then
    echo "gem5 submodule not found/initialized at ${_aes_gem5_src}." >&2
    echo "Run: git submodule update --init --recursive" >&2
    unset _aes_repo_root _aes_gem5_src _aes_gem5_isa _aes_gem5_variant _aes_gem5_bin _aes_gem5_stamp_file
    return 1
fi

_aes_gem5_current_commit="$(git -C "${_aes_gem5_src}" rev-parse HEAD 2>/dev/null)"
# The stamp covers the extras too, so editing the CactiCache SimObject is
# enough to get scons re-run on the next source.
_aes_gem5_stamp="${_aes_gem5_current_commit}:$(cat "${_aes_gem5_extras}"/* | md5sum | cut -d' ' -f1)"

if [[ -x "${_aes_gem5_bin}" ]] && [[ -f "${_aes_gem5_stamp_file}" ]] \
        && [[ "$(cat "${_aes_gem5_stamp_file}")" == "${_aes_gem5_stamp}" ]]; then
    echo "gem5.${_aes_gem5_variant} (${_aes_gem5_isa}) already built for commit ${_aes_gem5_current_commit} (cached)."
else
    if ! command -v scons >/dev/null 2>&1; then
        echo "scons not found; required to build gem5. Install it and re-source this script." >&2
        unset _aes_repo_root _aes_gem5_src _aes_gem5_isa _aes_gem5_variant _aes_gem5_bin _aes_gem5_stamp_file _aes_gem5_current_commit
        return 1
    fi
    echo "Building gem5.${_aes_gem5_variant} (${_aes_gem5_isa}, ${_aes_gem5_current_commit})... this can take a long time."
    mkdir -p "${_aes_repo_root}/.tools"
    # scons resolves relative target paths against the caller's cwd even with
    # "-C", so cd into the submodule first or the build lands outside it.
    if ( cd "${_aes_gem5_src}" && scons -j"$(nproc 2>/dev/null || echo 4)" \
                "EXTRAS=${_aes_gem5_extras}" \
                "build/${_aes_gem5_isa}/gem5.${_aes_gem5_variant}" ) \
            && echo "${_aes_gem5_stamp}" > "${_aes_gem5_stamp_file}"; then
        echo "gem5 build complete."
    else
        echo "gem5 build failed." >&2
        unset _aes_repo_root _aes_gem5_src _aes_gem5_isa _aes_gem5_variant _aes_gem5_bin _aes_gem5_stamp_file _aes_gem5_current_commit
        return 1
    fi
fi

export GEM5_HOME="${_aes_gem5_src}"
export GEM5_BIN="${_aes_gem5_bin}"

gem5() {
    "${GEM5_BIN}" "$@"
}

unset _aes_gem5_src _aes_gem5_isa _aes_gem5_variant _aes_gem5_bin _aes_gem5_stamp_file _aes_gem5_extras _aes_gem5_stamp

# --- DSENT (NoC power/area model used by Garnet) ------------------------
# ext/gem5/ext/dsent/interface.cc only builds against the Python 2 C API
# (Py_InitModule, PyString_*), so it doesn't compile against Python 3. To
# keep ext/gem5 pristine, build a patched copy in .tools/ instead of editing
# the submodule in place; see scripts/dsent-py3-patch/README.md.

_aes_dsent_src="${GEM5_HOME}/ext/dsent"
_aes_dsent_patch="${_aes_repo_root}/scripts/dsent-py3-patch/interface.cc"
_aes_dsent_copy="${_aes_repo_root}/.tools/dsent"
_aes_dsent_shim_dir="${_aes_repo_root}/.tools/dsent-shims"
_aes_dsent_stamp_file="${_aes_dsent_copy}/.built-commit"
_aes_dsent_stamp="${_aes_gem5_current_commit}:$(md5sum "${_aes_dsent_patch}" | cut -d' ' -f1)"

if [[ -f "${_aes_dsent_stamp_file}" ]] && [[ "$(cat "${_aes_dsent_stamp_file}")" == "${_aes_dsent_stamp}" ]]; then
    echo "dsent already built (cached)."
else
    echo "Building dsent (ported to Python 3)..."
    rm -rf "${_aes_dsent_copy}"
    mkdir -p "${_aes_dsent_copy}" "${_aes_dsent_shim_dir}"
    cp -r "${_aes_dsent_src}" "${_aes_dsent_copy}/src"
    cp "${_aes_dsent_patch}" "${_aes_dsent_copy}/src/interface.cc"

    # DSENT's CMakeLists hardcodes the unversioned "python-config", which
    # doesn't exist on systems that only ship python3-config.
    if ! command -v python-config >/dev/null 2>&1; then
        cat > "${_aes_dsent_shim_dir}/python-config" <<'PYCFG'
#!/bin/sh
exec python3-config "$@"
PYCFG
        chmod +x "${_aes_dsent_shim_dir}/python-config"
    fi

    mkdir -p "${_aes_dsent_copy}/build"
    if ( cd "${_aes_dsent_copy}/build" \
            && PATH="${_aes_dsent_shim_dir}:${PATH}" cmake ../src \
            && PATH="${_aes_dsent_shim_dir}:${PATH}" make -j"$(nproc 2>/dev/null || echo 4)" ) \
            && echo "${_aes_dsent_stamp}" > "${_aes_dsent_stamp_file}"; then
        echo "dsent build complete."
    else
        echo "dsent build failed." >&2
        unset _aes_dsent_src _aes_dsent_patch _aes_dsent_copy _aes_dsent_shim_dir _aes_dsent_stamp_file _aes_dsent_stamp _aes_repo_root _aes_gem5_current_commit
        return 1
    fi
fi

export DSENT_MODULE_DIR="${_aes_dsent_copy}/build"
export DSENT_CONFIG_DIR="${_aes_dsent_src}/configs"

unset _aes_repo_root _aes_dsent_src _aes_dsent_patch _aes_dsent_copy _aes_dsent_shim_dir _aes_dsent_stamp_file _aes_dsent_stamp _aes_gem5_current_commit
