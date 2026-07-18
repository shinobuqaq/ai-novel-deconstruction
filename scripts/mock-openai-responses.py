from __future__ import annotations

import argparse
import json

import uvicorn
from fastapi import FastAPI


app = FastAPI(title="Novel analysis preview provider")


@app.get("/v1/models")
def models() -> dict:
    return {
        "data": [
            {"id": "novel-quality-preview", "object": "model"},
            {"id": "novel-economy-preview", "object": "model"},
        ]
    }


def _analysis_result(source: str) -> dict:
    entities = []
    events = []
    if "林舟收到一封没有署名的信" in source:
        entities.extend([
            {
                "name": "林舟",
                "entity_type": "PERSON",
                "aliases": [],
                "description": "收到匿名信后仍前往白塔，并追查失踪船队线索的人。",
                "evidence_quotes": ["林舟收到一封没有署名的信"],
                "confidence": 98,
            },
            {
                "name": "周岚",
                "entity_type": "PERSON",
                "aliases": ["守门人"],
                "description": "白塔守门人，认出旧船厂水印并协助林舟调查。",
                "evidence_quotes": ["守门人周岚拦住他"],
                "confidence": 97,
            },
            {
                "name": "白塔",
                "entity_type": "PLACE",
                "aliases": [],
                "description": "雾港中的关键地点，顶层出现与失踪船队有关的蓝色灯火。",
                "evidence_quotes": ["白塔顶层亮起蓝色灯火"],
                "confidence": 96,
            },
        ])
        events.extend([
            {
                "title": "林舟收到匿名警告信",
                "event_type": "DISCOVERY",
                "summary": "林舟收到警告他午夜前不要登上白塔的匿名信。",
                "participants": ["林舟"],
                "evidence_quotes": ["午夜前不要登上白塔"],
                "confidence": 98,
            },
            {
                "title": "白塔亮起蓝色灯火",
                "event_type": "DISCOVERY",
                "summary": "午夜时白塔出现与十年前失踪船队返航信号相同的蓝色灯火。",
                "participants": ["林舟", "周岚"],
                "evidence_quotes": ["白塔顶层亮起蓝色灯火"],
                "confidence": 97,
            },
        ])
    return {"entities": entities, "events": events}


@app.post("/v1/responses")
def responses(payload: dict) -> dict:
    source = str(payload.get("input") or "")
    result = json.dumps(_analysis_result(source), ensure_ascii=False)
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": result}],
            }
        ],
        "usage": {"input_tokens": 320, "output_tokens": 180},
    }


@app.post("/v1/chat/completions")
def chat_completions(payload: dict) -> dict:
    messages = payload.get("messages") or []
    source = "\n".join(
        str(item.get("content") or "")
        for item in messages
        if isinstance(item, dict)
    )
    result = json.dumps(_analysis_result(source), ensure_ascii=False)
    return {
        "choices": [{"message": {"role": "assistant", "content": result}}],
        "usage": {"prompt_tokens": 320, "completion_tokens": 180},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
