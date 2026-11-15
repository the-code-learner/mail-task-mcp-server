# Single-trunk release policy

**One permanent branch: `main`.** Stable and beta are release channels, not independent
development histories. All shipping application code is merged into `main`
before any tag is created.

1. Create a short-lived feature/fix branch from the **current `main`**.
2. Open a PR against `main`, require full `v9 runtime tests / test`, and merge.
3. For a beta release, update `VERSION` and `CHANGELOG.md` on a short-lived
   branch **from current `main`**, merge that PR into `main` after CI, then
   trigger `beta-release.yml` with `--ref main` and the explicit beta version.
   The beta workflow verifies the exact selected commit equals current `main`.
4. For a stable release, update `VERSION` and `CHANGELOG.md` on a branch from
   current `main`, merge after PR CI, and let `stable-release.yml` publish
   the immutable stable tag only after exact-main-commit CI succeeds.
5. Never base a release candidate on the previous beta tag, never publish a beta
   from a separate release branch, and never keep a parallel beta branch alive
   between releases. A beta tag is an immutable pointer to a `main` commit.
6. Never rewrite existing tags. Preserve the versioned single-YAML Portainer
   deployment unchanged unless separately and explicitly authorized.

Legacy beta tags through `v9.8.9-beta.19` were created from a historical
parallel release line. Their history is preserved as immutable tags; the
`v9.8.9` stable promotion reconciles those commits into `main`. All subsequent
beta releases follow the single-trunk rules above.
