# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Where the label goes in a local-markdown tracker

There are no real labels here, so the string is written as a line near the top of the issue file:

```
Status: ready-for-agent
```

That line is the label. `/triage` reads and rewrites it; everything else treats it as metadata.

## A note on `ready-for-agent` in this repo

Two of the things this project tracks cannot honestly carry that label: an experiment against the
running player needs a human at the keyboard to open a lesson, and a question about what the player
actually does cannot be settled by reading code. Those are `ready-for-human` until the experiment
has run, whatever their size. Marking them `ready-for-agent` would be a claim that an agent can
finish the work alone, and for those it cannot.
