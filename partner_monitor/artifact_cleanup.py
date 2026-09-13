"""Opt-in cleanup of reproducible temporary report artifacts only."""
import argparse
import json
import time
from pathlib import Path

def candidates(reports, days=7, now=None):
    if days < 1: raise ValueError('Retention must be at least one day')
    root=Path(reports).resolve();cutoff=(time.time() if now is None else now)-days*86400
    paths=list(root.glob('*.tmp.xlsx'))+list(root.glob('*.tmp.xlsx.inspect.ndjson'))
    paths+=list((root/'workbook-preview').glob('*.png'))
    return sorted({p for p in paths if p.is_file() and not p.is_symlink()
        and p.resolve().is_relative_to(root) and p.stat().st_mtime<cutoff})

def cleanup(reports, days=7, apply=False):
    selected=candidates(reports,days);removed=[]
    for path in selected:
        if apply:
            # Revalidate the concrete target immediately before unlinking. No recursive deletion.
            if path not in candidates(reports,days): continue
            path.unlink();removed.append(str(path))
    return {'mode':'apply' if apply else 'preview','candidates':[str(p) for p in selected],'removed':removed}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports',type=Path,default=Path('data/reports'))
    parser.add_argument('--days',type=int,default=7)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();print(json.dumps(cleanup(args.reports,args.days,args.apply),indent=2))
if __name__=='__main__':main()
