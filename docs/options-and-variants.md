# Options and variants

A machine family is often sold with options: motor power, voltage, seal material. Instead of one BOM per
combination, a **configurable product** keeps one BOM that lists every alternative (all the motors, all the seals),
and a **rule** on each line that is not always used says when it is. Saved combinations are **variants**, one per
order.

Open it from a BOM with **Options and variants**, or from **BOMs › Configurable products**.

## Options

Each option has a short code used in rules (`MOTOR`) and a name for people (*Motor power*), and at least two
choices, each with a code (`7K5`) and a name (*7.5 kW*). An option or choice that a rule still uses cannot be
removed; change the rule first.

**Constraints** forbid combinations: *if* `MOTOR = 11K` *then* `VOLTAGE = 415`, with the reason shown to whoever
chooses ("The 11 kW motor runs on 415 V only").

## Line rules

**Line rules** lists the product's BOM, every level, with a *Used when* box on each line. Leave it empty for lines
that are always used. Rules are written in the option codes:

| Rule | Used when |
| --- | --- |
| `MOTOR = 7K5` | the motor is 7.5 kW |
| `MOTOR != 7K5` | any motor but 7.5 kW |
| `MOTOR in (5K5, 7K5)` | either of the two |
| `MOTOR not in (5K5, 7K5)` | neither |
| `MOTOR = 11K and VOLTAGE = 415` | both |
| `VOLTAGE = 230 or (MOTOR = 11K and SEAL = PTFE)` | either side; brackets group, `and` before `or` |
| `not SEAL = PTFE` | the opposite |

Codes are not case-sensitive; Kosha writes rules the same way every time. A rule that names an option or choice that
does not exist is refused with the list of what does. Rules can be on lines inside sub-assemblies too. They are kept
with the product, by the assembly and the part of the line, so they survive BOM edits, imports and new revisions.
A line left out takes everything below it along.

## Configure

**Configure** has one list per option and shows the BOM that combination builds, every level, from the released
BOM (or the BOM in work, as a preview). A combination a constraint forbids says why.

**Check every combination** builds every allowed combination (up to 5,000) and reports:

- lines that no allowed combination uses (warning),
- assemblies a combination leaves empty (error),
- choices that build the same BOM as the other choices of their option, because no line has a rule for them
  (information),
- rules that no longer read (error).

## Variants

**Save as variant for an order** keeps the combination with a number (`PU-300-000-V01`, `-V02` …), a name, the
customer and the sales order, and the BOM it builds as it is now: it does not change when the design moves on.
Variants are saved from the released BOM. **Excel** exports the variant's BOM with each line's rule.

**Record a machine** on a variant records a delivered machine whose as-built BOM is the variant's (see
[BOM views, machines and cost](bom-views-and-cost.md)). An approver can **withdraw** a variant (order cancelled);
it is kept, and no new machines can be recorded from it.

Engineers set up options, rules and constraints and save variants; everyone can configure and look.

API: `GET /api/products`, `GET /api/items/{id}/config`, `PUT /api/items/{id}/config/options|rules|constraints`,
`POST /api/items/{id}/configure` (`choice`), `POST /api/items/{id}/config/check`, `POST /api/items/{id}/variants`,
`GET /api/variants`, `GET /api/variants/{id}`, `GET /api/variants/{id}/export`, `POST /api/variants/{id}/withdraw`,
and `variant_id` on `POST /api/machines`.
