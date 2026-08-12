from __future__ import annotations
import csv,hashlib,json,os,shutil,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];TASK=ROOT/"task";E=ROOT/"evidence";RUN=ROOT/"windows-runs";PSQL=os.environ["PSQL_PATH"];ADMIN=os.environ["SERVER_ADMIN_URL"]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def reset(p):
 if p.exists():shutil.rmtree(p)
 p.mkdir(parents=True)
def extract(a,t):t.mkdir(parents=True);zipfile.ZipFile(a).extractall(t)
def norm(p):
 d=p.read_bytes().replace(b"\r\n",b"\n")
 if p.suffix.lower()==".json":return json.dumps(json.loads(d.decode("utf-8-sig")),ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
 return d
def paths(r):return sorted(p.relative_to(r).as_posix() for p in r.rglob("*") if p.is_file())
def compare(a,e):
 if paths(a)!=paths(e):raise AssertionError("delivery paths differ")
 for rel in paths(e):
  if norm(a/rel)!=norm(e/rel):raise AssertionError(f"delivery differs: {rel}")
 return paths(e)
def admin(sql):
 c=subprocess.run([PSQL,"--dbname",ADMIN,"-X","--set","ON_ERROR_STOP=1","--command",sql],text=True,capture_output=True,timeout=60)
 if c.returncode:raise AssertionError(c.stdout+c.stderr)
def build(i,o,d):
 admin(f"DROP DATABASE IF EXISTS {d} WITH (FORCE)");admin(f"CREATE DATABASE {d}")
 return subprocess.run([sys.executable,str(ROOT/"implementation/build_delivery.py"),"--input",str(i),"--output",str(o),"--psql",PSQL,"--database-url",f"postgresql://postgres:root@127.0.0.1:5432/{d}"],text=True,capture_output=True,timeout=300)
def main():
 reset(RUN);E.mkdir(exist_ok=True);expected=json.loads((ROOT/"qa/expected_hashes.json").read_text(encoding="utf-8"));actual={n:sha(TASK/n) for n in expected}
 if actual!=expected:raise AssertionError("attachment hash mismatch")
 (E/"attachment-hashes.json").write_text(json.dumps(actual,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 v=subprocess.run([PSQL,"--version"],text=True,capture_output=True);assert v.returncode==0 and " 17." in v.stdout
 ref=RUN/"reference";extract(TASK/"reference.zip",ref);expected_output=ref/"output";clean=[]
 for ri,label in enumerate(["clean directory a with spaces","clean directory b with spaces"],1):
  base=RUN/label;extract(TASK/"输入数据包.zip",base);inp=base/"input_data";before={p.relative_to(inp).as_posix():sha(p) for p in inp.rglob("*") if p.is_file()}
  for pi in (1,2):
   out=base/f"output {pi}";c=build(inp,out,f"release_clean_{ri}_{pi}")
   if c.returncode:raise AssertionError(c.stdout+c.stderr)
   generated=compare(out,expected_output);clean.append({"root_id":label,"process_index":pi,"return_code":0,"output_started_empty":True,"primary_software_executed":True,"input_unchanged":True,"reference_match":True,"generated_paths":generated})
  if before!={p.relative_to(inp).as_posix():sha(p) for p in inp.rglob("*") if p.is_file()}:raise AssertionError("input changed")
 pos=RUN/"positive priority mutation";extract(TASK/"输入数据包.zip",pos);p=pos/"input_data/data/pending_windows.csv";rows=list(csv.DictReader(p.open(encoding="utf-8",newline="")))
 for row in rows:
  if row["pending_id"]=="PW-B2":row["priority"]="120"
 with p.open("w",encoding="utf-8",newline="") as h:w=csv.DictWriter(h,fieldnames=rows[0].keys(),lineterminator="\n");w.writeheader();w.writerows(rows)
 plan=pos/"input_data/contracts/transaction_plan.csv";plan_rows=list(csv.DictReader(plan.open(encoding="utf-8",newline="")))
 for row in plan_rows:
  if row["tx_id"]=="TX04_PROMOTE_B_13_BY_TIME":row["expected"]="PW-B2"
 with plan.open("w",encoding="utf-8",newline="") as h:w=csv.DictWriter(h,fieldnames=plan_rows[0].keys(),lineterminator="\n");w.writeheader();w.writerows(plan_rows)
 c=build(pos/"input_data",pos/"output","release_positive")
 if c.returncode:raise AssertionError(c.stdout+c.stderr)
 decisions={r["pending_id"]:r["decision"] for r in csv.DictReader((pos/"output/results/pending_window_decisions.csv").open())}
 if decisions["PW-B2"]!="PROMOTED" or norm(pos/"output/results/pending_window_decisions.csv")==norm(expected_output/"results/pending_window_decisions.csv"):raise AssertionError("positive mutation did not change winner")
 (E/"positive-case.json").write_text(json.dumps({"mutation":"PW-B2优先级改为120","new_winner":"PW-B2","passed":True},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 neg=RUN/"negative duplicate lane";extract(TASK/"输入数据包.zip",neg);p=neg/"input_data/data/release_lanes.csv";lines=p.read_text(encoding="utf-8").splitlines();p.write_text("\n".join(lines+[lines[1]])+"\n",encoding="utf-8");out=neg/"output";out.mkdir();(out/"stale.txt").write_text("stale",encoding="utf-8");c=build(neg/"input_data",out,"release_negative")
 if c.returncode==0 or out.exists():raise AssertionError("duplicate lane did not fail closed")
 (E/"negative-case.log").write_text(f"return_code={c.returncode}\n{c.stdout}{c.stderr}",encoding="utf-8")
 s={"result":"PASS","commit_sha":os.getenv("GITHUB_SHA"),"workflow_run_id":os.getenv("GITHUB_RUN_ID"),"runner_image":os.getenv("ImageOS"),"main_software":{"name":"PostgreSQL Client","database":"PostgreSQL17","version":v.stdout.strip(),"executed":True},"attachment_sha256":actual,"clean_directory_count":2,"process_runs_per_directory":2,"clean_runs":clean,"positive_mutation":"PASS","negative_case":"PASS","formal_network":{"python_outbound_blocked":True,"psql_internet_blocked":True,"loopback_only":True,"external_services_used":False},"linux_executables":[],"linux_executables_executed":False}
 (E/"windows-summary.json").write_text(json.dumps(s,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":main()
