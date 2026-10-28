"""The narration language is a campaign setting injected into the narrator and planner instructions."""
import tempfile
import unittest
from pathlib import Path

from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, initial_state
from novel_engine.narrative import NarrativeLoop
from novel_engine.planner import instructions_for

ROOT = Path(__file__).resolve().parents[1]


class NarrationLanguageTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)

    def tearDown(self):
        self.temp.cleanup()

    def prompt(self, **config):
        campaign = Campaign(Path(self.temp.name) / 'language.sqlite').create(
            initial_state(), {**configuration(), **config})
        campaign.open_scene('You stand at the gate.')
        prepared = campaign.prepare_turn('I look around.', narrative_memory=True)
        return NarrativeLoop(campaign, None).build_prompt(prepared)

    def test_default_language_is_english(self):
        self.assertIn('NARRATION LANGUAGE: write the narration and every free-text field in English.',
                      self.prompt())

    def test_campaign_setting_changes_the_language(self):
        prompt = self.prompt(narration_language='French')
        self.assertIn('in French. Keep names and quotations in their source language.', prompt)
        self.assertNotIn('in English. Keep names', prompt)

    def test_planner_instructions_carry_the_language(self):
        self.assertTrue(instructions_for({'narration_language': 'German'}).rstrip().endswith(
            'Narration language: German.'))
        self.assertTrue(instructions_for({}).rstrip().endswith('Narration language: English.'))


if __name__ == '__main__':
    unittest.main()
