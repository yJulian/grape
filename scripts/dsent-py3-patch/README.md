# DSENT Python 3 patch

`ext/gem5/ext/dsent/interface.cc` (the Python binding used by
`ext/gem5/util/on-chip-network-power-area.py` to get NoC power/area estimates
for Garnet) is written against the Python 2 C API (`Py_InitModule`,
`PyString_*`). It does not build against Python 3, which is the only Python
this repo (and current gem5) targets.

`ext/` is meant to stay a pristine, readonly clone (see top-level README), so
this isn't patched in place. Instead, `activate_environment.sh` copies
`ext/gem5/ext/dsent` into `.tools/dsent` (gitignored) and overwrites
`interface.cc` with the ported copy in this directory before building.

`interface.cc` here is upstream's file with four changes:

- `Py_InitModule` + `initdsent(void)` -> `PyModule_Create` + `PyInit_dsent(void)`
  (Python 3's module init convention differs entirely from Python 2's).
- `PyString_AsString` -> `PyUnicode_AsUTF8`
- `PyString_FromString` (x2) -> `PyUnicode_FromString`
- `dsent_computeLinkPower`'s `frequency == -1` sentinel check (relies on an
  unsigned/signed comparison quirk of the old API) -> proper
  `PyErr_Occurred()` check after `PyLong_AsLongLong`.

No other files in DSENT needed changes.
