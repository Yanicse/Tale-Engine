"""`novel` command: check the setup, create a novel pack, build its canon, start a campaign and play."""
import argparse
import json
import re
import runpy
import shutil
import sqlite3
import sys
from pathlib import Path

from .contracts import PACKAGE, ROOT
from .pack import PRESETS

NOVELS = ROOT / 'novels'
NOVEL_JSON = {'title': '', 'language': 'en', 'tracked_npcs': [], 'currency_rates': {}, 'world_rules': []}
THEME_JSON = {'preset': 'auto', 'title': '', 'subtitle': '',
              'input_hint': '', 'clock': True, 'colors': {}, 'labels': {}, 'icons': {}}
SCRIPTS = {'play': 'play_narrative.py', 'resume': 'resume_game.py', 'read': 'read_game.py',
           'routine': 'routine.py', 'diagnose': 'diagnose_context.py'}
SCRIPT_HELP = {'play': 'play turns', 'resume': 'show the active campaign and its pending action',
               'read': 'serve the reading page', 'routine': 'play several fictional days, step by step',
               'diagnose': 'show how the context of one attempt was selected'}


def pack(slug):
    path = NOVELS / slug
    if not path.is_dir():
        raise SystemExit(f'No novel pack "{slug}": run `novel init {slug}` first.')
    return path


def write_json(path, value, overwrite=False):
    if path.exists() and not overwrite:
        return False
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return True


def doctor(_):
    from .llm import ENGINES
    from .narration_claude import find_claude
    from .narration_codex import find_codex
    import os
    checks = [('Python >= 3.10', sys.version_info >= (3, 10), sys.version.split()[0])]
    try:
        con = sqlite3.connect(':memory:')
        con.execute('CREATE VIRTUAL TABLE t USING fts5(x)')
        fts = True
    except sqlite3.Error:
        fts = False
    checks.append(('SQLite FTS5', fts, sqlite3.sqlite_version))
    try:
        import pypdf  # noqa: F401
        pdf = True
    except ImportError:
        pdf = False
    checks += [('Claude CLI (optional)', bool(find_claude()), find_claude() or 'not found'),
               ('Codex CLI (optional)', bool(find_codex()), find_codex() or 'not found'),
               ('ANTHROPIC_API_KEY (optional)', bool(os.environ.get('ANTHROPIC_API_KEY')), 'set' if os.environ.get('ANTHROPIC_API_KEY') else 'not set'),
               ('pypdf for PDF (optional)', pdf, 'installed' if pdf else 'pip install "tale-engine[pdf]"')]
    required = {'Python >= 3.10', 'SQLite FTS5'}
    for name, ok, detail in checks:
        print(f"[{'ok' if ok else ('FAIL' if name in required else '--')}] {name}: {detail}")
    print('Engines: ' + ', '.join(ENGINES) + ' (manual needs no account)')
    return 0 if all(ok for name, ok, _ in checks if name in required) else 1


def init(args):
    path = NOVELS / args.slug
    path.mkdir(parents=True, exist_ok=True)
    for folder in ('sources', 'text', 'work'):
        (path / folder).mkdir(exist_ok=True)
    write_json(path / 'novel.json', {**NOVEL_JSON, 'title': args.title or args.slug, 'language': args.language})
    write_json(path / 'theme.json', {**THEME_JSON, 'preset': args.preset})
    print(f'Novel pack created: {path}\nNext: novel ingest <book> --novel {args.slug}')


def ingest_cmd(args):
    from .ingest import ingest
    volume, pages = ingest(pack(args.novel), args.file, args.volume)
    print(f'Volume {volume}: {pages} pages extracted into novels/{args.novel}/text/')


