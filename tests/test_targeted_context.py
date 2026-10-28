"""Targeted context, optional reductions and calculated consequences, with no remote call."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from novel_engine.campaign import Campaign
from novel_engine.consequences import FINAL_STATE
from novel_engine.demo import initial_state, configuration, event, proposal
from novel_engine.mechanics import instant
from novel_engine.narrative import NarrativeLoop, packet_references
from novel_engine.narrative_preparation import select_modules, select_documents
from novel_engine.targeted_context import analysis, deduplicate_documents, project, reduction, serialize
import test_reliability as reliability

NAMES = ('danger_hp', 'agenda_risk', 'recent_threat', 'current_action', 'inventory', 'planification')
CURRENT_TURN = '\nCURRENT TURN\n'

# Fictional abilities. A catalogue entry makes the sheet optional in the prompt; mind_link has none.
RULES = deepcopy(reliability.MECHANICS_RULES)
RULES['abilities']['bolt']['catalogue'] = {'offensive': True, 'aliases': ['lightning']}
RULES['abilities']['swarm']['catalogue'] = {'offensive': True, 'aliases': ['locust cloud']}
RULES['abilities']['ward']['catalogue'] = {'offensive': False, 'aliases': ['guard spell']}
RULES['abilities']['rampart']['catalogue'] = {'offensive': False, 'bound': True}
COSTS = deepcopy(reliability.COST_RULES)
POWERS = ('bolt', 'swarm', 'ward', 'rampart', 'mind_link')


def sheet(key, **fields):
    return {'actor_id': 'player', 'audience': ['narrator', 'player'], 'references': ['demo:initial_configuration'],
            'description': f'Fictional power {key}. Second sentence with the details.', 'limits': 'None.', **fields}


def reductions(names):
    return patch.dict('os.environ', {'NOVEL_TARGETED_CONTEXT': '1', 'NOVEL_CONTEXT_REDUCTIONS': names})


def sent(prompt):
    return json.loads(prompt.rsplit(CURRENT_TURN, 1)[1])


class Fixture(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp-tests').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp-tests')
        self.counter = 0
        self.c = self.make()

    def tearDown(self):
        self.temp.cleanup()

    def make(self):
        """New fictional campaign: five powers, ten objects, a full character configuration."""
        self.counter += 1
        state, config = initial_state('targeted'), configuration()
        state['resources'].update(player_hp=100, player_hp_max=100, night_id=1, player_resurrections_left=1,
                                  player_domains_left=1, player_combat_id=0, player_combat_active=0,
                                  player_death_refusal_available=1, player_rampart_available=1)
        state['powers'] = {key: sheet(key) for key in POWERS}
        state['inventory'] = {name: reliability.item(1) for name in
                              ('lantern', 'silver_plate', 'old_beam', 'rope', 'bread', 'map', 'knife', 'flint',
                               'blanket', 'bucket')}
        state['inventory']['lantern']['equipped'] = True
        state['inventory']['old_beam'] = {**reliability.item(None, 'unknown'), 'location': 'on:harbor'}
        config['character_rules'] = deepcopy(RULES)
        config['spell_cost_rules'] = deepcopy(COSTS)
        campaign = Campaign(Path(self.temp.name) / f'targeted{self.counter}.sqlite').create(state, config)
        campaign.open_scene('The guard waits at the door.')
        return campaign

    def packet(self, action, campaign=None):
        campaign = campaign or self.c
        prepared = campaign.prepare_turn(action, narrative_memory=True)
        with reductions(''):
            return NarrativeLoop(campaign, None)._prompt_packet(prepared, True)[0], prepared


class TargetedContextTests(Fixture):
    def compare(self, action, label):
        campaign = self.make()
        prepared = campaign.prepare_turn(action, narrative_memory=True)
        prepared.pop('npc_sheets', None)
        prepared.pop('place_reminders', None)
        untouched = deepcopy(prepared)
        loop = NarrativeLoop(campaign, None)
        with patch.dict('os.environ', {'NOVEL_TARGETED_CONTEXT': '0'}):
            before = loop.build_prompt(prepared)
        with reductions(''):
            after = loop.build_prompt(prepared)
        self.assertEqual(prepared, untouched, label)
        a, b = sent(before), sent(after)
        for key in ('resources', 'effects', 'inventory', 'projects', 'commitments', 'knowledge', 'positions'):
            self.assertEqual(a['current_state'].get(key), b['current_state'].get(key), key)
        for key in ('world_agenda', 'world_planning', 'recent_exchanges', 'recalled_exchanges', 'extra_facts',
                    'unknowns', 'documentary_canon', 'computed_spell_costs'):
            self.assertEqual(a.get(key), b.get(key), key)
        self.assertEqual(set(a['allowed_references']), set(b['allowed_references']))
        self.assertEqual(set(b['allowed_references']), packet_references(b))
        for key, value in b['current_state'].get('powers', {}).items():
            self.assertEqual(value, a['current_state']['powers'][key])
        for key, value in b.get('power_catalogue', {}).items():
            original = a['current_state']['powers'][key]
            self.assertEqual(value['references'], original['references'])
            self.assertEqual(value['audience'], original['audience'])
        self.assertEqual(sum(v['utf8_bytes'] for v in loop.last_metrics['context_blocks'].values()), len(after.encode()))
        self.assertLess(len(after.encode()), len(before.encode()), label)
        return a, b

    def test_dialogue_and_changed_or_unknown_power_stays_complete(self):
        a, b = self.compare('I ask for news about the furniture.', 'dialogue')
        self.assertIn('bolt', b['power_catalogue'])
        self.assertEqual(set(b['current_state']['powers']) | set(b['power_catalogue']), set(POWERS))
        original = deepcopy(a)
        original['current_state']['powers']['new_power'] = sheet('new_power', description='New passive defense.')
        original['current_state']['powers']['bolt']['limits'] += ' Newly confirmed exception.'
        original['campaign_rules']['character_rules']['abilities']['new_power'] = {'phase': 'always'}
        with reductions(''):
            projected = project(original)
        self.assertEqual(projected['current_state']['powers']['new_power'], original['current_state']['powers']['new_power'])
        self.assertEqual(projected['current_state']['powers']['mind_link'], original['current_state']['powers']['mind_link'])
        # A changed sheet is never rewritten: it is either whole or replaced by an exact catalogue entry.
        self.assertIn(projected['current_state']['powers'].get('bolt', original['current_state']['powers']['bolt']),
                      [original['current_state']['powers']['bolt']])
        documents = [{'source': 'fictional', 'text': 'Exact rule.\n\nNew essential reserve.'}]
        reduced = deduplicate_documents(documents, 'Exact rule.')
        self.assertIn('New essential reserve.', reduced[0]['text'])
        self.assertNotIn('Exact rule.', reduced[0]['text'])
        self.assertEqual(documents[0]['text'], 'Exact rule.\n\nNew essential reserve.')

    def test_purchase_preserves_money_inventory_and_serialization(self):
        self.compare('I pay three coins for the bread.', 'purchase')
        value = {'text': 'A sentence with spaces, "quotes" and a caveat: not confirmed.', 'quantity': None}
        self.assertEqual(json.loads(serialize(value)), value)
        self.assertEqual(serialize({'a': 1, 'b': [1, 2]}), '{"a":1,"b":[1,2]}')
        with self.assertRaises(ValueError):
            serialize({'text': chr(0x2014)})

    def test_spell_preserves_full_selected_sheet_costs_and_explicit_recovery(self):
        a, b = self.compare('I cast lightning at 10 %.', 'named_spell')
        self.assertEqual(b['current_state']['powers']['bolt'], a['current_state']['powers']['bolt'])
        self.assertEqual(b['campaign_rules']['spell_cost_rules'], a['campaign_rules']['spell_cost_rules'])
        a['player_input'] = 'I ask for news about the furniture.'
        a['memory_recovery'] = {'query': 'power:swarm'}
        with reductions(''):
            self.assertIn('swarm', project(a)['current_state']['powers'])
        campaign = self.make()
        rid = campaign.begin_action('I ask for news about the furniture.')['request_id']
        recovered = campaign.recover_memory(rid, 'power:swarm')
        self.assertEqual(recovered['recovered_sources'], 1)
        prepared = campaign.prepare_turn(narrative_memory=True)
        with reductions(''):
            prompt = NarrativeLoop(campaign, None).build_prompt(prepared)
        self.assertEqual(sent(prompt)['current_state']['powers']['swarm'], campaign.state()['powers']['swarm'])

    def test_long_action_hidden_threat_and_ambiguous_action_keep_all_powers(self):
        a, b = self.compare('I work for 24 hours.', 'long_action')
        # A bound ability (here the rampart) stays individually retrievable even in a long scene.
        kept = {key: value for key, value in a['current_state']['powers'].items() if key != 'rampart'}
        self.assertEqual(b['current_state']['powers'], kept)
        self.assertEqual(set(b['power_catalogue']), {'rampart'})
        for action, threat in [('I go on.', False), ('Hello.', True)]:
            packet = deepcopy(a)
            packet['player_input'] = action
            if threat:
                packet['world_agenda']['events'].append({'title': 'Secret ambush', 'interrupt': False, 'kind': 'project'})
            with reductions(''):
                projected = project(packet)
            self.assertEqual(projected['current_state']['powers'], kept, action)

    def test_projection_does_not_mutate_the_packet(self):
        packet, _ = self.packet('I ask for news about the furniture.')
        original = deepcopy(packet)
        with reductions('inventory,index,planification'):
            project(packet)
        self.assertEqual(packet, original)

    def test_disabled_projection_changes_nothing(self):
        packet, _ = self.packet('I ask for news about the furniture.')
        with patch.dict('os.environ', {'NOVEL_TARGETED_CONTEXT': '0'}):
            self.assertFalse(reduction('inventory'))
        with reductions(''):
            result = project(packet)
        self.assertIn('power_catalogue', result)
        self.assertEqual(result['current_state']['inventory'], packet['current_state']['inventory'])


class ReductionTests(Fixture):
    def test_every_reduction_can_be_disabled_separately(self):
        with patch.dict('os.environ', {'NOVEL_TARGETED_CONTEXT': '0'}):
            self.assertFalse(reduction('inventory'))
        for name in NAMES:
            with reductions(name):
                self.assertTrue(reduction(name))
                self.assertFalse(any(reduction(other) for other in NAMES if other != name))
        with reductions(''):
            packet, _ = self.packet('I look at the lantern.')
            result = project(packet)
            for key in ('object_catalogue', 'inventory_projection'):
                self.assertNotIn(key, result)
            self.assertEqual(result['current_state']['inventory'], packet['current_state']['inventory'])
            self.assertEqual(result['world_planning'], packet['world_planning'])

    def test_settings_file_declares_every_reduction_as_a_boolean(self):
        settings = json.loads((ROOT / 'novel_engine/targeted_context_settings.json').read_text(encoding='utf-8'))
        self.assertIsInstance(settings['active'], bool)
        for name in (*NAMES, 'index'):
            self.assertIsInstance(settings['reductions'][name], bool, name)
        # The environment override wins over the file and the file is only read when no override is set.
        with patch.dict('os.environ', {'NOVEL_TARGETED_CONTEXT': '1', 'NOVEL_CONTEXT_REDUCTIONS': 'inventory'}):
            self.assertTrue(reduction('inventory'))
            self.assertFalse(reduction('index'))

    def test_inventory_catalogue_is_exact_and_recoverable(self):
        packet, _ = self.packet('I look at the lantern.')
        original = deepcopy(packet)
        with reductions('inventory'):
            result = project(packet)
        self.assertEqual(packet, original)
        items = original['current_state']['inventory']
        kept, catalogue = result['current_state']['inventory'], result['object_catalogue']
        self.assertIn('lantern', kept)
        self.assertTrue(catalogue)
        self.assertEqual(set(kept) | set(catalogue), set(items))
        self.assertFalse(set(kept) & set(catalogue))
        for key, value in kept.items():
            self.assertEqual(value, items[key])
        for key, value in items.items():
            if value.get('equipped'):
                self.assertIn(key, kept)
        for key, value in catalogue.items():
            self.assertEqual(value['references'], items[key]['references'])
            self.assertEqual(value['audience'], items[key]['audience'])
            self.assertEqual(value['quantity'], items[key]['quantity'])
            self.assertEqual(value['location'], items[key].get('location'))
        self.assertIn('silver_plate', catalogue)
        self.assertIsNone(catalogue['old_beam']['quantity'], 'an unknown quantity does not become zero')
        for key in ('resources', 'effects', 'projects', 'commitments', 'knowledge', 'positions'):
            self.assertEqual(result['current_state'].get(key), original['current_state'].get(key), key)
        self.assertEqual(result['world_agenda'], original['world_agenda'])
        # The omitted sheet comes back through the planned request, then whole in the prompt.
        packet['memory_recovery'] = {'query': 'object:silver_plate'}
        with reductions('inventory'):
            self.assertIn('silver_plate', project(packet)['current_state']['inventory'])
        rid = self.c.resume()['session']['pending_action']['request_id']
        recovered = self.c.recover_memory(rid, 'object:silver_plate')
        self.assertEqual(recovered['recovered_sources'], 1)
        prepared = self.c.prepare_turn(narrative_memory=True)
        with reductions('inventory'):
            prompt = NarrativeLoop(self.c, None).build_prompt(prepared)
        self.assertEqual(sent(prompt)['current_state']['inventory']['silver_plate'], self.c.state()['inventory']['silver_plate'])

    def test_general_inventory_request_keeps_everything(self):
        packet, _ = self.packet('I go through my belongings.')
        with reductions('inventory'):
            result = project(packet)
        self.assertEqual(result['current_state']['inventory'], packet['current_state']['inventory'])
        self.assertNotIn('object_catalogue', result)

    def test_small_inventory_is_never_reduced(self):
        packet, _ = self.packet('Hello there.')
        packet['current_state']['inventory'] = dict(list(packet['current_state']['inventory'].items())[:6])
        with reductions('inventory'):
            result = project(packet)
        self.assertEqual(result['current_state']['inventory'], packet['current_state']['inventory'])
        self.assertNotIn('object_catalogue', result)

    def test_planning_summarizes_only_unrelated_active_situations(self):
        packet, _ = self.packet('I talk to the guard at the gate.')
        minute = packet['current_state']['world_minute']
        base = {'status': 'active', 'interrupt': False, 'revision': 0, 'expected_revision': 0, 'goal': 'Long text.',
                'means': 'Means.', 'conditions': [], 'references': ['x:1']}
        packet['world_planning'] = {'situations': [
            {**base, 'id': 'far', 'title': 'Distant cargo', 'actor': 'A captain', 'place': 'Wyrmouth harbor',
             'due_minute': minute + 99999, 'reference': 'plan:far:0'},
            {**base, 'id': 'near', 'title': 'Herald visit', 'actor': 'A herald', 'place': 'Palace',
             'due_minute': minute + 60, 'reference': 'plan:near:0'},
            {**base, 'id': 'present', 'title': 'The nervous guard', 'actor': 'The guard', 'place': 'The gate',
             'due_minute': None, 'reference': 'plan:present:0'},
            {**base, 'id': 'reserve', 'title': 'Hypothesis', 'status': 'reserve', 'actor': 'Someone', 'place': 'Elsewhere',
             'due_minute': None, 'reference': 'plan:reserve:0'}],
            'private_notes': ['Private note.'], 'player_summaries': ['Summary.']}
        original = deepcopy(packet)
        with reductions('planification'):
            result = project(packet)
        self.assertEqual(packet, original)
        situations = {item['id']: item for item in result['world_planning']['situations']}
        self.assertEqual(result['world_planning']['summarized_situations'], ['far'])
        self.assertNotIn('goal', situations['far'])
        self.assertEqual(situations['far']['reference'], 'plan:far:0')
        self.assertEqual(situations['far']['due_minute'], minute + 99999)
        for key in ('near', 'present', 'reserve'):
            self.assertEqual(situations[key], next(i for i in original['world_planning']['situations'] if i['id'] == key))
        self.assertEqual(result['world_planning']['private_notes'], ['Private note.'])
        self.assertEqual(result['world_planning']['player_summaries'], ['Summary.'])

    def test_danger_threshold_and_agenda_risk_are_tightened(self):
        packet, _ = self.packet('Hello.')
        resources = packet['current_state']['resources']
        resources['player_combat_active'] = False
        resources['player_hp'] = int(resources['player_hp_max'] * 0.9)
        packet['world_agenda'] = {'events': [{'kind': 'appointment', 'interrupt': True, 'title': 'Visit'}]}
        with reductions(''):
            reasons, _ = analysis(packet)
        self.assertIn('danger:hp_not_max', reasons)
        self.assertIn('danger:agenda', reasons)
        with reductions('danger_hp,agenda_risk'):
            reasons, _ = analysis(packet)
            self.assertFalse([r for r in reasons if r.startswith('danger')])
            resources['player_hp'] = int(resources['player_hp_max'] * 0.5)
            self.assertIn('danger:hp_below_threshold', analysis(packet)[0])
            resources['player_hp'] = resources['player_hp_max']
            packet['world_agenda'] = {'events': [{'kind': 'risk', 'interrupt': False, 'title': 'Boredom'}]}
            self.assertIn('danger:agenda', analysis(packet)[0])

    def test_threat_is_read_in_the_last_three_exchanges_only(self):
        packet, _ = self.packet('I measure the wall.')
        packet['recent_exchanges'] = [{'role': 'narrator', 'content': 'An old ambush.', 'seq': 1}] + [
            {'role': 'player' if n % 2 else 'narrator', 'content': 'Nothing special.', 'seq': n} for n in range(2, 6)]
        packet['world_agenda'] = {'events': []}
        packet['world_planning'] = {}
        packet['current_state']['resources']['player_combat_active'] = False
        packet['current_state']['resources']['player_hp'] = packet['current_state']['resources']['player_hp_max']
        with reductions(''):
            self.assertIn('danger:threat_word', analysis(packet)[0])
        with reductions('recent_threat'):
            self.assertNotIn('danger:threat_word', analysis(packet)[0])
            packet['recent_exchanges'][-1]['content'] = 'A sudden attack.'
            self.assertIn('danger:threat_word', analysis(packet)[0])

    def test_current_action_catalogues_only_combat_spells_without_danger(self):
        packet, _ = self.packet('I measure the kitchen wall.')
        packet['current_state']['resources']['player_hp'] = packet['current_state']['resources']['player_hp_max']
        packet['current_state']['resources']['player_combat_active'] = False
        packet['world_agenda'] = {'events': []}
        packet['world_planning'] = {}
        packet['recent_exchanges'] = [{'role': 'narrator', 'content': 'The kitchen is calm.', 'seq': 1}]
        original = deepcopy(packet['current_state']['powers'])
        with reductions('current_action'):
            result = project(packet)
        powers, catalogue = result['current_state']['powers'], result['power_catalogue']
        self.assertEqual(set(catalogue), {'bolt', 'swarm', 'rampart'})
        self.assertEqual(set(catalogue) | set(powers), set(original))
        for key in ('ward', 'mind_link'):
            self.assertEqual(powers[key], original[key], key)
        # Danger, generic power word or named spell: the sheets come back.
        for action in ('I prepare for combat.', 'I cast a spell.'):
            packet['player_input'] = action
            with reductions('current_action'):
                kept = project(packet)['current_state']['powers']
            self.assertTrue({'bolt', 'swarm', 'ward', 'mind_link'} <= set(kept), action)
        packet['player_input'] = 'I measure the wall while thinking about lightning.'
        with reductions('current_action'):
            self.assertEqual(project(packet)['current_state']['powers']['bolt'], original['bolt'])

    def test_bound_ability_keeps_its_sheet_when_an_active_effect_cites_it(self):
        packet, _ = self.packet('Hello.')
        packet['current_state']['effects'] = {'stance': {'description': 'The rampart is raised.'}}
        with reductions(''):
            result = project(packet)
        self.assertIn('rampart', result['current_state']['powers'])
        self.assertNotIn('rampart', result.get('power_catalogue', {}))

    def test_catalogued_sheets_leave_the_ability_registry_with_them(self):
        packet, _ = self.packet('Hello.')
        with reductions(''):
            result = project(packet)
        abilities = result['campaign_rules']['character_rules']['abilities']
        for key in result['power_catalogue']:
            self.assertNotIn(key, abilities)
        self.assertIn('mind_link', abilities)
        self.assertEqual(result['power_projection']['catalogued_sheets'], list(result['power_catalogue']))

    def test_full_prompt_keeps_references_state_and_original_packet(self):
        prepared = self.c.prepare_turn('I look at the lantern.', narrative_memory=True)
        untouched = deepcopy(prepared)
        state_before = self.c.state()
        loop = NarrativeLoop(self.c, None)
        with reductions(''):
            plain = loop.build_prompt(prepared)
        with reductions(','.join(NAMES)):
            reduced = loop.build_prompt(prepared)
        self.assertEqual(prepared, untouched)
        self.assertEqual(self.c.state(), state_before)
        self.assertLess(len(reduced.encode()), len(plain.encode()))
        a, b = sent(plain), sent(reduced)
        for key in ('resources', 'effects', 'projects', 'commitments', 'knowledge', 'positions'):
            self.assertEqual(a['current_state'].get(key), b['current_state'].get(key), key)
        for key in ('world_agenda', 'recent_exchanges', 'recalled_exchanges', 'extra_facts', 'unknowns',
                    'documentary_canon', 'computed_spell_costs'):
            self.assertEqual(a.get(key), b.get(key), key)
        self.assertEqual(set(b['allowed_references']), packet_references(b))
        for key, value in b['campaign_rules']['character_rules']['abilities'].items():
            self.assertEqual(value, a['campaign_rules']['character_rules']['abilities'][key])
        self.assertEqual(b['campaign_rules']['spell_cost_rules'], a['campaign_rules']['spell_cost_rules'])
        omitted = set(a['current_state']['inventory']) - set(b['current_state']['inventory'])
        self.assertEqual(omitted, set(b.get('object_catalogue', {})))
        # Local validation stays based on the complete save, never on the prompt.
        self.assertEqual(set(self.c.state()['inventory']), set(a['current_state']['inventory']))


class IndexTests(Fixture):
    def test_index_is_a_separate_switch(self):
        with reductions('inventory'):
            self.assertFalse(reduction('index'))
        with reductions('index'):
            self.assertTrue(reduction('index'))
            self.assertFalse(reduction('inventory'))

    def test_index_catalogues_powers_items_projects_with_one_line(self):
        packet, _ = self.packet('I watch the surroundings, ready to defend myself against an attack.')
        packet['current_state']['projects'] = {'far_forge': {
            'title': 'Forge of the capital', 'status': 'in_progress', 'location': 'capital',
            'references': ['x:1'], 'audience': ['player']}}
        original = deepcopy(packet)
        self.assertTrue(any(r.startswith('danger:threat') for r in analysis(packet)[0]))
        with reductions('index'):
            result = project(packet)
        self.assertEqual(packet, original)
        powers, catalogue = result['current_state']['powers'], result['power_catalogue']
        self.assertTrue(catalogue)
        self.assertEqual(set(powers) | set(catalogue), set(original['current_state']['powers']))
        for key, value in catalogue.items():
            self.assertLessEqual(len(value['summary']), 90)
            self.assertEqual(value['audience'], original['current_state']['powers'][key]['audience'])
        self.assertIn('object_catalogue', result)
        self.assertIn('index_instruction', result)
        for key, value in result.get('project_catalogue', {}).items():
            self.assertEqual(value['references'], original['current_state']['projects'][key]['references'])
        self.assertIn('far_forge', result['project_catalogue'])
        for key in ('resources', 'effects', 'commitments', 'knowledge', 'positions'):
            self.assertEqual(result['current_state'].get(key), original['current_state'].get(key), key)
        self.assertEqual(result['world_agenda'], original['world_agenda'])

    def test_index_keeps_all_powers_in_active_combat(self):
        packet, _ = self.packet('I watch the surroundings.')
        packet['current_state']['resources']['player_combat_active'] = True
        with reductions('index'):
            result = project(packet)
        kept = {key: value for key, value in packet['current_state']['powers'].items() if key != 'rampart'}
        self.assertEqual(result['current_state']['powers'], kept)
        self.assertEqual(set(result['power_catalogue']), {'rampart'})

    def test_index_project_is_catalogued_and_recoverable(self):
        packet, _ = self.packet('I greet the passerby.')
        packet['recent_exchanges'] = [{'seq': 1, 'role': 'narrator', 'content': 'The passerby greets you.'}]
        packet['current_state']['projects'] = {'far_forge': {
            'title': 'Forge of the capital', 'status': 'in_progress', 'location': 'capital',
            'references': ['x:1'], 'audience': ['player']}}
        with reductions('index'):
            catalogue = project(packet)['project_catalogue']
        self.assertEqual(catalogue['far_forge']['title'], 'Forge of the capital')
        packet['memory_recovery'] = {'query': 'project:far_forge'}
        with reductions('index'):
            self.assertIn('far_forge', project(packet)['current_state']['projects'])
        with self.c.transaction() as con:
            state = self.c._state(con)
            state['projects'] = deepcopy(packet['current_state']['projects'])
            con.execute('UPDATE campaign SET state=?', (json.dumps(state),))
        rid = self.c.resume()['session']['pending_action']['request_id']
        self.assertEqual(self.c.recover_memory(rid, 'project:far_forge')['recovered_sources'], 1)
        self.assertEqual(self.c.recover_memory(rid, 'project:missing')['search_status'], 'blocked')


class PromptSelectionTests(Fixture):
    def prompt_packet(self, action='Hello'):
        prepared = self.c.prepare_turn(action, narrative_memory=True)
        prompt = NarrativeLoop(self.c, None).build_prompt(prepared)
        return prepared, prompt, sent(prompt)

    def test_short_sheet_keeps_exact_history_state_and_proofs(self):
        prepared, prompt, packet = self.prompt_packet()
        self.assertEqual(packet['recent_exchanges'], prepared['resume']['recent_exchanges'])
        for key in ('resources', 'effects', 'positions', 'knowledge', 'commitments'):
            self.assertEqual(packet['current_state'][key], prepared['state'][key], key)
        self.assertEqual(packet['situation_sheet']['place'], prepared['state']['positions']['player'])
        self.assertEqual(set(packet['allowed_references']), packet_references(packet))
        self.assertEqual(prompt.count('The guard waits at the door.'), 1)

    def test_social_turn_omits_trade_and_fight_instructions(self):
        _, prompt, packet = self.prompt_packet()
        self.assertNotIn('combat', packet['rule_modules'])
        self.assertNotIn('possessions', packet['rule_modules'])
        self.assertNotIn('CURRENCY RATES', prompt)
        self.assertIn('Respect the player', prompt)

    def test_hidden_ambush_and_active_combat_include_rules(self):
        _, _, packet = self.prompt_packet()
        packet['world_agenda']['events'] = [{'entry_id': 'secret', 'title': 'An ambush approaches',
                                             'interrupt': True, 'audience': ['narrator']}]
        self.assertIn('combat', select_modules(packet))
        self.assertIn('agenda', select_modules(packet))
        packet['world_agenda']['events'] = []
        packet['current_state']['resources']['player_combat_active'] = 1
        self.assertIn('combat', select_modules(packet))

    def test_ambiguous_action_fails_open_to_all_modules(self):
        _, _, packet = self.prompt_packet('I go on.')
        self.assertTrue({'possessions', 'abilities', 'combat', 'time'} <= set(packet['rule_modules']))

    def test_trade_work_and_travel_use_their_modules(self):
        _, _, packet = self.prompt_packet('Hello')
        for action, expected in [('I pay for the building wood.', 'possessions'),
                                 ('I cast a spell.', 'abilities'), ('I travel until the evening.', 'agenda')]:
            packet['player_input'] = action
            self.assertIn(expected, select_modules(packet))

    def test_all_private_preparation_and_unclassified_document_additions_remain(self):
        documents = [{'source': 'private.md', 'text': 'Exact secret and nuanced motivation.'},
                     {'source': 'docs/engine/agents/narrator.md',
                      'text': '## New rule\n\nImportant rule added by the player.'}]
        self.assertEqual(select_documents(documents, ['memory'], {'campaign_rules': {}}), documents)

    def test_edited_known_prefix_and_new_rule_in_filtered_section_are_preserved(self):
        documents = [{'source': 'docs/engine/agents/narrator.md',
                      'text': 'The world also takes initiatives. Particular new rule.'},
                     {'source': 'docs/engine/POSSESSIONS.md',
                      'text': '## Projects\n\nImportant new constraint added in parallel.'}]
        selected = select_documents(documents, ['memory'], {'campaign_rules': {}})
        self.assertEqual(selected, documents)

    def test_no_arbitrary_shortening_or_db_configuration_change(self):
        before = self.c.state()
        config = self.c.config()
        prepared, _, _ = self.prompt_packet('Hello')
        loop = NarrativeLoop(self.c, None)
        full = loop.build_prompt(prepared, compact=False)
        short = loop.build_prompt(prepared)
        self.assertLess(len(short.encode()), len(full.encode()))
        self.assertEqual(self.c.state(), before)
        self.assertEqual(self.c.config(), config)
        self.assertEqual(sum(v['utf8_bytes'] for v in loop.last_metrics['context_blocks'].values()), len(short.encode()))


class CalculatedConsequencesTests(reliability.MechanicFixture):
    def claim(self, kind, path, source='demo:initial_configuration'):
        return {'kind': kind, 'path': path, 'value_json': FINAL_STATE, 'source_reference': source}

    def test_cost_and_regeneration_values_are_filled_after_calculation(self):
        result, original = self.apply_ops([self.operation()], instant(self.c.state()) + 3,
                                          claims=[self.claim('damage', '/resources/player_hp')])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(json.loads(result['applied_events'][0]['public_consequences'][0]['value_json']), 4950)
        self.assertEqual(original['events'][0]['public_consequences'][0]['value_json'], FINAL_STATE)
        self.assertEqual(Campaign(self.path).apply(original), result)
        self.assertEqual(self.c.state()['resources']['player_hp'], 4950)

    def test_health_damage_is_computed_without_a_claimed_number(self):
        result, _ = self.apply_ops([self.operation()], claims=[self.claim('damage', '/resources/player_hp')])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(result['applied_events'][0]['public_consequences'][0]['value_json'], '4850')

    def test_auto_value_does_not_bypass_private_or_missing_operations(self):
        before = self.c.state()
        for kind, path in [('healing', '/resources/player_hp'), ('arrival', '/positions/faction'),
                           ('knowledge', '/knowledge/player'), ('payment', '/resources/player_hp')]:
            with self.subTest(kind=kind):
                result, _ = self.apply_ops([], claims=[self.claim(kind, path)])
                self.assertEqual(result['status'], 'rejected', result)
                self.assertEqual(result['narration'], '')
                self.assertEqual(self.c.state(), before)

    def test_payment_and_acquisition_fill_the_exact_final_object(self):
        with self.c.transaction() as con:
            state = self.c._state(con)
            state['resources']['player_silver'] = 22
            config = json.loads(con.execute('SELECT config FROM campaign').fetchone()[0])
            config['possession_rules'] = {'version': 1, 'currencies': {
                'player_silver': {'owner_id': 'player', 'label': 'Silver coins'}}}
            config['resource_owners']['player_silver'] = 'player'
            con.execute('UPDATE campaign SET state=?,config=?', (json.dumps(state), json.dumps(config)))
        state = self.c.state()
        e = event(state, 'Fictional purchase.', [])
        e['asset_operations'] = [reliability.op('purchase', -3, outputs={'nails': reliability.item(2)})]
        e['public_consequences'] = [self.claim('payment', '/resources/player_silver'),
                                    self.claim('acquisition', '/inventory/nails')]
        p = proposal(state, 'You have [[state:/resources/player_silver]] coins.', [e])
        result = self.c.apply(p)
        self.assertEqual(result['status'], 'recorded', result)
        claims = result['applied_events'][0]['public_consequences']
        self.assertEqual(json.loads(claims[0]['value_json']), 19)
        self.assertEqual(json.loads(claims[1]['value_json']), self.c.state()['inventory']['nails'])
        self.assertEqual(result['narration'], 'You have 19 coins.')

    def test_received_auto_claim_resumes_after_conversion_crash(self):
        now = instant(self.c.state())
        self.n.response = {'narrative_schema_version': '1.3', 'narration': 'The bolt strikes.',
            'blocking_questions': [], 'memory_request': None, 'memory_updates': [], 'agenda_updates': [],
            'continuation': None, 'roll_checks': [],
            'events': [{'world_minute': now // 60, 'world_second': now % 60,
                'description': 'Fictional cast.', 'causes': ['current_action'], 'conditions': [],
                'changes': [], 'audience': ['narrator', 'player'], 'asset_operations': [],
                'mechanical_operations': [{**self.operation(), 'source_reference': 'current_action'}],
                'public_consequences': [self.claim('damage', '/resources/player_hp', 'current_event:0')]}]}
        loop = self.loop()
        with patch.object(loop, '_proposal', side_effect=OSError('Local crash')):
            with self.assertRaises(OSError):
                loop.run_turn('I cast a bolt.')
        result = self.loop().run_turn(resume=True)
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.c.state()['resources']['player_hp'], 4850)
        self.assertEqual(self.n.calls, 1)


if __name__ == '__main__':
    unittest.main()
