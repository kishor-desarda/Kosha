# Change requests and change orders

A **change request** (ECR-26-0007) says *please change this*. Anyone who can see a part raises one from the part's
page (**Raise change request**) with a reason, and the discussion about it stays with the request. An engineer
takes it into a **change order** (ECO-26-0031), new or already open; an approver can decline it with a reason.
Requests are never deleted: withdrawn and declined ones stay in the list.

A change order lists the affected files, what happens to each (new revision, spares only, obsolete), its approvers,
and two more things:

## New revision or new number?

When a change order makes a new revision of a released part, it asks three questions:

1. Will the new part fit everywhere the old one is used, including machines already at customers' sites?
2. Can old and new stock be mixed in the store without any problem?
3. Will it do the same job, with the same life and performance?

| Answers | Result |
| --- | --- |
| All yes | Same number, next revision. |
| Any no | New number. The old number becomes *spares only* (naming the part that replaces it) if installed machines still need it, otherwise *obsolete*. |
| Only for one customer | New number, marked customer restricted. |

Kosha does not submit a change order whose answers say "new number" while it still revises the old one. When a
part is made spares only or obsolete, every released assembly that uses it must be in the same change order, so it
gets a new revision with the new part.

## When it takes effect

- **When implemented** (the default): for new work orders from the day the change is implemented.
- **From a date**: for new work orders from that date.
- **From a machine serial** (for example MX26-0042): so the as-built BOM of each machine stays right for spares.
- **Immediately**: also for open work orders, when the old design is unsafe. The reason must say why; after
  implementing, Kosha reminds you to check the open work orders in the ERP.

## Approval routes

Admin › Approval routes says who approves: for every change, per category (the code that starts the part number,
for example ROT for rotating parts) or per project. A route asks its approvers **all at once** or **one after
another**; the next person is asked only when the one before has approved. A new change order without approvers
named takes the route of its parts (the category and project routes combined, or the default route when none fits);
**Use approval route** on a draft does the same. The person who asked for the change is never their own approver.

Approvers need the approver role, or the extra role *Quality and purchase*.

## Stand-ins and reminders

An approver going on leave names a stand-in until a date (account menu › **Stand-in while I am away**). The stand-in
sees the approvals on their home page and decides in the approver's name; both names are kept.

An approver who has not decided after **two working days** is reminded (and their stand-in). After **four working
days** the route's design lead is told, or the administrators when the route names nobody. Each reminder is sent
once per approval round.

## The approval record

Every approval and rejection is kept for good: who decided (and for whom, as a stand-in), when, the revisions it
covered, the comment and the approval round. Nothing in Kosha can change or remove it; the audit log has the same.