def canon_search(path, words):
    from .canon_build import REVISION
    from .memory import CanonRepository
    if not (path / 'canon.sqlite').is_file():
        raise SystemExit('No canon yet: run `novel canon build` first.')
    query = ' '.join(words)
    if not query.strip():
        raise SystemExit('Give the words to look for: novel canon search <novel> <words>.')
    rows = CanonRepository(path / 'canon.sqlite', int(REVISION)).search(query, limit=10)
    for row in rows:
        print(f"[{row['claim_id']}] {row['statement']}")
        for source in row['sources']:
            print(f"    volume {source['volume']} page {source['pdf_page']}: \"{source['quote']}\"")
    if not rows:
        print('No sourced claim found for this search.')
    return 0


def canon(args):
    from . import canon_build
    path = pack(args.novel)
    if args.action == 'search':
        return canon_search(path, args.words)
    batches = canon_build.pending_batches(path, args.budget)
    figures = canon_build.estimate(batches)
    print(f"{figures['batches']} batches, about {figures['input_tokens']:,} input tokens and "
          f"{figures['output_tokens']:,} output tokens with the engine '{args.engine}'.")
    if args.action == 'plan' or (args.engine != 'manual' and not args.yes):
        if args.action == 'build':
            print('Nothing was sent. Add --yes to run; an interrupted run resumes where it stopped.')
        return 0
    result = canon_build.build(path, args.engine, args.model, args.budget, args.limit)
    print(json.dumps(result, indent=2))
    if result['awaiting_manual_answer']:
        print("Manual engine: send each work/<batch>.prompt.md to a model, save its JSON answer as "
              "work/<batch>.response.json, then run this command again.")
    return 1 if result['failed'] else 0


def parse_ability(spec):
    """'tide_sense:Tide sense:0' -> (id, label, full power cost in percent of maximum mana or None)."""
    parts = [p.strip() for p in spec.split(':')]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise SystemExit(f'Ability "{spec}": use id:Label or id:Label:cost (cost = percent of maximum mana at 100% power).')
    if len(parts) > 3 or (len(parts) == 3 and not re.fullmatch(r'\d+(\.\d+)?', parts[2])):
        raise SystemExit(f'Ability "{spec}": the cost must be a number from 0 to 100.')
    cost = float(parts[2]) if len(parts) == 3 else None
    if cost is not None and cost > 100:
        raise SystemExit(f'Ability "{spec}": the cost must be a number from 0 to 100.')
    ident = re.sub(r'[^a-z0-9]+', '_', parts[0].casefold()).strip('_')
    return ident, parts[1], (int(cost) if cost is not None and cost == int(cost) else cost)


def ability_rules(specs, ask_abilities=False):
    """The `rules` block of a character sheet from `id:Label[:cost]` specs, asked one by one on a terminal."""
    specs = list(specs or [])
    while ask_abilities:
        try:
            line = input('Ability as id:Label[:cost % of max mana at full power], empty to finish []: ').strip()
        except EOFError:
            break
        if not line:
            break
        specs.append(line)
    if not specs:
        return {}
    abilities, costs = {}, {}
    for spec in specs:
        ident, label, cost = parse_ability(spec)
        abilities[ident] = {'label': label, 'phase': 'always', **({'free': True} if cost in (None, 0) else {})}
        if cost is not None:
            costs[ident] = cost
    rules = {'abilities': abilities}
    if costs:
        rules['costs'] = {'default_output_percent': 100, 'full_power_cost_percent': costs}
    return rules


def character_new(args):
    path = pack(args.novel)
    if (path / 'character.json').exists() and not args.force:
        raise SystemExit('character.json exists; use --force to replace it.')

    def ask(value, question, default):
        if value is not None:
            return value
        if not sys.stdin.isatty():
            return default
        try:
            return input(f'{question} [{default}]: ').strip() or default
        except EOFError:
            return default

    name = ask(args.name, 'Character name', 'Hero')
    hp = ask(args.hp, 'Maximum HP', '100')
    if not hp.isdigit() or int(hp) < 1:
        raise SystemExit('Maximum HP must be a positive integer.')
    mana = getattr(args, 'mana', None) or '100'
    if not mana.isdigit() or int(mana) < 0:
        raise SystemExit('Maximum mana must be a non-negative integer.')
    sheet = {'name': name, 'hp_max': int(hp), 'mana_max': int(mana),
             'description': ask(args.description, 'Short description', ''),
             'rules': ability_rules(args.ability, ask_abilities=sys.stdin.isatty() and not args.ability)}
    if not write_json(path / 'character.json', sheet, args.force):
        raise SystemExit('character.json exists; use --force to replace it.')
    print(f"Character sheet written: {path / 'character.json'}")


