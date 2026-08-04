#!/usr/bin/env python3
"""Estimate Garnet on-chip-network power/area from a gem5 m5out/ via DSENT.

This is a Python-3-clean rewrite of gem5's own
ext/gem5/util/on-chip-network-power-area.py, which (a) imports the "dsent"
extension module, built from ext/gem5/ext/dsent against the Python 2 C API
and thus uninstallable here (see scripts/dsent-py3-patch/), and (b) uses a
Python-2-only `string.split` call that crashes under Python 3 regardless.

Requires the DSENT environment to be active (`source activate_environment.sh`
from the repo root), which builds the ported module and sets
$DSENT_MODULE_DIR / $DSENT_CONFIG_DIR.

Only applies to runs that actually used GarnetNetwork (--network garnet in
gem5's classic configs, or the gem5.components Garnet-based hierarchies) --
a run with the default "simple" Ruby network has nothing for DSENT to model.
"""

import argparse
import os
import re
import sys
from configparser import ConfigParser
from pathlib import Path


def load_dsent():
    module_dir = os.environ.get("DSENT_MODULE_DIR")
    if not module_dir:
        sys.exit(
            "DSENT_MODULE_DIR is not set. Run 'source activate_environment.sh' "
            "first."
        )
    sys.path.insert(0, module_dir)
    import dsent  # noqa: PLC0415

    return dsent


def parse_network_config(config_ini: Path):
    config = ConfigParser()
    if not config.read(config_ini):
        sys.exit(f"ERROR: config file '{config_ini}' not found")

    if not config.has_section("system.ruby.network"):
        sys.exit(f"ERROR: no Ruby network in '{config_ini}'")

    network_type = config.get("system.ruby.network", "type")
    if network_type not in ("GarnetNetwork", "GarnetNetwork_d"):
        sys.exit(
            f"ERROR: '{config_ini}' uses network type '{network_type}', not "
            "Garnet -- nothing for DSENT to model. Rerun gem5 with "
            "--network garnet (classic configs) or a Garnet-based cache "
            "hierarchy (gem5.components)."
        )

    return (
        config,
        config.getint("system.ruby.network", "number_of_virtual_networks"),
        config.getint("system.ruby.network", "vcs_per_vnet"),
        config.getint("system.ruby.network", "buffers_per_data_vc"),
        config.getint("system.ruby.network", "buffers_per_ctrl_vc"),
        8 * config.getint("system.ruby.network", "ni_flit_size"),
        config.get("system.ruby.network", "routers").split(),
        config.get("system.ruby.network", "int_links").split(),
        config.get("system.ruby.network", "ext_links").split(),
    )


def get_clock(obj: str, config: ConfigParser) -> float:
    obj_type = config.get(obj, "type")
    if obj_type == "SrcClockDomain":
        return config.getint(obj, "clock")
    if obj_type == "DerivedClockDomain":
        source = config.get(obj, "clk_domain")
        divider = config.getint(obj, "clk_divider")
        return get_clock(source, config) / divider
    return get_clock(config.get(obj, "clk_domain"), config)


def router_power_and_area(
    dsent,
    router: str,
    config: ConfigParser,
    int_links: list[str],
    ext_links: list[str],
    number_of_virtual_networks: int,
    vcs_per_vnet: int,
    buffers_per_data_vc: int,
    ni_flit_size_bits: int,
) -> dict[str, float]:
    frequency = get_clock(router, config)
    num_ports = sum(
        1
        for link in int_links
        if config.get(link, "node_a") == router
        or config.get(link, "node_b") == router
    )
    num_ports += sum(
        1 for link in ext_links if config.get(link, "int_node") == router
    )

    return dict(
        dsent.computeRouterPowerAndArea(
            int(frequency),
            num_ports,
            num_ports,
            number_of_virtual_networks,
            vcs_per_vnet,
            buffers_per_data_vc,
            ni_flit_size_bits,
        )
    )


def link_power(dsent, link: str, config: ConfigParser) -> dict[str, dict[str, float]]:
    # Current gem5 (GarnetLink) names a link's two directional sub-links
    # network_links0/network_links1 -- the "nls0"/"nls1" naming used by
    # gem5's own (unmaintained) on-chip-network-power-area.py is stale.
    results = {}
    for suffix in ("network_links0", "network_links1"):
        frequency = get_clock(f"{link}.{suffix}", config)
        results[suffix] = dict(dsent.computeLinkPower(int(frequency)))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("m5out", type=Path, help="Path to a gem5 m5out/ directory")
    parser.add_argument(
        "--router-config",
        type=Path,
        default=None,
        help="DSENT router config (default: $DSENT_CONFIG_DIR/router.cfg)",
    )
    parser.add_argument(
        "--link-config",
        type=Path,
        default=None,
        help="DSENT link config (default: $DSENT_CONFIG_DIR/electrical-link.cfg)",
    )
    args = parser.parse_args()

    dsent_config_dir = os.environ.get("DSENT_CONFIG_DIR")
    router_config = args.router_config or (
        Path(dsent_config_dir) / "router.cfg" if dsent_config_dir else None
    )
    link_config = args.link_config or (
        Path(dsent_config_dir) / "electrical-link.cfg" if dsent_config_dir else None
    )
    if not router_config or not link_config:
        sys.exit(
            "No DSENT config dir known. Run 'source activate_environment.sh' "
            "first, or pass --router-config/--link-config explicitly."
        )

    config_ini = (args.m5out / "config.ini").resolve()
    stats_txt = (args.m5out / "stats.txt").resolve()
    if not stats_txt.is_file():
        sys.exit(f"ERROR: {stats_txt} not found")
    router_config = router_config.resolve()
    link_config = link_config.resolve()

    dsent = load_dsent()

    # router.cfg/electrical-link.cfg reference their tech model via a path
    # ("ext/dsent/tech/...") relative to the gem5 checkout root, so DSENT
    # must be run from there for those lookups to succeed.
    gem5_home = os.environ.get("GEM5_HOME")
    if not gem5_home:
        sys.exit("GEM5_HOME is not set. Run 'source activate_environment.sh' first.")
    os.chdir(gem5_home)

    (
        config,
        number_of_virtual_networks,
        vcs_per_vnet,
        buffers_per_data_vc,
        _buffers_per_control_vc,  # unused: DSENT's router model only takes one buffer depth
        ni_flit_size_bits,
        routers,
        int_links,
        ext_links,
    ) = parse_network_config(config_ini)

    sim_seconds_match = re.search(
        r"^sim(?:_)?[sS]econds\s+([0-9.eE+-]+)", stats_txt.read_text(), re.MULTILINE
    )
    if not sim_seconds_match:
        sys.exit(f"ERROR: no 'simSeconds' stat found in {stats_txt}")
    sim_seconds = float(sim_seconds_match.group(1))
    print(f"Simulation length: {sim_seconds} s\n")

    dsent.initialize(str(router_config))
    for router in routers:
        power = router_power_and_area(
            dsent,
            router,
            config,
            int_links,
            ext_links,
            number_of_virtual_networks,
            vcs_per_vnet,
            buffers_per_data_vc,
            ni_flit_size_bits,
        )
        print(f"{router}:")
        for key, value in power.items():
            print(f"  {key}: {value}")
    dsent.finalize()

    dsent.initialize(str(link_config))
    for link in int_links + ext_links:
        power = link_power(dsent, link, config)
        print(f"{link}:")
        for suffix, values in power.items():
            print(f"  {suffix}:")
            for key, value in values.items():
                print(f"    {key}: {value}")
    dsent.finalize()


if __name__ == "__main__":
    main()
