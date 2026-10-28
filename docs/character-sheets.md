# Character sheets

`character.json` has a `rules` block for what makes the character mechanically different. Everything in it is optional.

```json
{
  "name": "Wren Ashby",
  "hp_max": 30,
  "description": "Seventeen, apprentice ferry pilot.",
  "rules": {
    "passives": {"knot_work": {"label": "Knot work", "free": true, "description": "...", "limits": "..."}},
    "abilities": {
      "tide_sense": {"label": "Tide sense", "phase": "always", "free": true},
      "second_wind": {"label": "Second wind", "phase": "always"}
    },
    "costs": {
      "default_output_percent": 10,
      "full_power_cost_percent": {"tide_sense": 0, "second_wind": 30},
      "free_output_up_to_percent": {}
    },
    "forbidden_resources": ["player_hunger"],
    "hunger_from_health": false
  }
}
```

## What each part does

| Key | Effect |
| --- | --- |
| `passives`, `abilities` | declared on the sheet and installed as the character's powers when the campaign starts, so the abilities tab and the narrator know them from turn one. Each takes `label`, `description`, `limits`, `phase` (a free word such as `always`; the page can filter `day` and `night`) and `free` (true = costs nothing, for an ability that has no entry in `costs.full_power_cost_percent`) |
| `costs.full_power_cost_percent` | per ability, the percent of maximum HP it costs at 100% power |
| `costs.default_output_percent` | the power used when the player does not say (default 10) |
| `costs.free_output_up_to_percent` | per ability, a power under which use is free |
| `forbidden_resources` | resources the story may never create (for example a hunger gauge you removed) |
| `hunger_from_health` | when true, the narrator is given a hunger sensation derived from low HP, with no gauge |

## The cost formula

At power `p` (percent), an ability whose full power cost is `c` (percent of maximum HP) costs

```
ceiling(max_hp * c * p / 10000)    hit points
```

unless `p` is at or under the ability's free threshold, in which case it costs nothing. An ability marked `free` with no
entry in `costs` also costs nothing. With
`hp_max` 30, `second_wind` at 30% full cost costs 1 HP at the default 10% power and 9 HP at 100%. A cost of `0` is
confirmed as zero and stays zero. The cost is paid once, when the ability is used.

## From the terminal

```bash
novel character new my-novel --name Ana --hp 40 \
  --ability "tide_sense:Tide sense:0" --ability "second_wind:Second wind:30"
```

`ID:LABEL[:COST]`, where `COST` is the percent at full power (`0` makes it free). Without `--ability`, a terminal
session asks for abilities one by one. `novel validate` checks the result.

## Not available to packs yet

Charges that refill per night or per combat, cooldowns, regeneration by form, a day and night schedule and the
vitality mechanics that drive them exist in the engine's tests, but a pack cannot enable them yet. `novel validate`
reports `charges`, `regeneration`, `solar_schedule`, `version` and `cooldown` as errors rather than ignoring them.
