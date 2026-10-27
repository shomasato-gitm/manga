"""urllib/http.clientのHTTP組立だけを通す。DNS/TCP/TLS接続は一切しない。"""
import http.client
import io
import json
import socket
import urllib.request
from urllib.parse import urlsplit,parse_qs

import pytest


class Wire:
    def __init__(self):
        self.connections=[]
        self.status=200
        self.response={}

    def connect(self, connection):
        # HTTPSConnection.connect全体を置換するためTLSもプロキシCONNECTも実行しない。
        stream=MemorySocket(self.status,self.response)
        connection.sock=stream
        self.connections.append(dict(socket=stream,timeout=connection.timeout,
                                     host=connection.host,tunnel=connection._tunnel_host))

    def request(self,index=-1):
        # フェイクキーを含む生バイトはメモリ内の検証専用。repr/ファイルへ渡さない。
        raw=b''.join(self.connections[index]['socket'].chunks)
        head,body=raw.split(b'\r\n\r\n',1)
        lines=head.split(b'\r\n');method,target,version=lines[0].decode('ascii').split(' ')
        headers={}
        for line in lines[1:]:
            name,value=line.split(b':',1)
            headers.setdefault(name.decode('ascii').lower(),[]).append(value.strip())
        return method,target,version,headers,json.loads(body)

    def safe_summary(self,index=-1):
        # URL/本文/ヘッダー値を返さない。未知の情報もそのまま出力しない。
        method,target,version,headers,body=self.request(index)
        parsed=urlsplit(target);config=body.get('generationConfig',{})
        return dict(post=method=='POST',http11=version=='HTTP/1.1',
            path_matches=parsed.path=='/v1beta/models/gemini-3.5-flash-lite:generateContent',
            auth_header_count=len(headers.get('x-goog-api-key',[])),
            query_key_present='key' in parse_qs(parsed.query),content_type_json=headers.get('content-type')==[b'application/json'],
            role_user=body['contents'][0].get('role')=='user',
            candidate_count_1_explicit=config.get('candidateCount')==1,
            max_output_256_explicit=config.get('maxOutputTokens')==256,max_output_64_explicit=config.get('maxOutputTokens')==64,
            thinking_minimal_explicit=config.get('thinkingConfig')=={'thinkingLevel':'minimal'},
            structured_output_present=any(k in config for k in ('responseFormat','responseMimeType','responseJsonSchema','responseSchema')),
            timeout_30=self.connections[index]['timeout']==30)


class MemorySocket:
    def __init__(self,status,response):
        self.chunks=[]
        body=json.dumps(response,ensure_ascii=False).encode('utf-8')
        self.reply=(f'HTTP/1.1 {status} Fake\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode('ascii')+body

    def sendall(self,data):self.chunks.append(bytes(data))
    def makefile(self,*args,**kwargs):return io.BytesIO(self.reply)
    def close(self):pass


@pytest.fixture
def wire(monkeypatch):
    result=Wire()
    def forbidden(*args,**kwargs):raise AssertionError('Offline test: DNS/network is prohibited')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket.socket,'connect_ex',forbidden)
    monkeypatch.setattr(http.client.HTTPSConnection,'connect',lambda connection:result.connect(connection))
    # このテストプロセス内だけのfake。実アプリのプロキシ設定は変更しない。
    monkeypatch.setattr(urllib.request,'getproxies',lambda:{})
    return result
