from __future__ import annotations
import json,os,shutil,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];E=ROOT/"evidence";W=ROOT/"work-reference"
def admin(sql):
 c=subprocess.run([os.environ["PSQL_PATH"],"--dbname",os.environ["SERVER_ADMIN_URL"],"-X","--set","ON_ERROR_STOP=1","--command",sql],text=True,capture_output=True,timeout=60)
 if c.returncode:raise SystemExit(c.stdout+c.stderr)
if W.exists():shutil.rmtree(W)
W.mkdir();E.mkdir(exist_ok=True)
with zipfile.ZipFile(ROOT/"task/输入数据包.zip") as z:z.extractall(W)
db="release_reference";admin(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)");admin(f"CREATE DATABASE {db}")
c=subprocess.run([sys.executable,str(ROOT/"implementation/build_delivery.py"),"--input",str(W/"input_data"),"--output",str(W/"output"),"--psql",os.environ["PSQL_PATH"],"--database-url",f"postgresql://postgres:root@127.0.0.1:5432/{db}"],text=True,capture_output=True,timeout=300)
if c.returncode:raise SystemExit(c.stdout+c.stderr)
with zipfile.ZipFile(E/"reference-candidate.zip","w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
 for p in sorted((W/"output").rglob("*")):
  if p.is_file():z.write(p,p.relative_to(W).as_posix())
(E/"reference-generation.json").write_text(json.dumps({"result":"PASS","commit_sha":os.getenv("GITHUB_SHA"),"workflow_run_id":os.getenv("GITHUB_RUN_ID"),"reference_members":sorted(p.relative_to(W).as_posix() for p in (W/"output").rglob("*") if p.is_file())},indent=2)+"\n")

