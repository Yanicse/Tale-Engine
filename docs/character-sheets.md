# Character sheets

`character.json` defines the character, hit points, mana and optional ability rules. Every new pack campaign uses
validated mechanical operations. Magic spends mana; it never spends hit points.

```json
{
  "name": "Wren Ashby",
  "hp_max": 30,
  "mana_max": 30,
  "description": "Seventeen, apprentice ferry pilot.",
  "rules": {
    "passives": {"knot_work": {"label": "Knot work", "free": true}},
    "abilities": {
      "tide_sense": {"label": "Tide sense", "free": true},
      "second_wind": {"label": "Second wind"}
    },
    "costs": {
      "default_output_percent": 100,
      "full_power_cost_percent": {"tide_sense": 0, "second_wind": 30},
      "free_output_up_to_percent": {}
    },
    "forbidden_resources": []
  }
}
```

## Health and mana

`hp_max` is a positive integer; `mana_max` is a non-negative integer. Both default to 100. New campaigns start with
full reserves. `mana_max: 0` works for a character with only free abilities. The description is saved with the campaign
and supplied to the narrator. Editing a sheet later affects future campaigns.

Neither reserve recovers from passing time, reopening the page or resuming a chat. `damage`, `heal` and `restore_mana`
operations apply explicit, sourced changes. Recovery is capped at the maximum, and healing cannot revive a dead character.

## Abilities and costs

`passives` and `abilities` map ids to sheets. Sheets accept `label`, `description`, `limits`, `free` and an optional
`phase` (`always`, `day` or `night`). A day/night phase requires an explicit `solar_schedule`.
Every active ability must declare a mana cost or `free: true`. Passive sheets do not cost resources.

At power `p` (percent), an ability whose full-power cost is `c` (percent of maximum mana) costs:

```
ceiling(maximum_mana * c * p / 10000)    mana
```

The default power is `costs.default_output_percent`, or 100. `costs.free_output_up_to_percent` gives an inclusive free
threshold for each listed ability. With 30 maximum mana and a 30% full-power cost, Second wind costs 9 mana at 100%
power or 1 mana at 10% power. A confirmed zero stays zero. `costs.full_power_cost_percent` must name an existing ability.

Each use needs one `ability` mechanical operation, including free uses. The engine checks the cost against the current
mana, computes the deduction and saves it in the same transaction as the consequences. A failed turn spends nothing;
a replay spends nothing twice. Spending the last mana point is allowed and never hurts the character.

The local English narration check also rejects explicit named casts without a matching operation. This is a targeted
check, not a general interpretation of every possible sentence; the structured operations remain the authoritative record.

## Charges and cooldowns

Packs can configure optional charge resources and persistent cooldowns:

```json
{
  "solar_schedule": {"first_sunrise_minute": 360, "first_sunset_minute": 1080},
  "charges": {"player_gales_left": {"period": "combat", "ability": "gale", "max": 1}},
  "abilities": {
    "gale": {
      "label": "Gale",
      "free": true,
      "cooldown": {"resource": "player_gale_ready", "seconds": 60}
    }
  }
}
```

This is an example `rules` block. A charge period is `combat` or `night`. Night charges require `solar_schedule` and
return only when a fictional sunset is crossed. Combat charges return on a real `combat_start`, never a reload.
A charge's optional `phase` restricts its consumption to `day` or `night` and requires `solar_schedule`.
Omitting the phase, or choosing `always`, permits consumption at any time without a solar schedule.
This applies to ability, resurrection and death-prevention charges; a refused use keeps the whole turn unchanged.
A cooldown stores an absolute fictional deadline. Charge and cooldown resources must have distinct ids and cannot
replace health, mana or money. `novel validate` checks the configuration before campaign creation.

`catalogue` can give an ability `aliases`, `offensive` and `bound` flags for targeted context selection. `parameters` and
`scaling` describe effects at reduced power. `forbidden_resources` lists resource names the story must never create.
Automatic regeneration is not supported.

For `effect: "death_prevention"`, the active cast saves its scaled damage capacity and survival HP, so another
ability's parameters cannot replace them after reopening. Survival HP and duration round up to whole hit points
and fictional seconds; damage capacity rounds down. A later activation replaces those active values.

## From the terminal

```bash
novel character new my-novel --name Ana --hp 40 --mana 60 \
  --ability "tide_sense:Tide sense:0" --ability "second_wind:Second wind:30"
```

`ID:LABEL[:COST]` uses the percent of maximum mana at full power. Omitting COST makes the ability free. Without
`--ability`, an interactive terminal asks for abilities. `novel validate` checks the result.

## Existing saves

No existing campaign is rewritten automatically. The former `vitality-1.0` mode is refused with an explicit message;
create a new campaign from an updated pack to use mana.
