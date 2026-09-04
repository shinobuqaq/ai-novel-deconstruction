"""One-shot diagnostic: check 2.2 readiness from real workbench projection."""
import sys, json
sys.path.insert(0, "backend")
from app.config import get_settings
from app.db import create_db_engine
from app.services.workbench import build_workbench_projection
from app.services.learning_report import assess_learning_report_readiness
from sqlalchemy.orm import Session

settings = get_settings()
engine = create_db_engine(settings)
run_id = "run_e9bfe7fa166348bba53115394608955e"

with Session(engine) as session:
    proj = build_workbench_projection(session, run_id)

cde = proj.get("character_design_evidence")
print("character_design_evidence type:", type(cde).__name__)
if isinstance(cde, dict):
    print("  keys:", list(cde.keys()))
    print("  is_current:", cde.get("is_current"))
    cov = cde.get("coverage") or {}
    print("  event_coverage_complete:", cov.get("event_coverage_complete"))
    flds = cde.get("fields", [])
    print("  fields len:", len(flds))
    for f in flds:
        print(f"    field={f.get('field')} status={f.get('status')} has_evids={bool(f.get('evidence_ids'))}")
elif cde is None:
    print("  → None! Not populated in projection.")
else:
    print("  → unexpected:", cde)

print()
readiness = assess_learning_report_readiness(proj)
gen_ids = readiness.get("generation_ready_question_ids", [])
print("generation_ready_question_ids:", gen_ids)
print("overall ready:", readiness.get("ready"))
print()
for c in readiness.get("checks", []):
    qid = c.get("question_id")
    if qid == "2.2":
        print(f"[2.2] ready={c.get('ready')} answer_scope={c.get('answer_scope')}")
        print("  gaps:", c.get("gaps"))
