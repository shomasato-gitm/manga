#!/usr/bin/env python3
"""Read-only offline report to stdout; never connects or opens a budget ledger."""
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from manga.api_architecture_diagnostics import diagnostic


def main():
    try:
        report = diagnostic()
    except (OSError,ValueError):
        print(json.dumps(dict(error='診断資料を読み込めません。実送信を行わず資料を復元してください。',phase9a_execution_enabled=False,network_calls=0),ensure_ascii=False))
        return 2
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0  # 診断完了。接続可の意味ではない。


if __name__=='__main__':
    raise SystemExit(main())
