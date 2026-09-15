import os, sqlite3, secrets
from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory, abort

BASE=os.path.dirname(os.path.abspath(__file__))
DB=os.getenv('AME_DB_PATH',os.path.join(BASE,'ame.db'))
BRIDGE_TOKEN=os.getenv('AME_BRIDGE_TOKEN','')
ADMIN_PIN=os.getenv('AME_ADMIN_PIN','')
app=Flask(__name__,static_folder='web',static_url_path='')

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init():
    c=db(); c.executescript('''
    CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, filename TEXT NOT NULL, size INTEGER DEFAULT 0, state TEXT NOT NULL DEFAULT 'waiting', driver TEXT, dt TEXT, message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, document_id TEXT NOT NULL, action TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', payload TEXT, result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bridge(id TEXT PRIMARY KEY, name TEXT, last_seen TEXT);
    '''); c.commit(); c.close()
init()

def bridge_auth():
    if not BRIDGE_TOKEN: abort(503,'Bridge token not configured')
    token=request.headers.get('Authorization','').removeprefix('Bearer ').strip()
    if not secrets.compare_digest(token,BRIDGE_TOKEN): abort(401)

def admin_auth():
    if not ADMIN_PIN: abort(503,'Admin PIN not configured')
    if not secrets.compare_digest(request.headers.get('X-AME-Admin',''),ADMIN_PIN): abort(401)

@app.get('/')
def home(): return send_from_directory('web','index.html')
@app.get('/admin')
def admin(): return send_from_directory('web','admin.html')
@app.get('/api/health')
def health(): return jsonify(ok=True,service='AME DT Portal')
@app.get('/api/documents')
def documents():
    c=db(); rows=c.execute('SELECT * FROM documents ORDER BY created_at DESC LIMIT 100').fetchall(); c.close(); return jsonify([dict(x) for x in rows])
@app.post('/api/documents/<docid>/submit')
def submit(docid):
    data=request.get_json(silent=True) or {}; driver=str(data.get('driver','')).strip(); dt=str(data.get('dt','')).strip()
    if not driver or not dt: return jsonify(error='Driver and DT are required'),400
    jid=secrets.token_hex(12); t=now(); c=db(); row=c.execute('SELECT id FROM documents WHERE id=?',(docid,)).fetchone()
    if not row: c.close(); return jsonify(error='Document not found'),404
    c.execute('UPDATE documents SET driver=?,dt=?,state=?,message=?,updated_at=? WHERE id=?',(driver,dt,'queued','Waiting for office ERP worker',t,docid))
    c.execute('INSERT INTO jobs(id,document_id,action,state,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(jid,docid,'erp_upload','queued','',t,t)); c.commit(); c.close(); return jsonify(ok=True,job_id=jid)
@app.post('/api/bridge/register')
def bridge_register():
    bridge_auth(); data=request.get_json(silent=True) or {}; bid=str(data.get('id','office-pc'))[:80]; t=now(); c=db(); c.execute('INSERT INTO bridge(id,name,last_seen) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,last_seen=excluded.last_seen',(bid,str(data.get('name','AME Office PC'))[:120],t)); c.commit(); c.close(); return jsonify(ok=True)
@app.post('/api/bridge/documents')
def bridge_document():
    bridge_auth(); d=request.get_json(silent=True) or {}; docid=str(d.get('id','')).strip(); fn=str(d.get('filename','')).strip()
    if not docid or not fn: return jsonify(error='id and filename required'),400
    t=now(); c=db(); c.execute('INSERT INTO documents(id,filename,size,state,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET filename=excluded.filename,size=excluded.size,updated_at=excluded.updated_at',(docid,fn,int(d.get('size',0)),'waiting',t,t)); c.commit(); c.close(); return jsonify(ok=True)
@app.get('/api/bridge/jobs/next')
def next_job():
    bridge_auth(); c=db(); r=c.execute("SELECT j.*,d.filename,d.driver,d.dt FROM jobs j JOIN documents d ON d.id=j.document_id WHERE j.state='queued' ORDER BY j.created_at LIMIT 1").fetchone()
    if not r: c.close(); return jsonify(job=None)
    t=now(); c.execute("UPDATE jobs SET state='claimed',updated_at=? WHERE id=? AND state='queued'",(t,r['id'])); c.execute("UPDATE documents SET state='processing',message='Office ERP worker started',updated_at=? WHERE id=?",(t,r['document_id'])); c.commit(); c.close(); return jsonify(job=dict(r))
@app.post('/api/bridge/jobs/<jid>/result')
def job_result(jid):
    bridge_auth(); d=request.get_json(silent=True) or {}; ok=bool(d.get('ok')); state='done' if ok else 'error'; t=now(); msg=str(d.get('message','ERP upload verified' if ok else 'ERP upload failed'))[:500]; c=db(); r=c.execute('SELECT document_id FROM jobs WHERE id=?',(jid,)).fetchone()
    if not r: c.close(); return jsonify(error='Job not found'),404
    c.execute('UPDATE jobs SET state=?,result=?,updated_at=? WHERE id=?',(state,msg,t,jid)); c.execute('UPDATE documents SET state=?,message=?,updated_at=? WHERE id=?',('uploaded' if ok else 'error',msg,t,r['document_id'])); c.commit(); c.close(); return jsonify(ok=True)
@app.get('/api/admin/status')
def status():
    admin_auth(); c=db(); b=c.execute('SELECT * FROM bridge ORDER BY last_seen DESC').fetchall(); counts={r['state']:r['n'] for r in c.execute('SELECT state,count(*) n FROM documents GROUP BY state')}; c.close(); return jsonify(bridges=[dict(x) for x in b],documents=counts)

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','8765')))
