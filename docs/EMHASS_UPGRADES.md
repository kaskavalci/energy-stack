# EMHASS upgrades

EMHASS is pinned by release tag and immutable digest in `docker-compose.yml`.
Never replace it with `latest`.

For every candidate:

1. Resolve and record the candidate's release tag, immutable manifest digest,
   package version, and source revision.
2. Run:
   `./scripts/verify-emhass-load-gap-contract.sh '<tag@sha256:digest>'`.
3. Run:
   `.venv/bin/python -m unittest scripts.tests.test_emhass_image_pin -v`.
4. Run the complete cron test suite.
5. Commit a note under `docs/verification/emhass/` containing the exact image,
   command, and passing output.
6. Only then change the Compose pin and deploy.

Release notes are supporting evidence, not a substitute for the compatibility
probe. A changed private EMHASS API fails the probe until the behavior and
probe are reviewed together.
