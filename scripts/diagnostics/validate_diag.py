"""Validate the model output failure diagnostic files through Pydantic."""
import sys
import json
import os

sys.path.insert(0, "backend")

from app.services.learning_report import LearningReportOutput
from pydantic import ValidationError

FAILED_TASKS = [
    ("tsk_831ecb1b86b6404f96c1d2548a6dd030", "1.5"),
    ("tsk_e1e9d741706a4364acb68253ac698020", "2.10"),
    ("tsk_8c54531a07d548399e8f35014dc52c29", "2.8"),
]

for tid, qid in FAILED_TASKS:
    diag_dir = os.path.join("workspace", "diagnostics", "model-output-failures", tid)
    if not os.path.exists(diag_dir):
        print(f"{qid}: no diag dir")
        continue
    files = os.listdir(diag_dir)
    if not files:
        print(f"{qid}: no diag files")
        continue

    fpath = os.path.join(diag_dir, files[0])
    with open(fpath, encoding="utf-8") as f:
        raw = f.read().strip()

    # Strip markdown code fences
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as ex:
        print(f"{qid}: JSON parse error: {ex}")
        continue

    try:
        LearningReportOutput.model_validate(data)
        print(f"{qid}: VALIDATION OK (should not happen if task failed)")
    except ValidationError as e:
        print(f"{qid}: VALIDATION FAILED with {len(e.errors())} errors:")
        for err in e.errors():
            print(f"  type={err.get('type')!r}")
            print(f"  loc ={err.get('loc')}")
            print(f"  msg ={err.get('msg')[:150]}")
            print()
