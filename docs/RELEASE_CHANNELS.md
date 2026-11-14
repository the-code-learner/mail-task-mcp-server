# Stable and Beta release channels (v9.8.6+)

Postmaster MCP publishes two kinds of immutable application releases:

| Release | Tag format | GitHub Release | Who receives it |
| --- | --- | --- | --- |
| **Stable** | `v9.8.6` | Published, not a prerelease | Everyone |
| **Beta** | `v9.8.6-beta.1` | Published prerelease | Only users who explicitly opt in |

Draft releases, other suffixes (including `-rc`), and releases whose GitHub
`prerelease` flag does not match the tag are **never** eligible.

## User preference in Settings

Open **Settings → Versions & updates → Update channel** and choose:

- **Stable only** (default): see and install only published stable releases.
- **Stable + Beta**: see and install either kind, ordered by semantic version.

Select **Save update preference**. This is a persistent *eligibility* choice,
**not an immediate version change or restart**. The list and update badge
refresh according to the new choice. To change the running build now, choose
**Update to latest allowed release** or **Switch to selected version**, select
**Preview change**, and explicitly confirm the exact target in the separate
confirmation step. A beta candidate carries a visible **BETA** label.

A final `v9.8.6` is newer than `v9.8.6-beta.2`, but
`v9.8.7-beta.1` is newer than stable `v9.8.6`. This comparison uses numeric
SemVer components, not alphabetical tag ordering. In Stable-only mode,
prereleases are hidden and disallowed by the server, even if someone submits
a beta target manually.

## One-time Portainer bootstrap requirement

The **versioned canonical** [`postmaster-mcp.yml`](../postmaster-mcp.yml)
adds the beta-aware resolver and the
`POSTMASTER_BOOTSTRAP_BETA_SUPPORTED: "true"` capability flag.
An existing 9.8.5 stack can continue receiving **Stable** releases without
changing its deployment. **Beta opt-in is disabled until the operator updates
the Portainer stack from this new canonical YAML.** Merely installing a newer
Python application build cannot change the shell script in the *already
deployed* Portainer YAML.

Upgrade the *existing* stack through its normal operator-approved Portainer
procedure, **preserving all existing secrets, domain settings, mounted volumes
and local customizations**. This is not an instruction to create another stack.
The default remains `POSTMASTER_VERSION: latest` with **Stable only** until
the user saves the Beta preference. The preference is persisted under the
existing Postmaster runtime-control path; it is not tied to an owner, project
or email account and does not change MCP authorization.

When `latest` is the effective selector, an ordinary future container
restart may resolve the newest eligible version. That is why opting into
Beta **before a scheduled restart** can change what installs on that restart.
A pinned specific version stays pinned. Neither GitHub release publication
nor preference saving directly restarts the running production instance.

Users currently *running* a beta must first explicitly switch to an eligible
stable release before disabling beta; this avoids an accidental downgrade on
the next ordinary restart.

## Maintainer release procedure

**Stable**: merge reviewed and tested changes to `main`, update `VERSION`
to exactly `MAJOR.MINOR.PATCH` and add a matching heading in
`CHANGELOG.md`. The existing `stable-release.yml` workflow creates
the corresponding immutable tag and verifies a non-prerelease GitHub release.

**Beta**: on a reviewed, test-verified release branch, set `VERSION` to
`MAJOR.MINOR.PATCH-beta.N` and add a matching changelog heading.
First ensure the **v9 runtime tests** workflow is green for that exact
beta-branch commit. Then run the **beta application release** workflow manually
using the release branch and exactly the same version input. Publication is
blocked if the matching CI run is absent or not successful. The workflow refuses unmatched
VERSION/changelog data, tags the exact selected commit once, and verifies
that GitHub publishes it with `isPrerelease=true`. Never retarget an
existing beta tag; publish a new numbered beta instead.

All releases continue to use the **same repository, single canonical YAML,
runtime data volumes, MCP tool access and existing preview/confirmation
lifecycle**. A beta release is a real deployable build, but is not considered
production stable or shown to Stable-only users.
