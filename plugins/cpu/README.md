# CPU Usage plugin 0.2.0

Installed by the Controller 0.2.0 installer, without granting approval. Choose CPU
Usage in the Configurator after reviewing the package in Plugins. Uses plugin API
1.1. There is no sampling thread, timer, network client, shell command, or external
Python dependency. Only explicit requests for visible instances read `/proc/stat`.
Multiple instances share reads while retaining independent parameters/baselines.
See `docs/PLUGIN-HOST-0.2.0.md` in the repository for setup and resource limits.

The first sample is unavailable while establishing a fresh baseline, not a false
0%. `scope` is `total` or `logicalCpu`; logical CPU indices are zero-based. Labels,
measurement precision, and warning threshold are per instance. `guest` counters
are not double counted. I/O wait is treated as non-busy; steal time is treated as
busy. Regressed counters or disappearing CPUs cause a new baseline/unavailable
reading rather than a fabricated percentage.

The original standalone source tests and `plugins/tools/cpu_demo.py` remain usable.
The terminal demo has no device access. Native plugins are trusted code, not a
security sandbox. This version adds localized manifest labels and accompanies the
production host; it does not modify any existing 0.1.0 installation or approval.
