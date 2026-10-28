# Routines

A routine covers a period of several fictional days in which the player delegates the ordinary decisions ("I mend
the nets with Hob for two days"). The narrator writes the period step by step; the engine validates and saves each
step like a normal turn, so money, possessions, abilities and the clock follow the same rules. Passing time never
restores health or mana.

A routine is driven by hand or by a chat assistant: the engine prepares a packet, someone answers it, the engine
applies the answer. It never calls a model by itself (`remote_calls` is always 0).

```bash
novel routine preview --days 2                              # dates covered, changes nothing
novel routine open --days 2 --folder routines/nets --objective "Mend Hob's nets"
novel routine prepare --folder routines/nets                # writes the packet of the next step
novel routine apply --folder routines/nets --file answer.json --next
novel routine status --folder routines/nets
novel routine report --folder routines/nets                 # the accepted narrations only
```

`prepare` names four files in the step folder: `packet.json` (the turn), `instructions.txt`, `schema.json` (the
answer contract) and `model_answer.json`, an empty answer to fill in. Answer with one JSON object valid against the
schema and pass it to `apply`; `--next` prepares the following step when the routine goes on. A refused answer
changes nothing: `correct` reopens the step for a corrected answer, without any automatic generation.

The folder fixes the campaign once the routine is open: `--campaign` is only accepted by `preview` and `open`.
`open` takes options for long periods (`--period-days`, `--pause-period`, `--stop-on alert|critical`,
`--alert-days`, `--arcs-review-days`); run `novel routine open --help` for their meaning.
