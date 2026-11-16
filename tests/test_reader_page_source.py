"""The source of the reading page: English names, and every class the script uses has a rule."""
from pathlib import Path
import re
import unittest

PAGE = (Path(__file__).resolve().parents[1] / 'novel_engine' / 'reader_page.html').read_text(encoding='utf-8')


class PageSourceTests(unittest.TestCase):
    def test_every_badge_variant_used_by_the_script_has_a_style_rule(self):
        variants = set(re.findall(r"'badge ([a-z]+)'", PAGE))
        variants |= set(re.findall(r"cardBadge\([^\n]*?,'(gold|blood|[a-z]{2})',\[", PAGE))
        self.assertTrue(variants)
        for variant in variants:
            self.assertTrue(('.badge.' + variant + '{') in PAGE, variant)

    def test_the_place_icons_use_tower_for_a_tower(self):
        row = re.search(r"var ICONS_PLACES=.*", PAGE).group(0)
        self.assertTrue("'tower|watch" in row)
        self.assertFalse('turn|' in row)

    def test_the_particle_blend_key_is_english(self):
        self.assertFalse('clair' in PAGE)
        self.assertTrue('additive:true' in PAGE)

    def test_code_names_are_english(self):
        for snippet in ('searchNpc', 'reader-cap-filter', 'filterNode', 'class="rank"', '.rank{', "'piece'", '.piece',
                        'dust2', 'markShape', "footer.setBusy", "toggle('setBusy'"):
            self.assertFalse(snippet in PAGE, snippet)
        self.assertFalse('request=' in PAGE.replace('query=', ''))

    def test_comments_say_what_they_mean(self):
        for phrase in ('follows its vault', 'does not designate', 'chosen from words of', 'Ideas from research',
                       'panel relief'):
            self.assertFalse(phrase in PAGE, phrase)

    def test_the_ability_filter_has_no_dead_condition(self):
        self.assertFalse("(p==='other'&&" in PAGE)

    def test_the_hour_is_never_taken_from_elapsed_time(self):
        clock = re.search(r"function clockTime\(\)\{.*", PAGE).group(0)
        self.assertTrue('minute_of_day' in clock)
        self.assertFalse('world_minute' in clock)
        update = re.search(r"function updateClock\(\)\{.*?[\r\n]\}", PAGE, re.S).group(0)
        self.assertTrue('h===null' in update)
        self.assertTrue("style.display='none'" in update)

    def test_the_vignette_uses_a_real_css_shape_keyword(self):
        self.assertFalse('ellipsis at' in PAGE)
        self.assertEqual(PAGE.count('radial-gradient(ellipse at'), 2)

    def test_the_settings_panel_fits_a_narrow_viewport(self):
        narrow = re.search(r"@media \(max-width:480px\)\{(.*?)\n\}", PAGE, re.S).group(1)
        self.assertTrue('.settings{position:static}' in narrow)
        self.assertTrue('.settings .sidePanel{left:12px;right:12px;min-width:0}' in narrow)
        self.assertTrue('white-space:normal' in narrow)

    def test_a_failed_first_load_is_retried_and_every_tab_is_refreshed(self):
        cycle = re.search(r"function cycle\(\)\{.*?[\r\n]\}", PAGE, re.S).group(0)
        self.assertFalse('firstTime' in PAGE)
        self.assertTrue('if(!ready){ready=true;showTabs();loadTab();return}' in cycle)
        self.assertTrue('loadConfig()' in cycle)
        self.assertTrue('initialStory()' in cycle)
        self.assertTrue('refreshTab()' in cycle)
        self.assertFalse("current==='npc'" in cycle)
        refresh = re.search(r"function refreshTab\(\)\{.*?[\r\n]\}", PAGE, re.S).group(0)
        self.assertTrue('fieldBusy()' in refresh)
        self.assertTrue('window.scrollTo(0,y)' in refresh)

    def test_search_results_replace_the_list_and_links_open_an_explicit_slice(self):
        search = re.search(r"function searchStory\(\)\{.*?[\r\n]\}", PAGE, re.S).group(0)
        self.assertTrue("d.removeAttribute('id')" in search)
        self.assertTrue("'listEl'" in search)
        self.assertTrue('openExchange(e.seq)' in search)
        self.assertTrue('ev.preventDefault();openExchange(e.seq)' in PAGE.split('function npc()')[1])
        opener = re.search(r"function openExchange\(seq\)\{.*?[\r\n]\}", PAGE, re.S).group(0)
        self.assertTrue("location.hash='exchange-'+seq" in opener)
        self.assertTrue('loadTab(seq)' in opener)
        self.assertTrue('function initialStory(seq)' in PAGE)

    def test_an_older_slice_never_receives_the_latest_exchanges(self):
        new_turns = re.search(r"function newTurns\(\)\{.*?[\r\n]\}", PAGE, re.S).group(0)
        self.assertTrue('!tail' in new_turns)
        self.assertTrue('tail=!target' in PAGE)
        self.assertTrue('if(tail){scrollList();return}' in PAGE)


if __name__ == '__main__':
    unittest.main()
