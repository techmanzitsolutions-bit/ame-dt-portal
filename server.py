import os, sqlite3, secrets, json, hashlib
from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory, abort

BASE=os.path.dirname(os.path.abspath(__file__))
DB=os.getenv('AME_DB_PATH',os.path.join(BASE,'ame.db'))
ADMIN_PIN=os.getenv('AME_ADMIN_PIN','')
app=Flask(__name__,static_folder='web',static_url_path='')

def now(): return datetime.now(timezone.utc).isoformat()
def db():
 c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row; return c
def init():
 c=db(); c.executescript('''
 CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,filename TEXT NOT NULL,size INTEGER DEFAULT 0,state TEXT NOT NULL DEFAULT 'waiting',driver TEXT,dt TEXT,message TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,action TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',payload TEXT,result TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS bridge(id TEXT PRIMARY KEY,name TEXT,last_seen TEXT);
 CREATE TABLE IF NOT EXISTS bridge_devices(id TEXT PRIMARY KEY,name TEXT,token_hash TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,last_seen TEXT);
 CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT,profile_id TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS drivers(id TEXT PRIMARY KEY,name TEXT NOT NULL,account_id TEXT,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,event TEXT NOT NULL,detail TEXT,created_at TEXT NOT NULL);
 '''); c.commit(); c.close()
init()
def sha(s): return hashlib.sha256(s.encode('utf-8')).hexdigest()
def admin_auth():
 if not ADMIN_PIN: abort(503,'Admin PIN not configured')
 if not secrets.compare_digest(request.headers.get('X-AME-Admin',''),ADMIN_PIN): abort(401)
def bridge_auth():
 token=request.headers.get('Authorization','').removeprefix('Bearer ').strip()
 if not token: abort(401)
 c=db(); r=c.execute('SELECT id FROM bridge_devices WHERE token_hash=? AND active=1',(sha(token),)).fetchone()
 if not r: c.close(); abort(401)
 c.execute('UPDATE bridge_devices SET last_seen=? WHERE id=?',(now(),r['id'])); c.commit(); c.close(); return r['id']
def audit(event,detail=''):
 c=db(); c.execute('INSERT INTO audit(event,detail,created_at) VALUES(?,?,?)',(event,detail,now())); c.commit(); c.close()
@app.get('/')
def home(): return send_from_directory('web','index.html')
@app.get('/admin')
def admin(): return send_from_directory('web','admin.html')
@app.get('/api/health')
def health(): return jsonify(ok=True,service='AME DT Portal')
@app.get('/api/documents')
def documents():
 c=db(); rows=c.execute('SELECT * FROM documents ORDER BY created_at DESC LIMIT 100').fetchall(); c.close(); return jsonify([dict(x) for x in rows])
@app.get('/api/drivers')
def public_drivers():
 c=db(); rows=c.execute('SELECT id,name FROM drivers WHERE active=1 ORDER BY name').fetchall(); c.close(); return jsonify([dict(x) for x in rows])
