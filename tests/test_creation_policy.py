"""Common creation policy: independent of the transport and of the installed mechanics."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from novel_engine import creation_policy
from novel_engine.narrative_preparation import ALL_MODULES, build_instructions


class CreationPolicyTests(unittest.TestCase):
    def test_policy_texts_are_plain_single_strings_without_forbidden_punctuation(self):
        for text in (creation_policy.INSTRUCTIONS, creation_policy.TRANSPORT):
            self.assertIsInstance(text, str)
            self.assertNotIn(chr(0x2014), text)
            self.assertTrue(text.endswith(' '), 'blocks are concatenated without separator')
            text.encode('ascii')

    def test_policy_keeps_offers_and_names_from_creating_facts(self):
        text = creation_policy.INSTRUCTIONS
        for phrase in ('A testimony stays attributed', 'proposed until the player explicitly accepts',
                       'An announced bounty credits neither purse nor inventory',
                       'A name alone creates no relation', 'The planner\'s reserves stay hypothetical',
                       'not a documentary blocker', 'Do not contradict the established canon'):
            self.assertIn(phrase, ' '.join(text.split()), phrase)

    def test_transport_declares_the_creation_fields_and_quest_states(self):
        text = ' '.join(creation_policy.TRANSPORT.split())
        for phrase in ('creation_updates', 'entity_id', 'origin=campaign', 'proposed, accepted, resolved, refused or abandoned',
                       'accepted requires an explicit decision', 'empty creation_updates', 'No implicit payment or agenda change'):
            self.assertIn(phrase, text, phrase)

    def test_instructions_are_always_in_the_prompt_but_transport_is_not_part_of_them(self):
        for modules in (['memory'], list(ALL_MODULES)):
            built = build_instructions({'campaign_rules': {}}, modules, 180)
            self.assertIn(creation_policy.INSTRUCTIONS, built)
            self.assertNotIn(creation_policy.TRANSPORT, built)

    def test_transport_is_added_only_for_the_creation_format(self):
        import tempfile
        from novel_engine.campaign import Campaign
        from novel_engine.contracts import ROOT
        from novel_engine.demo import initial_state, configuration
        from novel_engine.narrative import NarrativeLoop
        (ROOT / '.tmp-tests').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp-tests') as temporary:
            for format_id, expected in (('1', True), (None, False)):
                with self.subTest(creation_format=format_id):
                    config = configuration()
                    if format_id:
                        config['creation_format'] = format_id
                    campaign = Campaign(Path(temporary) / f'creation{format_id}.sqlite').create(initial_state(), config)
                    campaign.open_scene('The innkeeper waits.')
                    prepared = campaign.prepare_turn('Hello.', narrative_memory=True)
                    prompt = NarrativeLoop(campaign, None).build_prompt(prepared)
                    self.assertEqual(creation_policy.TRANSPORT in prompt, expected)
                    self.assertIn(creation_policy.INSTRUCTIONS, prompt)


if __name__ == '__main__':
    unittest.main()
