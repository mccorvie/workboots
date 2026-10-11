import csv,json,sys,subprocess,statistics,os,urllib.parse,time
pin=sys.argv[1]; only_existing=len(sys.argv)>2
sock=f"{os.environ['XDG_RUNTIME_DIR']}/tauceti-explore/{pin}/explore.sock"
def get(q,k=50):
    out=subprocess.run(["curl","-s","--unix-socket",sock,"-G","http://localhost/search","--data-urlencode","q="+q,"-d",f"k={k}"],capture_output=True,text=True).stdout
    return json.loads(out)
import sqlite3
db=sqlite3.connect(os.path.expanduser(f"~/.local/share/tauceti-explore/{pin}/lean_explore.db"))
rows=list(csv.reader(open("pilot/rank-queries.csv")))[1:]
res=[];lat=[]
for r in rows:
    name,q=r[0],r[4]
    if not db.execute("select 1 from declarations where name=?",(name,)).fetchone():
        print("ABSENT",name); continue
    t=time.time(); d=get(q); lat.append(time.time()-t)
    names=[h['name'] for h in d['hits']]
    rk=names.index(name)+1 if name in names else None
    res.append((name,rk)); print(rk,name)
rks=[x for _,x in res]
print("n",len(res),"median",statistics.median([x if x else 51 for x in rks]),"top10",sum(1 for x in rks if x and x<=10),"top1",sum(1 for x in rks if x==1),"notfound",sum(1 for x in rks if not x))
print("lat p50 ms",statistics.median(lat)*1000)
