# Persistent possessions and projects

The state keeps possessions in `inventory`, currencies in `resources` and projects in `projects`. The story and the log remain the historical proofs. A current intention never replaces an inventory or the progress of a building site.

## Inventory

Each entry contains `owner_id`, `quantity`, `equipped`, `description`, `references`, `audience`, then `location`, `condition`, `possession_status`, `quantity_precision` and `unit`. `location=on:player` designates a carried object. For an NPC, the carrier is written `on:<owner_id>` (e.g. `owner_id=merchant` gives `location=on:merchant`, never `on:npc:merchant`); an equipped object must have exactly `on:<owner_id>`. Other places are stable identifiers, without inventing a movement. A lent object keeps its real owner and the status `borrowed`. `deposited` designates a stored possession; `property_to_verify` keeps a caveat about ownership.

A quantity can be exact, approximate or unknown. For unknown, use `quantity=null` and `quantity_precision=unknown`. Do not convert an uncounted lot into zero or into an arbitrary number. Before a numbered withdrawal from an approximate or unknown lot, actually count the lot within the action and record the result. For a qualitative lot, a transformation may consume the whole lot with `quantity=null` and produce distinct qualitative lots, notably the remainder that was really observed. Do not assert an old number that was never established.

## Purse and operations

`possession_rules.currencies` defines the monetary resources, their owner and their label. When `possession_rules.currency_rates` is provided, those rates are the official conversion rates of the campaign: apply them to prices and conversions between currencies, and do not deduce a rate for the other currencies. This campaign rule does not change the sources of the canon and does not automatically convert existing balances. The purse has no double representation in the inventory.

Each event contains `asset_operations`, empty when no material movement is accomplished. Each operation contains all these fields:

```json
{"kind":"purchase","money_resource":"player_silver","money_delta":-3,"inputs":[],"outputs":[{"item_id":"fittings_purchase_1","item_json":"{\"owner_id\":\"player\",\"quantity\":2,\"equipped\":false,\"description\":\"Two fittings bought.\",\"references\":[\"current_action\"],\"audience\":[\"narrator\",\"player\"],\"location\":\"on:player\",\"condition\":\"Good condition\",\"possession_status\":\"owns\",\"quantity_precision\":\"exact\",\"unit\":\"coin\"}"}],"destination":null}
```

This example is fictional: its goods and its price are never acquired facts of the player character.

- `payment`: gain or expense for a service; `money_delta` positive or negative non-zero, no goods.
- `purchase`: negative expense and goods received in `outputs`; `inputs` empty.
- `sale`: positive gain and goods given up in `inputs`; `outputs` empty.
- `acquire`: goods found, harvested or received; no money and no inputs.
- `craft`: materials consumed in `inputs`, results in `outputs`, no money.
- `consume`: materials consumed, no result and no money.
- `gift`: permanent gift, one whole lot in `inputs`, destination `on:<new_owner>`, with no money and no `outputs`. Computes owner, location, status `owns` and `equipped=false`; keeps the other attributes. Refuses empty lots and borrowed or uncertain properties. A later event can record the actual carrying and the metadata through `changes`, without changing the owner again.
- `transfer`: one whole lot in `inputs`, explicit destination, no money and no outputs. For a partial transfer, first split the lot in a verifiable way.

A transfer already computes `equipped=false` and the new `location`, while keeping the owner, the description and the other attributes. A weapon temporarily handed over for examination requires only this operation: no manual change on its inventory entry in the same event, even to change `equipped`, add a reference or reword the description. The handover does not authorize an additional activation or magical analysis without consent.

Each input contains `item_id` and `quantity`. Each output contains `item_id` and `item_json`, the complete JSON of a new entry. Use a new identifier if an existing lot still holds goods. The engine computes the balances and the withdrawals, refuses insufficient stocks and saves everything in one transaction. Do not duplicate the computed mutations in `changes`. For an inspection, a measurement, an equipment change or a qualitative update, use `changes` with the complete, sourced object. Currencies are never modified directly in `changes`.

## Consumables and used-up lots

- A meal, a drink or a snack taken on site, in the same scene as their purchase, creates no inventory entry: only the payment (`payment`) and the story record them. A consumable only enters the inventory if it is taken along or kept for later (road provisions, potion, ration).
- A `consume`, `craft` or `sale` operation that brings a lot to zero removes it from the active inventory. The log and the story keep the history. The identifier can be reused.
- An approximate remainder (bottom of a jug, crumbs) is not tracked: once the scene is over, it disappears through `consume` with `quantity=null` (uncounted lot) or is never created.
- Never create a lot with `quantity=0`: the acquisition of an empty lot is refused.

## Projects

A project contains `title`, `owner_id`, `location`, `status`, `plan`, `completed`, `remaining`, `agreements`, `unknowns`, `inventory_ids`, `references`, `audience`. All of these fields except `title` are lists of texts. Keep the acquired steps, agreements, caveats and unknowns. Update `/projects/identifier` with the complete object after a step is accomplished. Archive through the status `finished` or `abandoned`, without deleting the history. Materials that were incorporated are consumed in the inventory; the project describes the work done.

Add the new steps to `completed` without removing or rewording the acquired entries. A count that was really performed can clarify an unknown lot with a sourced change; a movement or a consumption requires an operation. Explicit material announcements are checked in the events and the narration, but these lexical checks are not an exhaustive understanding of every sentence.

## Memory and unknowns

A missing historical source can justify `memory_request`. An uncounted stock, an undefined dimension or a price that was never discussed are checked in play if the action allows it. An authorized purchase allows asking for a quote and buying what is still missing within the available funds, without retroactively inventing an old price. A commitment exceeding the funds or changing the goal requires the player's choice. An uncertainty with no effect on what follows stays unknown.

Recovery compares the content of the proofs. Without progress, it provides `in_game_verification`: the next resume must use the available facts and carry out the ordinary checks. A new request without progress goes to `blocked`; no automatic call is made. `/reminder` targets another archive and `/retry` explicitly allows a new attempt. No additional memory limit and no remote verification call is needed.

`/inventory`, `/purse` and `/projects` are local lookups with no model and no fictional advance. `play_narrative.py --inventory --json` returns the same structured view.
