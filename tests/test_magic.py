"""Deterministic costs and hand-off to the narrator, with no remote call."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state,configuration
from novel_engine.magic import spell_cost,cost_table
from novel_engine.narrative import NarrativeLoop

# Fictional rule set: full-power cost per spell, as a percentage of the maximum mana.
RULES={'default_output_percent':10,
       'full_power_cost_percent':{'bolt':15,'ward':10,'drain':15,'brand':35,'swarm':40,
                                  'calm':0,'whisper':0,'vision':0},
       'free_output_up_to_percent':{'ward':10}}


class SpellCostTests(unittest.TestCase):
    def test_full_power_default_and_fractional_rounding(self):
        expected={'bolt':(15,2),'ward':(10,0),'drain':(15,2),'brand':(35,4),'swarm':(40,4),'calm':(0,0)}
        for spell,(full,low) in expected.items():
            with self.subTest(spell=spell):
                self.assertEqual(spell_cost(spell,100,RULES,100),full)
                self.assertEqual(spell_cost(spell,100,RULES),low)
        self.assertEqual(spell_cost('bolt',137,RULES,25),6)
        self.assertEqual(spell_cost('bolt',100,RULES,'0.1'),1)

    def test_free_threshold_only_applies_up_to_its_power(self):
        self.assertEqual(spell_cost('ward',100,RULES,10),0)
        self.assertEqual(spell_cost('ward',100,RULES,'10.5'),2)
        bad={**RULES,'free_output_up_to_percent':{'ward':101}}
        with self.assertRaises(ValueError):spell_cost('ward',100,bad,10)

    def test_unknown_cost_and_invalid_power_cannot_be_treated_as_free(self):
        for output in (0,-1,101,'NaN','Infinity','invalid'):
            with self.subTest(output=output),self.assertRaises(ValueError):
                spell_cost('bolt',100,RULES,output)
        with self.assertRaises(ValueError):spell_cost('unknown_spell',100,RULES)
        with self.assertRaises(ValueError):spell_cost('bolt',0,RULES)
        with self.assertRaises(ValueError):spell_cost('bolt',True,RULES)
        with self.assertRaises(ValueError):spell_cost('bolt',100,{**RULES,'full_power_cost_percent':{'bolt':101}})

    def test_declared_free_spells_remain_free_at_any_output_and_health_scale(self):
        for spell in ('calm','whisper','vision'):
            for hp in (100,10000):
                for output in (10,25,100):
                    with self.subTest(spell=spell,hp=hp,output=output):
                        self.assertEqual(spell_cost(spell,hp,RULES,output),0)

    def test_cost_table_lists_every_spell_for_each_standard_power(self):
        table=cost_table(100,RULES)
        self.assertEqual((table['mana_max'],table['default_power_pct']),(100,10))
        self.assertEqual(set(table['mana_cost_per_power_pct']),{'10','25','50','100'})
        for costs in table['mana_cost_per_power_pct'].values():
            self.assertEqual(set(costs),set(RULES['full_power_cost_percent']))
        self.assertEqual(table['mana_cost_per_power_pct']['100']['brand'],35)
        # A default power outside the standard list is added to the table.
        self.assertIn('5',cost_table(100,{**RULES,'default_output_percent':5})['mana_cost_per_power_pct'])

    def test_wounded_character_prompt_uses_maximum_mana(self):
        state=initial_state()
        state['resources']['player_hp']=20
        state['resources']['player_hp_max']=100
        state['resources'].update(player_mana=70, player_mana_max=100)
        config=configuration()
        config['spell_cost_rules']=RULES
        base=ROOT/'.tmp-tests';base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            c=Campaign(Path(temporary)/'novel_engine.sqlite').create(state,config)
            c.open_scene('A target waits.')
            prepared=c.prepare_turn('I cast a bolt at low power.')
            packet=json.loads(NarrativeLoop(c,None).build_prompt(prepared).split('\nCURRENT TURN\n')[1])
            self.assertEqual(packet['campaign_rules']['spell_cost_rules']['default_output_percent'],10)
            table=packet['computed_spell_costs']
            self.assertEqual(table['mana_max'],100)
            self.assertEqual(table['mana_cost_per_power_pct']['100']['bolt'],15)
            self.assertEqual(table['mana_cost_per_power_pct']['10']['bolt'],2)
            self.assertEqual(c.state()['resources']['player_hp'],20)


if __name__=='__main__':
    unittest.main(verbosity=2)
