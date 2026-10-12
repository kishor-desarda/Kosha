# Governance

Kosha is a community project. This page says who decides, and how.

## Roles

| Role | Who | Can |
| --- | --- | --- |
| **Contributor** | Anyone who opens an issue, a discussion or a pull request | Propose changes; review others' work |
| **Maintainer** | Contributors with a record of good, reviewed contributions, listed below | Review and merge pull requests in their areas, triage issues, cut releases |
| **Lead maintainer** | Named below | Breaks ties, guards the project's direction and trademark, appoints maintainers with the other maintainers' agreement |

## How decisions are made

- Day-to-day decisions happen in pull requests: one maintainer approval for normal changes, two
  for security-sensitive areas, the add-in contract and database migrations (see the development
  guide, section 12).
- Larger decisions (new dependency, new add-in interface, licence questions, a change of direction)
  get an issue or a decision record in `docs/adr/`, open for comment for at least one week.
- Maintainers aim for consensus. If there is none, the lead maintainer decides and records why.

## Becoming a maintainer

Make sustained, high-quality contributions (code, reviews, documentation, packs or support) for a
few months. Any maintainer can propose you; the other maintainers agree in a private discussion.
Maintainers who are inactive for a year move to *emeritus* and can return at any time.

## Maintainers

| Name | GitHub | Areas |
| --- | --- | --- |
| Kishor Desarda | @kishor-desarda | Lead maintainer; all areas |

## Licences and contributions

Contributions are accepted under the licence of the folder they change (AGPL-3.0-or-later for the
core, Apache-2.0 for SDKs and add-ins, CC BY 4.0 for packs and documentation), certified by a
`Signed-off-by` line under the Developer Certificate of Origin. There is no contributor licence
agreement, so the project cannot be relicensed without the agreement of its contributors.

## Contact

Until the project has its own e-mail address, reach the maintainers privately through the
repository's **Security** tab › **Report a vulnerability**. For conduct reports, start the title
with "Conduct:". Everything else belongs in public issues and discussions.
