#!/usr/bin/env bash
# Source this file to build (once, cached) and expose Cacti:
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

export CACTI_HOME="${_aes_build_dir}"

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

unset _aes_repo_root _aes_cacti_src _aes_build_dir _aes_stamp_file _aes_current_commit
