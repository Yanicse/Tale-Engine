"""Routine and chronicle tool, answered by hand or by a chat assistant."""
import argparse
import json
from pathlib import Path
import sys
import uuid

PKG_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG_PARENT))
from novel_engine.contracts import ROOT
from novel_engine.campaign import Campaign
from novel_engine.resume import active_campaign
from novel_engine.routine import Routine, preview, read_json, DEFAULT_DELEGATION, DEFAULT_POLICY, LEGACY_POLICY


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', help='Existing save; active campaign by default')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('preview', 'open'):
        p = sub.add_parser(name)
        p.add_argument('--days', type=int, default=7)
        if name == 'open':
            p.add_argument('--folder')
            p.add_argument('--objective', default='')
            p.add_argument('--topic', action='append')
            p.add_argument('--delegation', default=DEFAULT_DELEGATION)
            p.add_argument('--points-per-step', type=int, help='Daily points per step (1: one step per day); default: next deadline')
            p.add_argument('--period-days', type=int, default=DEFAULT_POLICY['period_days'], help='Length of a review period; 0: no period')
            p.add_argument('--pause-period', action='store_true', help='Stop at the end of each period (resume with prepare)')
            p.add_argument('--stop-on', action='append', choices=['alert', 'critical'], help='Signal levels that stop the routine (critical always)')
            p.add_argument('--alert-days', type=int, default=DEFAULT_POLICY['alert_days'], help='Days of daily steps after an alert')
            p.add_argument('--arcs-review-days', type=int, default=DEFAULT_POLICY['arcs_review_days'], help='Maximum delay between arc reviews, 2 to 30 days')
            p.add_argument('--legacy-mode', action='store_true', help='Old behavior: one step per 6 pm point, no bulletin or signals')
    for name in ('prepare', 'apply', 'correct', 'status', 'report'):
        p = sub.add_parser(name)
        p.add_argument('--folder', required=True)
        if name == 'apply':
            p.add_argument('--file', required=True, help='UTF-8 JSON answer, or - for stdin')
            p.add_argument('--next', action='store_true', help='Also prepare the next packet if the routine continues')
    args = parser.parse_args(argv)
    try:
        if args.command in ('preview', 'open'):
            campaign = Campaign(args.campaign) if args.campaign else active_campaign()[1]
            if campaign is None:
                raise ValueError('No active campaign.')
            if args.command == 'preview':
                output = preview(campaign, args.days)
            else:
                folder = args.folder or ROOT / '.routines' / uuid.uuid4().hex
                policy = dict(LEGACY_POLICY) if args.legacy_mode else {
                    'points_per_step': args.points_per_step, 'period_days': args.period_days or None,
                    'pause_period': args.pause_period, 'stop_on': args.stop_on or ['critical'],
                    'alert_days': args.alert_days, 'arcs_review_days': args.arcs_review_days}
                output = Routine.create(folder, campaign, args.days, args.objective,
                                        args.topic, args.delegation, policy).status()
        else:
            if args.campaign:
                parser.error('--campaign is reserved to preview and open; the folder fixes the save afterwards.')
            routine = Routine(args.folder)
            if args.command == 'prepare':
                output = routine.prepare()
            elif args.command == 'apply':
                response = json.loads(sys.stdin.read()) if args.file == '-' else read_json(args.file)
                output = routine.apply(response)
                if args.next and output['status'] in ('recorded', 'recorded_world'):
                    # Never hide a successful save behind a failure of the next preparation.
                    try:
                        following = routine.prepare(resume=False)
                    except (ValueError, OSError, RuntimeError) as exc:
                        following = {'status': 'to_examine', 'reason': str(exc)}
                    output = {'result': output, 'next': following}
            elif args.command == 'report':
                output = routine.report()
            elif args.command == 'correct':
                output = routine.correct()
            else:
                output = routine.status()
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({'status': 'error', 'reason': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