@app.post('/api/documents/<docid>/submit')
def submit(docid):
 d=request.get_json(silent=True) or {}; driver=str(d.get('driver','')).strip(); dt=str(d.get('dt','')).strip()
 if not driver or not dt: return jsonify(error='Driver and DT are required'),400
 c=db(); dr=c.execute('SELECT d.*,a.profile_id,a.email,a.active account_active FROM drivers d LEFT JOIN accounts a ON a.id=d.account_id WHERE d.name=? AND d.active=1',(driver,)).fetchone(); row=c.execute('SELECT id FROM documents WHERE id=?',(docid,)).fetchone()
 if not row: c.close(); return jsonify(error='Document not found'),404
 if not dr or not dr['account_id'] or not dr['account_active']: c.close(); return jsonify(error='Driver is not mapped to an enabled ERP account in Admin'),400
 jid=secrets.token_hex(12); t=now(); payload=json.dumps({'driver_id':dr['id'],'account_id':dr['account_id'],'profile_id':dr['profile_id'],'email':dr['email'] or '-'})
 c.execute('UPDATE documents SET driver=?,dt=?,state=?,message=?,updated_at=? WHERE id=?',(driver,dt,'queued','Waiting for office ERP worker',t,docid)); c.execute('INSERT INTO jobs(id,document_id,action,state,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(jid,docid,'erp_upload','queued',payload,t,t)); c.commit(); c.close(); audit('job_queued',f'{driver} | {dt}'); return jsonify(ok=True,job_id=jid)
@app.post('/api/documents/<docid>/decision')
def decision(docid):
 d=request.get_json(silent=True) or {}; choice=str(d.get('choice','')).lower()
 if choice not in ('replace','discard'): return jsonify(error='Choose replace or discard'),400
 c=db(); row=c.execute('SELECT * FROM documents WHERE id=?',(docid,)).fetchone()
 if not row: c.close(); return jsonify(error='Document not found'),404
 prev=c.execute("SELECT payload FROM jobs WHERE document_id=? AND action IN ('erp_upload','replace') ORDER BY created_at DESC LIMIT 1",(docid,)).fetchone(); payload=prev['payload'] if prev else '{}'; jid=secrets.token_hex(12); t=now(); action='replace' if choice=='replace' else 'discard'; msg='Replace approved; waiting for office ERP worker' if choice=='replace' else 'Discard requested; waiting for office bridge'
 c.execute('UPDATE documents SET state=?,message=?,updated_at=? WHERE id=?',('queued',msg,t,docid)); c.execute('INSERT INTO jobs(id,document_id,action,state,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(jid,docid,action,'queued',payload,t,t)); c.commit(); c.close(); audit(choice,row['dt'] or ''); return jsonify(ok=True,job_id=jid)
@app.post('/api/bridge/pair')
def bridge_pair():
 d=request.get_json(silent=True) or {}; pin=str(d.get('pin','')); bid=str(d.get('id','')).strip()[:120]; name=str(d.get('name','AME Office PC')).strip()[:120]
 if not ADMIN_PIN: return jsonify(error='Admin PIN not configured on Railway'),503
 if not secrets.compare_digest(pin,ADMIN_PIN): return jsonify(error='Incorrect admin PIN'),403
 if not bid: return jsonify(error='Bridge id required'),400
 token=secrets.token_urlsafe(36); t=now(); c=db(); c.execute('INSERT INTO bridge_devices(id,name,token_hash,active,created_at,last_seen) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,token_hash=excluded.token_hash,active=1,last_seen=excluded.last_seen',(bid,name,sha(token),1,t,t)); c.execute('INSERT INTO bridge(id,name,last_seen) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,last_seen=excluded.last_seen',(bid,name,t)); c.commit(); c.close(); audit('bridge_paired',name); return jsonify(ok=True,token=token)
@app.post('/api/bridge/register')
def bridge_register():
 bid_auth=bridge_auth(); d=request.get_json(silent=True) or {}; bid=str(d.get('id',bid_auth))[:120]; name=str(d.get('name','AME Office PC'))[:120]; t=now(); c=db(); c.execute('INSERT INTO bridge(id,name,last_seen) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,last_seen=excluded.last_seen',(bid,name,t)); c.commit(); c.close(); return jsonify(ok=True)
@app.post('/api/bridge/documents')
def bridge_document():
 bridge_auth(); d=request.get_json(silent=True) or {}; docid=str(d.get('id','')).strip(); fn=str(d.get('filename','')).strip()
 if not docid or not fn: return jsonify(error='id and filename required'),400
 t=now(); c=db(); c.execute('INSERT INTO documents(id,filename,size,state,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET filename=excluded.filename,size=excluded.size,updated_at=excluded.updated_at',(docid,fn,int(d.get('size',0)),'waiting',t,t)); c.commit(); c.close(); return jsonify(ok=True)
@app.get('/api/bridge/jobs/next')
def next_job():
 bridge_auth(); c=db(); r=c.execute("SELECT j.*,d.filename,d.driver,d.dt FROM jobs j LEFT JOIN documents d ON d.id=j.document_id WHERE j.state='queued' ORDER BY j.created_at LIMIT 1").fetchone()
 if not r: c.close(); return jsonify(job=None)
 t=now(); changed=c.execute("UPDATE jobs SET state='claimed',updated_at=? WHERE id=? AND state='queued'",(t,r['id'])).rowcount
 if not changed: c.commit(); c.close(); return jsonify(job=None)
 if r['document_id']:
  msg='Office ERP worker started' if r['action']!='discard' else 'Office bridge is discarding the new scan'; c.execute('UPDATE documents SET state=?,message=?,updated_at=? WHERE id=?',('processing',msg,t,r['document_id']))
 c.commit(); out=dict(r); c.close()
 try: out.update(json.loads(out.get('payload') or '{}'))
 except Exception: pass
 return jsonify(job=out)
@app.post('/api/bridge/jobs/<jid>/progress')
def job_progress(jid):
 bridge_auth(); d=request.get_json(silent=True) or {}; msg=str(d.get('message','Working'))[:500]; stage=str(d.get('stage','working'))[:80]; t=now(); c=db(); r=c.execute('SELECT document_id FROM jobs WHERE id=?',(jid,)).fetchone()
 if not r: c.close(); return jsonify(error='Job not found'),404
 c.execute('UPDATE jobs SET result=?,updated_at=? WHERE id=?',(json.dumps({'stage':stage,'message':msg}),t,jid))
 if r['document_id']: c.execute('UPDATE documents SET message=?,updated_at=? WHERE id=?',(msg,t,r['document_id']))
 c.commit(); c.close(); return jsonify(ok=True)
@app.post('/api/bridge/jobs/<jid>/result')
def job_result(jid):
 bridge_auth(); d=request.get_json(silent=True) or {}; t=now(); c=db(); r=c.execute('SELECT document_id,action FROM jobs WHERE id=?',(jid,)).fetchone()
 if not r: c.close(); return jsonify(error='Job not found'),404
 docid=r['document_id']; msg=str(d.get('message','Completed'))[:500]
 if d.get('existing') and docid:
  c.execute("UPDATE jobs SET state='waiting_decision',result=?,updated_at=? WHERE id=?",(msg,t,jid)); c.execute("UPDATE documents SET state='needs_decision',message=?,updated_at=? WHERE id=?",(msg,t,docid))
 else:
  ok=bool(d.get('ok')); c.execute('UPDATE jobs SET state=?,result=?,updated_at=? WHERE id=?',('done' if ok else 'error',msg,t,jid))
  if docid:
   state='discarded' if d.get('discarded') else ('uploaded' if ok else 'error'); c.execute('UPDATE documents SET state=?,message=?,updated_at=? WHERE id=?',(state,msg,t,docid))
 c.commit(); c.close(); return jsonify(ok=True)
@app.post('/api/admin/login')
def admin_login():
 d=request.get_json(silent=True) or {}; pin=str(d.get('pin',''))
 if not ADMIN_PIN: return jsonify(error='Admin PIN not configured on Railway'),503
 if not secrets.compare_digest(pin,ADMIN_PIN): return jsonify(error='Incorrect admin PIN'),403
 return jsonify(ok=True)
@app.get('/api/admin/config')
def admin_config():
 admin_auth(); c=db(); ac=[dict(x) for x in c.execute('SELECT * FROM accounts ORDER BY name')]; dr=[dict(x) for x in c.execute('SELECT * FROM drivers ORDER BY name')]; br=[dict(x) for x in c.execute('SELECT * FROM bridge ORDER BY last_seen DESC')]; au=[dict(x) for x in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 30')]; c.close(); return jsonify(accounts=ac,drivers=dr,bridges=br,audit=au)
@app.post('/api/admin/accounts')
def add_account():
 admin_auth(); d=request.get_json(silent=True) or {}; name=str(d.get('name','')).strip(); email=str(d.get('email','')).strip(); profile=str(d.get('profile_id','')).strip() or secrets.token_hex(5)
 if not name: return jsonify(error='Account name required'),400
 aid=secrets.token_hex(8); c=db(); c.execute('INSERT INTO accounts(id,name,email,profile_id,created_at) VALUES(?,?,?,?,?)',(aid,name,email,profile,now())); c.commit(); c.close(); audit('account_added',name); return jsonify(ok=True,id=aid)
@app.post('/api/admin/accounts/<aid>/toggle')
def toggle_account(aid):
 admin_auth(); c=db(); c.execute('UPDATE accounts SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?',(aid,)); c.commit(); c.close(); return jsonify(ok=True)
@app.delete('/api/admin/accounts/<aid>')
def delete_account(aid):
 admin_auth(); c=db(); c.execute('UPDATE drivers SET account_id=NULL WHERE account_id=?',(aid,)); c.execute('DELETE FROM accounts WHERE id=?',(aid,)); c.commit(); c.close(); return jsonify(ok=True)
@app.post('/api/admin/accounts/<aid>/login')
def account_login(aid):
 admin_auth(); c=db(); a=c.execute('SELECT * FROM accounts WHERE id=? AND active=1',(aid,)).fetchone()
 if not a: c.close(); return jsonify(error='ERP account not found or disabled'),404
 jid=secrets.token_hex(12); t=now(); payload=json.dumps({'account_id':a['id'],'profile_id':a['profile_id'],'email':a['email'] or '-'}); c.execute('INSERT INTO jobs(id,document_id,action,state,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(jid,'','account_login','queued',payload,t,t)); c.commit(); c.close(); audit('erp_login_test',a['name']); return jsonify(ok=True,job_id=jid)
@app.get('/api/admin/jobs/<jid>')
def admin_job(jid):
 admin_auth(); c=db(); r=c.execute('SELECT id,action,state,result,updated_at FROM jobs WHERE id=?',(jid,)).fetchone(); c.close(); return jsonify(dict(r)) if r else (jsonify(error='Job not found'),404)
@app.post('/api/admin/drivers')
def add_driver():
 admin_auth(); d=request.get_json(silent=True) or {}; name=str(d.get('name','')).strip(); aid=str(d.get('account_id','')).strip()
 if not name or not aid: return jsonify(error='Driver and account required'),400
 did=secrets.token_hex(8); c=db(); c.execute('INSERT INTO drivers(id,name,account_id,created_at) VALUES(?,?,?,?)',(did,name,aid,now())); c.commit(); c.close(); audit('driver_added',name); return jsonify(ok=True,id=did)
@app.post('/api/admin/drivers/<did>')
def update_driver(did):
 admin_auth(); d=request.get_json(silent=True) or {}; c=db(); c.execute('UPDATE drivers SET name=COALESCE(?,name),account_id=COALESCE(?,account_id),active=COALESCE(?,active) WHERE id=?',(d.get('name'),d.get('account_id'),d.get('active'),did)); c.commit(); c.close(); return jsonify(ok=True)
@app.delete('/api/admin/drivers/<did>')
def delete_driver(did):
 admin_auth(); c=db(); c.execute('DELETE FROM drivers WHERE id=?',(did,)); c.commit(); c.close(); return jsonify(ok=True)
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','8765')))