def report_pack(path, quiet_ok=False):
    """Print the problems of a pack; True when there is no error."""
    from .pack_check import check_pack
    report = check_pack(path)
    for line in report.errors:
        print('ERROR   ' + line)
    for line in report.warnings:
        print('warning ' + line)
    if report.ok and not quiet_ok:
        print(f"{Path(path).name}: no error, {len(report.warnings)} warning(s).")
    return report.ok


def validate(args):
    names = [args.novel] if args.novel else sorted(p.name for p in NOVELS.iterdir() if p.is_dir()) if NOVELS.is_dir() else []
    if not names:
        raise SystemExit('No novel pack to validate: run `novel init <name>` or `novel example`.')
    return 0 if all([report_pack(pack(name)) for name in names]) else 1


def campaign_new(args):
    from .campaign import Campaign
    from .demo import configuration, initial_state
    from .resume import select_campaign
    path = pack(args.novel)
    if not report_pack(path, quiet_ok=True):
        raise SystemExit('Fix the errors above first (`novel validate ' + args.novel + '` lists them again).')
    canon_file = path / 'canon.sqlite'
    if not canon_file.is_file():
        raise SystemExit('No canon yet: run `novel canon build` first.')
    from .pack import campaign_config, read, seed_powers, seed_resources
    sheet = read(path / 'character.json')
    state = initial_state(args.novel)
    opening = read(path / 'novel.json').get('opening')
    opening = opening if isinstance(opening, dict) and opening.get('place') and opening.get('scene') else None
    state['versions'] = {'canon_revision': 1, 'canon_snapshot': canon_file.relative_to(ROOT).as_posix(),
                         'adaptation_version': 'novel-1'}
    start = re.sub(r'[^a-z0-9]+', '_', opening['place'].casefold()).strip('_') if opening else args.start
    state.update(positions={'player': start}, knowledge={}, intentions={}, applied_event_ids=[],
                 resources={'player_hp': sheet.get('hp_max', 100), 'player_hp_max': sheet.get('hp_max', 100)})
    state['powers'] = seed_powers(sheet)
    config = campaign_config(path, configuration())
    state['resources'] = seed_resources(sheet, config)
    target = ROOT / 'campaigns' / args.novel / 'campaign.sqlite'
    if target.exists():
        raise SystemExit(f'Campaign already exists: {target}')
    # Persistent creations (invented NPCs, places, quests) are on unless the author turns them off.
    config['creation_format'] = None if getattr(args, 'no_creations', False) else '1'
    # The narrator receives a few sourced claims of the canon at each turn, chosen locally.
    config['canon_selection'] = {'version': 1, 'enabled': True}
    if getattr(args, 'bounded_recovery', False):
        from .bounded_resume import POLICY
        config['recovery_policy'] = POLICY
    campaign = Campaign(target).create(state, config)
    try:
        if opening:
            campaign.open_scene(opening['scene'].strip())
        from .world_setup import install_into_campaign
        world = install_into_campaign(campaign, path)
    except Exception:
        for suffix in ('', '-wal', '-shm'):
            Path(str(target) + suffix).unlink(missing_ok=True)
        (target.parent / 'chronology.json').unlink(missing_ok=True)
        raise
    select_campaign(target)
    extras = ', '.join(name for name, on in (('summaries', world['planner']), ('chronicler', world['chronicler']),
                                            ('arcs', world['arcs'])) if on)
    print(f'Campaign created and selected: {target}\nWorld: {extras}. Play with: novel play')


EXAMPLE = PACKAGE / 'assets' / 'examples' / 'lantern_bay'
WORLD_FILES = ('chronology.json', 'clocks.json', 'arcs.json')


