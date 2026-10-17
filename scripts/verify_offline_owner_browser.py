"""Real Chromium/IndexedDB/worker proof with synthetic loopback accounts."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATE = {'owner': '11', 'status': 200, 'owner_status': 200, 'switch': False, 'writes': []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body, status=200, kind='application/json', private=True):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        if private:
            self.send_header('Cache-Control', 'private, no-store')
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/sync/owner':
            owner = STATE['owner']
            if STATE['switch']:
                STATE['owner'] = '22'
                STATE['switch'] = False
            self.reply(json.dumps({'owner': owner}), STATE['owner_status'])
        elif path.startswith('/static/'):
            file = (ROOT / path.lstrip('/')).resolve()
            if file.is_relative_to((ROOT / 'static').resolve()) and file.is_file():
                self.reply(file.read_bytes(), kind=mimetypes.guess_type(str(file))[0] or 'application/octet-stream', private=False)
            else:
                self.reply('{}', 404)
        elif path == '/sw-test.js':
            self.reply((ROOT / 'static/sw.js').read_text(encoding='utf-8'), kind='application/javascript', private=False)
        elif path == '/worker.js':
            self.reply("importScripts('/static/js/ip-queue-core.js'); onmessage = () => IPQueueCore.flush().then(r => postMessage(r));",
                       kind='application/javascript')
        elif path == '/api/data':
            status = STATE['status']
            if self.headers.get('X-IP-Queue-Owner') != STATE['owner']:
                status = 409
            self.reply(json.dumps({'private': STATE['owner']}), status)
        else:
            scripts = ''.join(f'<script src="/static/js/{name}.js"></script>' for name in
                              ('ip-queue-core', 'ip-core', 'ip-async', 'ip-offline'))
            self.reply(f'<!doctype html><meta name="ip-account-owner" content="{STATE["owner"] or ""}">'
                       f'<body><h1>private-owner-{STATE["owner"]}</h1>{scripts}</body>', kind='text/html')

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()
        status = STATE['status']
        if self.headers.get('X-IP-Queue-Owner') != STATE['owner']:
            status = 409
        if status == 200:
            STATE['writes'].append(json.loads(body))
        self.reply(json.dumps({'status': 'ok' if status == 200 else 'error'}), status)


def run():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            a = context.new_page()
            errors = []
            a.on('pageerror', lambda err: errors.append(str(err)))
            a.goto(origin + '/a')
            a.evaluate("IPQueueCore.enqueue({url:'/write',body:{work:'A'},dedupeKey:'same'})")
            assert a.evaluate('IPQueueCore.size()') == 1
            assert a.evaluate("IP.offline.read('/api/data')")['data'] == {'private': '11'}
            # Cache writes are asynchronous, so wait for the actual transaction.
            a.wait_for_function("IP.offline.cachedAt('/api/data')")

            STATE['owner'] = '22'
            b = context.new_page()
            b.goto(origin + '/b')
            assert b.evaluate('IPQueueCore.all()') == []
            b.evaluate("IPQueueCore.enqueue({url:'/write',body:{work:'B'},dedupeKey:'same'})")
            assert a.evaluate('IPQueueCore.size()') == 1  # B did not dedupe A.
            assert a.evaluate('IP.queue.flush()')['sent'] == 0  # stale tab cannot flush B.
            assert STATE['writes'] == []
            assert b.evaluate('IPQueueCore.flush()')['sent'] == 1
            assert STATE['writes'] == [{'work': 'B'}]

            # B has no copy of A's read, even though both use the same URL/DB.
            b.evaluate("Object.defineProperty(navigator,'onLine',{get:()=>false, configurable:true})")
            assert b.evaluate("IP.offline.read('/api/data').then(()=>false,()=>true)")
            a.evaluate("Object.defineProperty(navigator,'onLine',{get:()=>false, configurable:true})")
            assert a.evaluate("IP.offline.read('/api/data')")['data'] == {'private': '11'}
            a.evaluate("Object.defineProperty(navigator,'onLine',{get:()=>true, configurable:true})")
            STATE['owner'] = '11'
            STATE['status'] = 403
            assert a.evaluate("IP.offline.read('/api/data',{retries:0}).then(()=>false,e=>e.status===403)")
            STATE['status'] = 200
            STATE['switch'] = True
            assert a.evaluate('IPQueueCore.flush()')['kept'] == 1
            assert a.evaluate('IPQueueCore.all()')[0]['tries'] == 0
            assert STATE['writes'] == [{'work': 'B'}]

            STATE['owner'] = '11'
            STATE['owner_status'] = 503
            assert a.evaluate('IP.queue.flush()')['kept'] == 1
            assert a.evaluate('IP.queue.size()') == 1
            STATE['owner_status'] = 200
            STATE['status'] = 503
            for _ in range(6):
                assert a.evaluate('IPQueueCore.flush()')['kept'] == 1
            assert a.evaluate('IPQueueCore.size()') == 1
            STATE['status'] = 200
            # Real dedicated worker uses the same IndexedDB with no document/meta.
            result = a.evaluate("""() => new Promise((resolve,reject) => {
                const w = new Worker('/worker.js'); w.onerror=reject;
                w.onmessage=e=>{resolve(e.data);w.terminate()};w.postMessage('flush');
            })""")
            assert result['owner'] == '11' and result['sent'] == 1
            assert STATE['writes'] == [{'work': 'B'}, {'work': 'A'}]

            # Legacy records stay stored, are not assigned to the current user.
            a.evaluate("""async () => {
                const db=await IPQueueCore.openDb(); await new Promise((resolve,reject)=>{
                    const t=db.transaction('queue','readwrite');
                    t.objectStore('queue').put({id:'legacy',url:'/write',body:{work:'unknown'}});
                    t.oncomplete=resolve;t.onerror=reject;
                });
            }""")
            assert a.evaluate('IPQueueCore.flush()')['sent'] == 0
            assert a.evaluate("IPQueueCore.migrateFrom([{url:'/write'}]).then(()=>false,()=>true)")

            # A full queue must reject visibly and roll back optimistic UI.
            a.evaluate("""async () => {for(let i=0;i<60;i++) await IPQueueCore.enqueue({url:'/write',body:{i}})}""")
            result = a.evaluate("""async () => {
                const messages=[]; IP.toast=m=>messages.push(m); let value='before';
                Object.defineProperty(navigator,'onLine',{get:()=>false,configurable:true});
                const rejected=await IP.optimistic({label:'test change',
                  apply:()=>value='after',rollback:()=>value='before',
                  queueOnOffline:{url:'/write',body:{extra:true}}}).then(()=>false,()=>true);
                return {rejected,value,messages,count:await IPQueueCore.size()};
            }""")
            assert result['rejected'] and result['value'] == 'before' and result['count'] == 60
            assert not any('Saved offline' in m for m in result['messages'])

            result = a.evaluate("""async () => {
                const original=IDBDatabase.prototype.transaction;
                let rolledBack=false; const messages=[];IP.toast=m=>messages.push(m);
                IDBDatabase.prototype.transaction=()=>{throw new Error('synthetic storage failure')};
                try {
                  const rejected=await IP.optimistic({apply:()=>{},rollback:()=>rolledBack=true,
                    queueOnOffline:{url:'/write',body:{extra:true}}}).then(()=>false,()=>true);
                  return {rejected,rolledBack,messages};
                } finally {IDBDatabase.prototype.transaction=original;}
            }""")
            assert result['rejected'] and result['rolledBack']
            assert not any('Saved offline' in m for m in result['messages'])
            assert a.evaluate('IPQueueCore.size()') == 60
            assert a.evaluate("""async () => {
                const db=await IPQueueCore.openDb();return new Promise(resolve=>{
                    const r=db.transaction('queue').objectStore('queue').get('legacy');
                    r.onsuccess=()=>resolve(r.result.body.work);
                });
            }""") == 'unknown'

            # Actual registered service worker must not retain authenticated HTML.
            # Clear only this disposable test's new owner-bound rows.
            a.evaluate("""async () => {for(const row of await IPQueueCore.all()) await IPQueueCore.remove(row.id)}""")
            a.evaluate("Object.defineProperty(navigator,'onLine',{get:()=>true, configurable:true})")
            a.evaluate("navigator.serviceWorker.register('/sw-test.js',{scope:'/'}).then(()=>navigator.serviceWorker.ready)")
            a.reload()
            a.wait_for_function('navigator.serviceWorker.controller !== null')
            a.goto(origin + '/private-plan')
            assert 'private-owner-11' in a.content()
            context.set_offline(True)
            a.goto(origin + '/private-plan')
            assert 'private-owner-11' not in a.content()
            assert "haven't opened this page offline" in a.content()
            assert errors == []
            browser.close()
        print('PASS: real Chromium IndexedDB, two-account isolation, denied reads, owner-switch race, retained retries/legacy work, full-queue rollback, dedicated-worker replay and private HTML offline exclusion.')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    run()
