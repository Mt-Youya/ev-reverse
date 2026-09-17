"""Reproduce the local player's catalog API using its authorized session."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent / 'parser-tools/captured/runtime'))
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


class CatalogApi:
    def __init__(self, session):
        self.token = session['token']
        self.key = session['data_key'].encode()
        self.secret = session['sign_secret']

    def body(self, endpoint, fields, timestamp=None):
        fields = dict(fields, app_name='EVPlayer2', app_version='5.0.5', need_zip=1,
                      os_name='windows', platform=1, platform_type=1,
                      req_time=int(time.time()) if timestamp is None else timestamp,
                      net_url='https://en2.ieway.cn' + endpoint)
        canonical = '&'.join(f'{k}={fields[k]}' for k in sorted(fields))
        fields['sign'] = hashlib.md5((canonical + '&&' + self.secret).encode()).hexdigest()
        plain = json.dumps(fields, ensure_ascii=False).encode()
        cipher = AES.new(self.key, AES.MODE_ECB).encrypt(pad(plain, 16))
        return json.dumps({'params': base64.b64encode(cipher).decode(), 'version': 200}).encode()

    def open(self, envelope):
        if envelope.get('errcode') != 0:
            raise RuntimeError(f"API errcode={envelope.get('errcode')}: {envelope.get('errmsg')}")
        value = envelope['result']
        if envelope.get('encrypt'):
            plain = AES.new(self.key, AES.MODE_ECB).decrypt(base64.b64decode(value))
            plain = unpad(plain, 16)
        elif isinstance(value, str):
            plain = value.encode()
        else:
            return value
        if envelope.get('zip'):
            plain = zlib.decompress(plain, 16 + zlib.MAX_WBITS)
        return json.loads(plain)

    def request(self, endpoint, fields):
        token = self.token if self.token.startswith('Bearer ') else 'Bearer ' + self.token
        request = urllib.request.Request('https://en2.ieway.cn' + endpoint,
            data=self.body(endpoint, fields), headers={'Authorization': token,
            'Content-Type': 'application/json', 'User-Agent': 'EVPlayer2/5.0.5'})
        with urllib.request.urlopen(request, timeout=45) as response:
            return self.open(json.load(response))

    def roots(self, account):
        return self.request('/student/getEvsAuthorityCourse',
                            {'account_id': account, 'api_version': '20250103'})

    def course(self, account, course):
        return self.request('/student/getEVSCourseDetail',
                            {'account_id': account, 'course_id': course})

    def download_url(self, video):
        return self.request('/student/getEvsSignUrl', {
            'file_id': video['file_id'], 'enc_ver': video['enc_ver'],
            'fsave': video['fsave'], 'online': -1, 'api_version': 20260528})


def videos(node):
    for file in node.get('files', []):
        if file.get('type') == 'video':
            yield file
    for child in node.get('childs', []):
        yield from videos(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--account', type=int, required=True)
    parser.add_argument('--course', type=int)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    api = CatalogApi(json.loads(args.session.read_text(encoding='utf-8')))
    value = api.roots(args.account) if args.course is None else api.course(args.account, args.course)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'saved': str(args.output), 'fields': list(value)}, ensure_ascii=True))


if __name__ == '__main__':
    main()
