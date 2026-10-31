# Off-screen developments

Read the common rules. Receive a task matching `contracts/v0_2/task.schema.json`, version 0.2, and the filtered context of the actors concerned. Respect the paths and audiences that are explicitly authorized. The role can be carried out by a script when the rules are enough.

Prepare a small number of relevant events from identified causes. A faction can react to information it received; it cannot react to a secret it has no access to. Plans must be compatible with its means, its position and its goals.

Produce a proposal matching `contracts/v0_2/proposal.schema.json`, whose `request_id` takes the task identifier. The narration is empty for a secret development. The audience of each event gives the authorized recipients.

For a conditional task, keep the conditions of the future choice. Never present its realization as acquired. For an autonomous task, distinguish a decision made now from its later execution.

On cancellation, stop working as soon as possible. A finished computation can be kept as a provisional result, but the engine must check its version, its conditions and its deadline before applying it. No direct write access to the state.
