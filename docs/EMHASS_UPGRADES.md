# EMHASS upgrades

EMHASS is pinned by release version in `docker-compose.yml` (for example
`ghcr.io/davidusb-geek/emhass:v0.18.1`). Never use `latest`. Do not put a
digest in Compose.

For every candidate:

1. Resolve and record the candidate's release version, immutable manifest
   digest, package version, and source revision.
2. Run:
   `./scripts/verify-emhass-load-gap-contract.sh '<version@sha256:digest>'`
   (always pass version@digest; Compose alone is not enough).
3. Run the complete cron test suite and scripts test suite:
   `.venv/bin/python -m unittest discover -s emhass-cron/tests -v`
   `PYTHONPATH=scripts/tests:scripts/lib .venv/bin/python -m unittest discover -s scripts/tests -v`
4. Commit a note under `docs/verification/emhass/` containing the exact
   candidate, command, and passing output.
5. Only then change the Compose **version** pin and deploy.

Release notes are supporting evidence, not a substitute for the compatibility
probe. A changed private EMHASS API fails the probe until the behavior and
probe are reviewed together.
