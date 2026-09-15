import os,time,hashlib,requests,socket
from pathlib import Path

PORTAL=os.getenv('AME_PORTAL_URL','').rstrip('/')
TOKEN=os.getenv('AME_BRIDGE_TOKEN','')
INCOMING=Path(os.getenv('AME_INCOMING',r'D:\AME-Production\Incoming'))
POLL=int(os.getenv('AME_POLL_SECONDS','3'))
HEAD={'Authorization':f'Bearer {TOKEN}','Content-Type':'application/json'}
seen={}

def api(method,path,**kw):
    if not PORTAL or not TOKEN: raise RuntimeError('AME_PORTAL_URL and AME_BRIDGE_TOKEN are required')
    r=requests.request(method,PORTAL+path,headers=HEAD,timeout=25,**kw); r.raise_for_status(); return r.json()
def stable(p):
    try:
        s=p.stat().st_size; old=seen.get(str(p)); seen[str(p)]=s; return old==s and s>0
    except OSError:return False
def docid(p):
    h=hashlib.sha256(); h.update(p.name.encode()); h.update(str(p.stat().st_size).encode()); h.update(str(int(p.stat().st_mtime)).encode()); return h.hexdigest()[:32]
def register(): api('POST','/api/bridge/register',json={'id':socket.gethostname(),'name':'AME Office PC'})
def sync_docs():
    INCOMING.mkdir(parents=True,exist_ok=True)
    for p in INCOMING.glob('*.pdf'):
        if stable(p): api('POST','/api/bridge/documents',json={'id':docid(p),'filename':p.name,'size':p.stat().st_size})
def process_job(job):
    # The existing proven local ERP uploader is connected here rather than moving Microsoft SSO/MFA to the cloud.
    # Until erp_upload.py is installed beside this bridge, fail safely and keep the PDF local.
    jid=job['id']; p=INCOMING/job['filename']
    if not p.exists():
        api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':False,'message':'Local scanner PDF not found'}); return
    uploader=Path(__file__).with_name('erp_upload.py')
    if not uploader.exists():
        api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':False,'message':'ERP worker not installed on office PC yet'}); return
    import subprocess,sys,json
    try:
        cp=subprocess.run([sys.executable,str(uploader),'upload',str(p),job.get('driver') or '',job.get('dt') or ''],capture_output=True,text=True,timeout=360)
        ok=cp.returncode==0; msg=(cp.stdout or cp.stderr or ('ERP upload verified' if ok else 'ERP worker failed'))[-500:]
    except Exception as e: ok=False; msg=str(e)
    api('POST',f'/api/bridge/jobs/{jid}/result',json={'ok':ok,'message':msg})
def main():
    print('AME Office Bridge started:',INCOMING,'->',PORTAL)
    while True:
        try:
            register(); sync_docs(); data=api('GET','/api/bridge/jobs/next'); job=data.get('job')
            if job: process_job(job)
        except Exception as e: print('Bridge:',e)
        time.sleep(POLL)
if __name__=='__main__': main()
