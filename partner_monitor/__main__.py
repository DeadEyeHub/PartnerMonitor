import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .collection import collect
from .inspection import open_database,resolve_run,summary,company,report,compare
from .sources import load_sources


def main():
    load_dotenv(Path('.env'), override=False)
    parser = argparse.ArgumentParser(description='Partner Monitor: official data collection and inspection')
    commands = parser.add_subparsers(dest='command',required=True)
    collect_parser = commands.add_parser('collect')
    collect_parser.add_argument('--input',type=Path,required=True)
    collect_parser.add_argument('--sources',help='Comma-separated source IDs; default: all')
    collect_parser.add_argument('--replay',help='Replay a full run without source downloads')
    collect_parser.add_argument('--refresh-debt',action='store_true',help='Query VID live while replaying other sources')
    collect_parser.add_argument('--ownership-depth',type=int,choices=range(0,6),default=2)
    collect_parser.add_argument('--tax-debt-file',type=Path,default=os.getenv('TAX_DEBT_FILE') or None)
    collect_parser.add_argument('--snapshot',type=Path,help='Compatibility: replay the old register-only snapshot')
    commands.add_parser('sources')
    for command in ['status','company','report','compare']:
        p = commands.add_parser(command)
        p.add_argument('--run')
        if command=='company':
            p.add_argument('registration_number')
        if command=='report':
            p.add_argument('--output',type=Path,default=Path(os.getenv('REPORT_DIR','data/reports'))/'latest.html')
        if command=='compare':
            p.add_argument('--previous',required=True)
    argv = sys.argv[1:]
    if '--input' in argv and (not argv or argv[0].startswith('-')):
        argv.insert(0,'collect')
    args = parser.parse_args(argv)
    data_dir = Path(os.getenv('DATA_DIR','data'))
    try:
        if args.command=='sources':
            result = [{'id':s['id'],'format':s['format']} for s in load_sources()] + [{'id':'vid_debt','format':'browser HTML/PDF or manual CSV evidence'}]
        elif args.command=='collect':
            if args.snapshot:
                from .pipeline import run
                result = run(args.input,data_dir,os.getenv('UR_REGISTER_URL',''),args.snapshot)
            else:
                result = collect(args.input,data_dir,args.sources.split(',') if args.sources else None,
                                 args.replay,args.ownership_depth,args.tax_debt_file,args.refresh_debt)
        else:
            db = open_database(data_dir)
            try:
                run_id = resolve_run(db,args.run)
                if args.command=='status': result = summary(db,run_id)
                elif args.command=='company': result = company(db,run_id,args.registration_number)
                elif args.command=='report': result = report(db,run_id,args.output)
                else: result = compare(db,run_id,resolve_run(db,args.previous))
            finally:
                db.close()
    except ValueError as exc:
        parser.exit(1, f'Validation failed: {exc}\n')
    except Exception as exc:
        parser.exit(1, f'Collection failed ({type(exc).__name__}). Check input access and source availability.\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command=='collect' and result.get('status') in {'PARTIAL','FAILED'}:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
