"""Show how the context of one attempt was selected; opens the campaign read-only."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from novel_engine.context_diagnostic import read


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path, help='Path of the existing SQLite database.')
    parser.add_argument('--request', help='Attempt identifier; latest attempt by default.')
    args = parser.parse_args(argv)
    if not args.campaign.is_file(): parser.error('Existing database required.')
    print(json.dumps(read(args.campaign, args.request), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