def example(args):
    """Install the bundled example novel: pack, text, canon built from prepared answers, campaign. No account needed."""
    from . import canon_build
    from .ingest import ingest
    path = NOVELS / args.slug
    if path.exists():
        raise SystemExit(f'{path} already exists: choose another name with --slug.')
    for folder in ('sources', 'text', 'work'):
        (path / folder).mkdir(parents=True)
    for name in ('novel.json', 'theme.json', 'character.json', *(WORLD_FILES if getattr(args, 'world', False) else ())):
        shutil.copyfile(EXAMPLE / name, path / name)
    story = path / 'sources' / 'story.txt'
    shutil.copyfile(EXAMPLE / 'story.txt', story)
    ingest(path, story)
    for batch in canon_build.plan(path):
        shutil.copyfile(EXAMPLE / 'canon_answer.json', path / 'work' / (batch['id'] + '.response.json'))
    result = canon_build.build(path, 'manual', log=lambda *_: None)
    if result['failed'] or result['remaining']:
        raise SystemExit('The example canon could not be built: ' + json.dumps(result))
    print(f"Example novel installed in {path} with {result['claims']} sourced claims.")
    campaign_new(argparse.Namespace(novel=args.slug, start='start'))
    print('Next: novel read   (or novel play --engine manual)')


def target_campaign(args):
    """The campaign named by --campaign, else the active one."""
    from .campaign import Campaign
    from .resume import active_campaign
    if args.campaign:
        return Campaign(Path(args.campaign).resolve())
    try:
        campaign = active_campaign()[1]
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(f'No usable active campaign ({error}): pass --campaign <path to campaign.sqlite>.')
    if campaign is None:
        raise SystemExit('No campaign is selected: create one with `novel campaign new <novel>`.')
    return campaign


def unblock(args):
    from .campaign_tools import unblock as run
    print(json.dumps(run(target_campaign(args), args.apply), indent=2, ensure_ascii=False))
    return 0


def configure(args):
    from .campaign_tools import configure as run
    flag = {'on': True, 'off': False, None: None}
    print(json.dumps(run(target_campaign(args), args.apply, flag[args.creations], flag[args.bounded_recovery],
                         flag[args.canon_selection]), indent=2, ensure_ascii=False))
    return 0


def run_script(name, rest):
    module = 'novel_engine.scripts.' + SCRIPTS[name].removesuffix('.py')
    # argparse names the program after argv[0]; alter_sys=False keeps it, so the help shows `novel play`.
    sys.argv = ['novel ' + name] + list(rest)
    try:
        runpy.run_module(module, run_name='__main__', alter_sys=False)
    except SystemExit as stop:
        return stop.code or 0
    return 0


