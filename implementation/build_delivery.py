from __future__ import annotations

import argparse
import atexit
import csv
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path


def run(command: list[str], *, stdin: str | None = None, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, input=stdin, text=True, capture_output=True, timeout=300)
    if completed.returncode and not allow_failure:
        raise RuntimeError(f"command failed with return code {completed.returncode}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}")
    return completed


def psql(psql_bin: str, database_url: str) -> list[str]:
    return [psql_bin, "--dbname", database_url, "-X", "--set", "ON_ERROR_STOP=1"]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def require_unique(rows: list[dict[str, str]], field: str, label: str) -> None:
    values = [row.get(field, "") for row in rows]
    duplicate = [value for value, count in Counter(values).items() if not value or count > 1]
    if duplicate:
        raise SystemExit(f"invalid {field} in {label}")


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def export_csv(psql_bin: str, database_url: str, query: str, target: Path) -> None:
    completed = run(psql(psql_bin, database_url) + ["--quiet", "--command", f"COPY ({query}) TO STDOUT WITH(FORMAT CSV,HEADER TRUE)"])
    target.write_text(completed.stdout, encoding="utf-8", newline="")


def scalar(psql_bin: str, database_url: str, query: str) -> str:
    return run(psql(psql_bin, database_url) + ["--tuples-only", "--no-align", "--command", query]).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    input_root = Path(args.input).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        shutil.rmtree(output)
    completed_flag = {"value": False}
    def clean_failure() -> None:
        if not completed_flag["value"] and output.exists(): shutil.rmtree(output)
    atexit.register(clean_failure)

    files = {
        "lanes": input_root / "data/release_lanes.csv",
        "windows": input_root / "data/release_windows.csv",
        "pending": input_root / "data/pending_windows.csv",
        "plan": input_root / "contracts/transaction_plan.csv",
        "contract": input_root / "contracts/window_contract.json",
        "starter": input_root / "starter/release_window_starter.sql",
        "readme": input_root / "README.md",
    }
    if any(not path.is_file() for path in files.values()): raise SystemExit("missing input file")
    lanes, windows, pending, plan = [read_csv(files[key]) for key in ("lanes","windows","pending","plan")]
    contract = json.loads(files["contract"].read_text(encoding="utf-8"))
    require_unique(lanes,"lane_id","release_lanes.csv"); require_unique(windows,"window_id","release_windows.csv")
    require_unique(pending,"pending_id","pending_windows.csv"); require_unique(plan,"tx_id","transaction_plan.csv")
    lane_ids={row["lane_id"] for row in lanes}; window_ids={row["window_id"] for row in windows}; pending_ids={row["pending_id"] for row in pending}
    if any(row["lane_id"] not in lane_ids for row in windows+pending): raise SystemExit("unknown lane_id")
    if contract.get("timezone")!="UTC" or contract.get("slot_bounds")!="[)": raise SystemExit("unsupported window contract")
    if contract.get("promotion_order") != ["priority DESC","requested_at ASC","pending_id ASC"]: raise SystemExit("unsupported promotion order")
    for row in windows+pending:
        if not row["start_at"].endswith("Z") or not row["end_at"].endswith("Z") or row["start_at"]>=row["end_at"]: raise SystemExit("invalid UTC interval")
    if any(row["status"] not in {"ACTIVE","CANCELLED"} for row in windows): raise SystemExit("invalid window status")
    if any(not row["priority"].isdigit() or not 0<=int(row["priority"])<=999 for row in pending): raise SystemExit("invalid priority")

    output.mkdir(parents=True); (output/"sql").mkdir(); (output/"tools").mkdir(); (output/"results").mkdir()
    solution=Path(__file__).resolve().parent/"solution.sql"; shutil.copy2(solution,output/"sql/solution.sql"); shutil.copy2(Path(__file__).resolve(),output/"tools/build_delivery.py")
    bootstrap=["DROP SCHEMA IF EXISTS release_ops CASCADE;","CREATE SCHEMA release_ops;",solution.read_text(encoding="utf-8")]
    for row in lanes: bootstrap.append(f"INSERT INTO release_ops.release_lane VALUES({sql_literal(row['lane_id'])},{sql_literal(row['edge_scope'])},{sql_literal(row['opens_at'])}::time,{sql_literal(row['closes_at'])}::time);")
    for row in windows: bootstrap.append(f"INSERT INTO release_ops.release_window(window_id,lane_id,release_ref,slot,status) VALUES({sql_literal(row['window_id'])},{sql_literal(row['lane_id'])},{sql_literal(row['release_ref'])},tstzrange({sql_literal(row['start_at'])},{sql_literal(row['end_at'])},'[)'),{sql_literal(row['status'])});")
    for row in pending: bootstrap.append(f"INSERT INTO release_ops.pending_window(pending_id,lane_id,release_ref,requested_slot,priority,requested_at) VALUES({sql_literal(row['pending_id'])},{sql_literal(row['lane_id'])},{sql_literal(row['release_ref'])},tstzrange({sql_literal(row['start_at'])},{sql_literal(row['end_at'])},'[)'),{int(row['priority'])},{sql_literal(row['requested_at'])}::timestamptz);")
    run(psql(args.psql,args.database_url),stdin="BEGIN;\n"+"\n".join(bootstrap)+"\nCOMMIT;\n")

    window_by_id={row["window_id"]:row for row in windows}; pending_by_slot={}
    for row in pending: pending_by_slot.setdefault((row["lane_id"],row["start_at"][11:16],row["end_at"][11:16]),[]).append(row)
    outcomes=[]
    for row in plan:
        result="COMMIT"; sqlstate=""; detail=""; state_preserved=True
        script="BEGIN; SET CONSTRAINTS ALL DEFERRED;\n"
        if row["operation"]=="SWAP":
            ids=row["target"].split("|")
            if len(ids)!=2 or any(value not in window_ids for value in ids): raise SystemExit("invalid SWAP target")
            script+=f"SELECT release_ops.swap_release_windows({sql_literal(ids[0])},{sql_literal(ids[1])});\nCOMMIT;"
        elif row["operation"]=="MOVE":
            wid,times=row["target"].split("=>"); start,end=times.split("-"); source=window_by_id.get(wid)
            if not source: raise SystemExit("invalid MOVE target")
            date=source["start_at"][:10]
            script+=f"SELECT release_ops.move_release_window({sql_literal(wid)},tstzrange('{date}T{start}:00Z','{date}T{end}:00Z','[)'));\nCOMMIT;"
        elif row["operation"]=="CANCEL":
            if row["target"] not in window_ids: raise SystemExit("invalid CANCEL target")
            script+=f"SELECT release_ops.cancel_release_window({sql_literal(row['target'])});\nCOMMIT;"
        elif row["operation"] in {"PROMOTE","ASSESS"}:
            lane,times=row["target"].split("@"); start,end=times.split("-"); candidates=pending_by_slot.get((lane,start,end),[])
            if not candidates: raise SystemExit("invalid queue target")
            date=candidates[0]["start_at"][:10]
            script+=f"SELECT release_ops.decide_pending_window({sql_literal(lane)},tstzrange('{date}T{start}:00Z','{date}T{end}:00Z','[)'),{sql_literal(row['tx_id'])});\nCOMMIT;"
        else: raise SystemExit("unsupported operation")
        executed=run(psql(args.psql,args.database_url),stdin=script,allow_failure=True)
        if executed.returncode:
            result="ROLLBACK"; detail=executed.stderr.strip(); sqlstate="23P01" if "23P01" in executed.stderr or "active release windows overlap" in executed.stderr else ""
        if row["operation"]=="MOVE":
            wid=row["target"].split("=>")[0]; actual=scalar(args.psql,args.database_url,f"SELECT to_char(lower(slot),'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')||'|'||to_char(upper(slot),'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') FROM release_ops.release_window WHERE window_id={sql_literal(wid)}")
            state_preserved=actual==window_by_id[wid]["start_at"]+"|"+window_by_id[wid]["end_at"]
        database_outcome = scalar(args.psql,args.database_url,f"SELECT coalesce((SELECT CASE WHEN decision='PROMOTED' THEN pending_id ELSE decision END FROM release_ops.pending_window WHERE decision_tx={sql_literal(row['tx_id'])} ORDER BY decision='PROMOTED' DESC,pending_id LIMIT 1),'')")
        matches=(row["expected"]==result) or (result=="COMMIT" and database_outcome==row["expected"])
        outcomes.append({"tx_id":row["tx_id"],"operation":row["operation"],"result":result,"expected":row["expected"],"matches_expected":str(matches).lower(),"sqlstate":sqlstate,"detail":detail,"state_preserved":str(state_preserved).lower()})

    results=output/"results"
    with (results/"transaction_outcomes.csv").open("w",encoding="utf-8",newline="") as h:
        w=csv.DictWriter(h,fieldnames=outcomes[0].keys(),lineterminator="\n");w.writeheader();w.writerows(outcomes)
    export_csv(args.psql,args.database_url,"SELECT window_id,lane_id,release_ref,to_char(lower(slot),'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS start_at,to_char(upper(slot),'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS end_at,status,coalesce(source_pending_id,'') AS source_pending_id FROM release_ops.release_window ORDER BY lane_id,lower(slot),window_id",results/"release_window_final.csv")
    export_csv(args.psql,args.database_url,"SELECT pending_id,lane_id,release_ref,priority,to_char(requested_at,'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS requested_at,decision,coalesce(decision_tx,'') AS decision_tx,coalesce(promoted_window_id,'') AS promoted_window_id FROM release_ops.pending_window ORDER BY pending_id",results/"pending_window_decisions.csv")
    export_csv(args.psql,args.database_url,"SELECT l.window_id AS left_window,r.window_id AS right_window,l.lane_id,true AS overlap_flag FROM release_ops.release_window l JOIN release_ops.release_window r ON l.lane_id=r.lane_id AND l.window_id<r.window_id AND l.status='ACTIVE' AND r.status='ACTIVE' AND l.slot&&r.slot ORDER BY 1,2",results/"active_overlap_probe.csv")
    export_csv(args.psql,args.database_url,"WITH p(probe_id,left_slot,right_slot,same_lane,left_status,right_status,expected_rule_overlap) AS (VALUES ('TOUCHING',tstzrange('2026-08-03 09:00Z','2026-08-03 10:00Z','[)'),tstzrange('2026-08-03 10:00Z','2026-08-03 11:00Z','[)'),true,'ACTIVE','ACTIVE',false),('PARTIAL',tstzrange('2026-08-03 09:00Z','2026-08-03 10:00Z','[)'),tstzrange('2026-08-03 09:30Z','2026-08-03 10:30Z','[)'),true,'ACTIVE','ACTIVE',true),('CONTAINED',tstzrange('2026-08-03 09:00Z','2026-08-03 11:00Z','[)'),tstzrange('2026-08-03 09:15Z','2026-08-03 09:45Z','[)'),true,'ACTIVE','ACTIVE',true),('OTHER_LANE',tstzrange('2026-08-03 09:00Z','2026-08-03 10:00Z','[)'),tstzrange('2026-08-03 09:30Z','2026-08-03 10:30Z','[)'),false,'ACTIVE','ACTIVE',false),('CANCELLED',tstzrange('2026-08-03 09:00Z','2026-08-03 10:00Z','[)'),tstzrange('2026-08-03 09:30Z','2026-08-03 10:30Z','[)'),true,'ACTIVE','CANCELLED',false)) SELECT probe_id,(left_slot&&right_slot) AS raw_range_overlap,same_lane,left_status,right_status,((left_slot&&right_slot) AND same_lane AND left_status='ACTIVE' AND right_status='ACTIVE') AS rule_overlap,expected_rule_overlap,(((left_slot&&right_slot) AND same_lane AND left_status='ACTIVE' AND right_status='ACTIVE')=expected_rule_overlap) AS rule_matches FROM p ORDER BY probe_id",results/"range_boundary_probe.csv")
    export_csv(args.psql,args.database_url,"SELECT t.tgname AS trigger_name,t.tgdeferrable AS deferrable,t.tginitdeferred AS initially_deferred,p.proname AS function_name FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid WHERE NOT t.tgisinternal AND t.tgname='release_window_active_no_overlap'",results/"trigger_inventory.csv")
    counts={"lanes":len(lanes),"initial_windows":len(windows),"pending_requests":len(pending),"planned_transactions":len(plan)}
    (results/"source_counts.json").write_text(json.dumps(counts,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    summary={"status":"READY" if all(row["matches_expected"]=="true" for row in outcomes) and int(scalar(args.psql,args.database_url,"SELECT count(*) FROM release_ops.release_window l JOIN release_ops.release_window r ON l.lane_id=r.lane_id AND l.window_id<r.window_id AND l.status='ACTIVE' AND r.status='ACTIVE' AND l.slot&&r.slot"))==0 else "HOLD","transaction_rows":len(outcomes),"window_rows":int(scalar(args.psql,args.database_url,"SELECT count(*) FROM release_ops.release_window")),"pending_rows":int(scalar(args.psql,args.database_url,"SELECT count(*) FROM release_ops.pending_window")),"active_overlap_pairs":0}
    (results/"handover.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (output/"HANDOVER.md").write_text("# 发布窗口处理摘要\n\ninput_data目录包含通道、窗口、排队请求和事务计划。\n\n使用tools/build_delivery.py连接空数据库。results目录保存事务、最终窗口、排队决定、冲突及边界结果。handover.json状态为READY时，可继续本批排程。\n",encoding="utf-8")
    if summary["status"]!="READY": raise RuntimeError("release window batch is not ready: "+json.dumps(outcomes,ensure_ascii=False))
    completed_flag["value"]=True


if __name__=="__main__": main()
