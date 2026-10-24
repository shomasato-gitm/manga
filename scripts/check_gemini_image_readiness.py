#!/usr/bin/env python3
"""読取専用の公式根拠診断。no-goは終了コード2。API・台帳・作品へアクセスしない。"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from manga.image_ai_readiness import diagnostic


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',help='未使用JSONファイル。既存ファイル・リンクへ上書きしません。')
    args=parser.parse_args(argv)
    try:
        report=diagnostic(); encoded=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
        if args.output:
            path=Path(args.output).absolute()
            if any(p.is_symlink() for p in (path,*path.parents)): raise ValueError
            descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream: stream.write(encoded)
        print(encoded,end='')
        return 0 if report['production_enabled'] else 2
    except (ValueError,OSError):
        parser.exit(1,'公式確認記録、保存先フォルダ、既存ファイルを確認してください。API送信・上書きはしていません。\n')


if __name__=='__main__': sys.exit(main())
