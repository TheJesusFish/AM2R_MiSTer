# Upstream checkout area

Fetched dependencies live here in the private working tree and are ignored by
Git. Exact adopted revisions are listed in [sources](../docs/sources.md). Fetch
only the needed repositories, record commits/licenses/submodules, and keep game
assets separate.

Do not leave the only copy of implementation changes in an ignored checkout. Maintain applicable tracked patches under `patches/`, or deliberately adopt a managed fork/submodule before handoff. Preserve third-party notices and provenance.
