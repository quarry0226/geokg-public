# `data/` — Raw input datasets (not tracked in git)

This directory is intentionally empty in the public repository. The raw KAIS / LSMD / TN_RODWAY datasets used by the framework (~1.4 GB total) are not redistributed here because of:

1. **Size** — exceeds GitHub's recommended repository size budget.
2. **Licence** — Korean government open data is best obtained from the official portals so the user accepts the original terms of use directly.

See **[../docs/DATA_DOWNLOAD.md](../docs/DATA_DOWNLOAD.md)** for the exact source URLs and the expected directory layout after download.

After populating this directory according to the layout in `DATA_DOWNLOAD.md`, the loaders under `backend/data/` will pick everything up automatically without further configuration.
