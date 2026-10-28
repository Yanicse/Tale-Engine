You extract a sourced canon from an excerpt of a novel. Use only the excerpt below.
Return ONE JSON object, no commentary:
{{"entities": [{{"id": "snake_case_id", "kind": "character|place|faction|object|concept|event", "name": "Display name", "aliases": ["..."]}}],
 "claims": [{{"subject": "entity id", "statement": "one self-contained factual sentence, in your own words", "predicate": "short_snake_case_label", "quote": "the exact words of the excerpt that support it, at most 25 words", "page": <page number>}}]}}
Rules: the statement is a paraphrase; the quote is copied exactly from the excerpt (a claim whose quote
cannot be found in the excerpt is discarded); at most {limit} characters per statement;
only facts the text states; one fact per claim; every claim subject must be a listed entity;
pages available: {first} to {last}.

EXCERPT
{excerpt}
