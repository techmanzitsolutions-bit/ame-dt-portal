import os, time, hashlib, requests, socket, subprocess, sys, json, shutil
from pathlib import Path

PORTAL = os.getenv('AME_PORTAL_URL', 'https://ame-dt-portal-production.up.railway.app').rstrip('/')
ROOT = Path(os.getenv('AME_ROOT', r'D:\AME-Production'))
INCOMING = Path(os.getenv('AME_INCOMING', str(ROOT / 'Incoming')))
PROCESSING = Path(os.getenv('AME_PROCESSING', str(ROOT / 'Processing')))
UPLOADED = Path(os.getenv('AME_UPLOADED', str(ROOT / 'Uploaded')))
FAILED = Path(os.getenv('AME_FAILED', str(ROOT / 'Failed')))
PROFILES = Path(os.getenv('AME_ERP_PROFILES', str(ROOT / 'ERP-Profiles')))
BRIDGE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = Path(os.getenv('AME_BRIDGE_TOKEN_FILE', str(BRIDGE_DIR / 'bridge-token.txt')))
POLL = int(os.getenv('AME_POLL_SECONDS', '3'))
LOG_FILE = BRIDGE_DIR / 'bridge.log'
seen = {}
TOKEN = ''

def log(*parts):
    msg = time.strftime('%Y-%m-%d %H:%M:%S') + ' ' + ' '.join(str(x) for x in parts)
    print(msg, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open('a', encoding='utf-8') as f: f.write(msg + '\n')
    except Exception: pass

def load_token():
    global TOKEN
    if TOKEN_FILE.exists(): TOKEN = TOKEN_FILE.read_text(encoding='utf-8').strip()
    return TOKEN

def save_token(token):
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip(), encoding='utf-8')
    load_token()

def api(method, path, auth=True, **kw):
    headers = {'Content-Type': 'application/json'}
    if auth:
        if not load_token(): raise RuntimeError('Office bridge is not paired yet.')
        headers['Authorization'] = f'Bearer {TOKEN}'
    r = requests.request(method, PORTAL + path, headers=headers, timeout=40, **kw)
    try: data = r.json()
    except Exception: data = {'error': r.text[:500]}
    if not r.ok: raise RuntimeError(data.get('error') or f'Portal HTTP {r.status_code}')
    return data

def pair(pin):
    host = socket.gethostname()
    d = api('POST', '/api/bridge/pair', auth=False, json={'id': host, 'name': f'AME Office PC - {host}', 'pin': pin})
    save_token(d['token']); log('Paired successfully with online AME portal.')

def stable(p):
    try:
        s = p.stat().st_size; old = seen.get(str(p)); seen[str(p)] = s; return old == s and s > 0
    except OSError: return False

def docid(p):
    h = hashlib.sha256(); h.update(p.name.encode('utf-8', errors='ignore')); h.update(str(p.stat().st_size).encode()); h.update(str(int(p.stat().st_mtime)).encode()); return h.hexdigest()[:32]

def ensure_dirs():
    for p in (INCOMING, PROCESSING, UPLOADED, FAILED, PROFILES): p.mkdir(parents=True, exist_ok=True)

def register():
    host = socket.gethostname(); api('POST', '/api/bridge/register', json={'id': host, 'name': f'AME Office PC - {host}'})

def sync_docs():
    ensure_dirs()
    for p in INCOMING.glob('*.pdf'):
        if stable(p): api('POST', '/api/bridge/documents', json={'id': docid(p), 'filename': p.name, 'size': p.stat().st_size})

def find_local_file(name):
    for folder in (PROCESSING, INCOMING, FAILED):
        p = folder / name
        if p.exists(): return p
    return None

def move_unique(src, dest_dir):
    dest_dir.mkdir(parents=True, exist_ok=True); dest = dest_dir / src.name
    if dest.exists(): dest = dest_dir / f'{src.stem}-{int(time.time())}{src.suffix}'
    shutil.move(str(src), str(dest)); return dest

def progress(jid, stage, message, needs_action=False):
    try: api('POST', f'/api/bridge/jobs/{jid}/progress', json={'stage': stage, 'message': message, 'needs_action': needs_action})
    except Exception as e: log('Progress report failed:', e)

