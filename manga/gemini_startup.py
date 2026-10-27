"""任意の調査用起動。StreamlitによるSecrets読込前に比較基準を置く。"""
import json
from pathlib import Path
import sys

from manga.gemini_credentials import capture_startup,credential_report


def main():
    capture_startup('before_streamlit')
    print('Gemini認証の通信なし確認（値・一部分・ハッシュは出力しません）')
    print(json.dumps(credential_report(),ensure_ascii=False,indent=2))
    # 比較用メモリを保持する同じプロセスで、従来のStreamlit CLIを実行する。
    from streamlit.web import cli
    sys.argv=['streamlit','run',str(Path(__file__).resolve().parents[1]/'app.py'),*sys.argv[1:]]
    cli.main()


if __name__=='__main__':main()
