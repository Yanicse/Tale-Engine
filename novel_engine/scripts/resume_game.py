"""Entry point for a new chat, with no generation and no fictional progress."""
import argparse
import json
from pathlib import Path
import sys

PKG_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG_PARENT))
from novel_engine.resume import resume_active,select_campaign


def main():
    parser=argparse.ArgumentParser(description='Find the active game without reinitializing it.')
    parser.add_argument('--enable',help='Select another campaign: its campaign.sqlite in campaigns/')
    parser.add_argument('--demo',action='store_true',help='Explicitly allow a fictional campaign')
    args=parser.parse_args()
    if args.enable:
        try:
            select_campaign(args.enable,allow_demo=args.demo)
        except ValueError as exc:
            parser.error(str(exc))
    print(json.dumps(resume_active(),ensure_ascii=False,indent=2))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