def run_worker(args, jid, timeout=420):
    env = os.environ.copy(); env['AME_ERP_PROFILES'] = str(PROFILES)
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    final = None; started = time.time()
    while True:
        if time.time() - started > timeout: proc.kill(); raise RuntimeError('ERP operation timed out after 7 minutes.')
        line = proc.stdout.readline() if proc.stdout else ''
        if line:
            try: event = json.loads(line.strip())
            except Exception: event = None; log('ERP:', line.strip())
            if event:
                if event.get('type') == 'progress': progress(jid, event.get('stage','working'), event.get('message','Working'), bool(event.get('needs_action')))
                elif event.get('type') == 'result': final = event
        if proc.poll() is not None:
            if proc.stdout:
                for tail in proc.stdout.readlines():
                    try: event = json.loads(tail.strip())
                    except Exception: continue
                    if event.get('type') == 'result': final = event
            break
        time.sleep(0.1)
    return proc.returncode, final or {'ok': False, 'error': 'ERP worker stopped without a result.'}

def process_job(job):
    jid = job['id']; action = job.get('action') or 'erp_upload'; uploader = BRIDGE_DIR / 'erp_upload.py'
    if not uploader.exists(): api('POST', f'/api/bridge/jobs/{jid}/result', json={'ok':False,'message':'ERP worker is missing on office PC.'}); return
    if action == 'account_login':
        profile = job.get('profile_id') or ''; email = job.get('email') or '-'; progress(jid,'login',f'Opening dedicated ERP profile {profile}.')
        try:
            rc, final = run_worker([sys.executable,str(uploader),'login',profile,email],jid); ok = bool(final.get('ok')) and rc == 0; msg = 'ERP account session is ready.' if ok else final.get('error','ERP login/test failed.')
        except Exception as e: ok=False; msg=str(e)
        api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':ok,'message':msg}); return
    name = job.get('filename') or ''; p = find_local_file(name)
    if not p: api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':False,'message':'Local scanner PDF not found on office PC.'}); return
    if action == 'discard':
        try:
            dest=move_unique(p,FAILED); api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':True,'discarded':True,'message':f'New scan discarded locally: {dest.name}'})
        except Exception as e: api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':False,'message':str(e)})
        return
    if p.parent != PROCESSING:
        try: p=move_unique(p,PROCESSING)
        except Exception as e: api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':False,'message':f'Could not move PDF to Processing: {e}'}); return
    dt=job.get('dt') or ''; profile=job.get('profile_id') or ''; email=job.get('email') or '-'; args=[sys.executable,str(uploader),'upload',str(p),dt,profile,email]
    if action == 'replace': args.append('--replace')
    try:
        rc, final = run_worker(args,jid)
        if final.get('conflict'):
            api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':False,'existing':True,'message':final.get('error') or 'Signed DT already contains an attachment. Choose Replace or Discard.','existing_file':final.get('existing_file'),'resolved_dt':final.get('resolved_dt')}); return
        ok=bool(final.get('ok')) and rc==0
        if ok:
            dest=move_unique(p,UPLOADED); api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':True,'message':f'ERP upload saved and verified. Archived locally: {dest.name}'})
        else:
            err=final.get('error') or 'ERP upload failed.'; dest=move_unique(p,FAILED); api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':False,'message':f'{err} Local PDF moved to Failed: {dest.name}'})
    except Exception as e:
        try: dest=move_unique(p,FAILED); msg=f'{e} Local PDF moved to Failed: {dest.name}'
        except Exception: msg=str(e)
        api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':False,'message':msg})

def main():
    ensure_dirs(); load_token()
    if len(sys.argv)>=2 and sys.argv[1]=='--pair':
        pin=sys.argv[2] if len(sys.argv)>=3 else input('AME Admin PIN: ').strip(); pair(pin); return
    if not TOKEN:
        pin=os.getenv('AME_PAIR_PIN','').strip()
        if pin: pair(pin)
        else: raise RuntimeError('Bridge is not paired. Run: bridge.py --pair <Admin PIN>')
    log('AME Office Bridge started:',INCOMING,'->',PORTAL)
    while True:
        try:
            register(); sync_docs(); data=api('GET','/api/bridge/jobs/next'); job=data.get('job')
            if job: log('Claimed job',job.get('id'),job.get('action'),job.get('filename','')); process_job(job)
        except Exception as e: log('Bridge:',e)
        time.sleep(POLL)

if __name__=='__main__': main()
