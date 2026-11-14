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

## Existing Portainer YAML — no changes required

The existing `postmaster-mcp.yml` stays **byte-for-byte unchanged**.
Its startup shell accepts numeric `vX.Y.Z` refs but cannot parse
`-beta.N` suffixes. The beta publishing workflow therefore creates two
immutable Git tags pointing to the **same exact tested commit**:

- Human-facing GitHub prerelease: `v9.8.7-beta.2`.
- Internal numeric transport alias: `v999999999.9.8000007000002`.

Only the human-facing tag gets a GitHub Release entry. Beta opt-in,
catalog filtering, version comparisons and approval previews all use the
real `-beta.N` version. On confirmation, the Python runtime writes its
numeric transport alias into the *existing* persistent runtime-control file.
The original YAML reads this numeric selector normally and downloads
the matching immutable Git ref. An accompanying `beta_target` key allows
the WebGUI and MCP API to display the original version rather than the alias.

Stable remains the default and retains its normal `latest` startup behavior.
**A selected Beta remains pinned until a further explicitly approved
version change**: the legacy bootstrap only discovers stable releases for
`latest`. Selecting Stable + Beta changes which updates can be installed;
saving the preference alone neither restarts nor installs anything.
The usual CSRF and preview/explicit confirmation safety checks remain in force.
Reverting to Stable while running or pinned to Beta requires first
installing an eligible stable version explicitly.

The beta publishing workflow refuses alias collisions and never reassigns
published Git refs. Each beta must have a successful test run for the same
commit and a matching VERSION and CHANGELOG entry. If the alias was not
published, the install fails safely rather than switching to arbitrary code.
No new container, volume, Portainer stack edit, Docker socket access,
or in-place tampering with the cached source is required.

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