def passthrough(name):
    return lambda args: run_script(name, args.rest)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    # A redirected output on Windows uses the ANSI code page, which cannot print every book's characters.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    # argparse cannot forward a leading option such as `novel play --text ...`: hand over the arguments untouched.
    if argv and argv[0] in SCRIPTS:
        return run_script(argv[0], argv[1:])
    parser = argparse.ArgumentParser(prog='novel', description='Turn a novel into an interactive campaign.')
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('doctor', help='check Python, SQLite FTS5 and available engines').set_defaults(func=doctor)
    p = subs.add_parser('init', help='create a novel pack in novels/<slug>')
    p.add_argument('slug', help='folder name of the pack, used in every later command')
    p.add_argument('--title', help='title of the novel (default: the slug)')
    p.add_argument('--language', default='en', help='language of the narration and the page, such as en or fr')
    p.add_argument('--preset', choices=PRESETS, default='auto', help='look of the reading page')
    p.set_defaults(func=init)
    p = subs.add_parser('ingest', help='extract the text of a TXT, EPUB or PDF book')
    p.add_argument('file', help='the book file')
    p.add_argument('--novel', required=True, help='slug of the novel pack')
    p.add_argument('--volume', type=int, help='volume number (default: the next one)')
    p.set_defaults(func=ingest_cmd)
    p = subs.add_parser('validate', help='check a novel pack and list every problem (all packs when no name is given)')
    p.add_argument('novel', nargs='?', help='slug of the novel pack')
    p.set_defaults(func=validate)
    p = subs.add_parser('canon', help='plan or build the sourced canon, or search its claims')
    p.add_argument('action', choices=('plan', 'build', 'search'), help='plan the batches, build the canon or search it')
    p.add_argument('novel', help='slug of the novel pack')
    p.add_argument('words', nargs='*', help='words to look for (search only)')
    p.add_argument('--engine', choices=('claude', 'codex', 'api', 'manual'), default='manual',
                   help='engine that extracts the claims (default: manual)')
    p.add_argument('--model', help='model name (default: the engine default)')
    p.add_argument('--budget', type=int, default=12000, help='input tokens per batch')
    p.add_argument('--limit', type=int, help='stop after this many batches')
    p.add_argument('--yes', action='store_true', help='run after the estimate (needed for remote engines)')
    p.set_defaults(func=canon)
    p = subs.add_parser('character', help='write a character sheet, asking for what you leave out')
    p.add_argument('action', choices=('new',), help='write a new character sheet')
    p.add_argument('novel', help='slug of the novel pack')
    p.add_argument('--name', help="the character's name")
    p.add_argument('--hp', help='maximum hit points (default: 100)')
    p.add_argument('--mana', help='maximum mana (default: 100; 0 for a character without magic)')
    p.add_argument('--description', help='a short description, sent to the narrator')
    p.add_argument('--ability', action='append', metavar='ID:LABEL[:COST]',
                   help='an ability; COST is the percent of maximum mana paid at 100%% power (0 = free); repeatable')
    p.add_argument('--force', action='store_true', help='replace an existing character.json')
    p.set_defaults(func=character_new)
    p = subs.add_parser('example', help='install the bundled example novel and start its campaign (no account needed)')
    p.add_argument('--slug', default='lantern-bay', help='folder name of the example pack and campaign')
    p.add_argument('--world', action='store_true',
                   help='also install the example chronicler (two clocks) and arcs, which add a world review at the first turn')
    p.set_defaults(func=example)
    p = subs.add_parser('campaign', help='create a campaign from a novel pack')
    p.add_argument('action', choices=('new',), help='create a new campaign')
    p.add_argument('novel', help='slug of the novel pack')
    p.add_argument('--start', default='start', help='starting place identifier')
    p.add_argument('--no-creations', action='store_true',
                   help='do not keep a register of invented NPCs, places and quests')
    p.add_argument('--bounded-recovery', action='store_true',
                   help='allow one automatic correction after a technical failure (off by default)')
    p.set_defaults(func=campaign_new)
    p = subs.add_parser('unblock', help='grant one more generation to a pending action whose budget is spent')
    p.add_argument('--campaign', help='path of a campaign.sqlite (the active campaign by default)')
    p.add_argument('--apply', action='store_true', help='apply it (preview otherwise); the save is backed up first')
    p.set_defaults(func=unblock)
    p = subs.add_parser('configure', help='switch the creations format, the bounded automatic recovery or the canon selection')
    p.add_argument('--campaign', help='path of a campaign.sqlite (the active campaign by default)')
    p.add_argument('--creations', choices=('on', 'off'), help='persistent register of invented NPCs, places and quests')
    p.add_argument('--bounded-recovery', choices=('on', 'off'),
                   help='one automatic correction after a technical failure, within the action budget')
    p.add_argument('--canon-selection', choices=('on', 'off'),
                   help='give the narrator the sourced canon claims that match each turn (on for new campaigns)')
    p.add_argument('--apply', action='store_true', help='apply it (preview otherwise); the save is backed up first')
    p.set_defaults(func=configure)
    for name, summary in SCRIPT_HELP.items():
        p = subs.add_parser(name, help=summary)
        p.add_argument('rest', nargs=argparse.REMAINDER)
        p.set_defaults(func=passthrough(name))
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == '__main__':
    sys.exit(main())
