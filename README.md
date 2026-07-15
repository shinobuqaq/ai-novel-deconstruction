# AI 鑷姩灏忚鎷嗕功鍒嗘瀽鍣?

杩欐槸椤圭洰鐨?**M0 宸ョ▼楠ㄦ灦 + Vertical Slice 01 璧风偣**銆傚綋鍓嶄粨搴撳疄鐜扮殑鏄渶灏忓彲杩愯闂幆锛岃€屼笉鏄畬鏁寸殑灏忚鎷嗕功浜у搧锛?

- FastAPI API
- SQLite + Alembic
- Project / Task / Artifact 涓夌被鍩虹瀵硅薄
- 鍗曟満杞 Worker 涓庝换鍔＄绾?
- Fake Provider
- React + TypeScript + Vite 鏈€灏忔帶鍒跺彴
- Windows 涓€閿畨瑁呫€佸惎鍔ㄤ笌娴嬭瘯鑴氭湰
- M0鈥擬8 寮€鍙?Backlog

> M0 鐨勭洰鏍囨槸楠岃瘉浠撳簱缁撴瀯銆佷换鍔℃仮澶嶃€丄rtifact 鍐欏叆銆佸墠鍚庣杩炴帴鍜屽紑鍙戞祦绋嬨€傚悗缁兘鍔涙寜 `docs/ISSUE_BACKLOG.md` 閫愭瀹炵幇銆?


## 0. 鐜瑕佹眰

- Windows 10/11
- Git
- Python 3.12 鎴?3.13
- Node.js 20 鎴栨洿鏂扮増鏈紙鍖呭惈 npm锛?

## 1. Windows 蹇€熷惎鍔?

鍦ㄩ」鐩牴鐩綍鎵撳紑 PowerShell锛?

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
.\scripts\dev.ps1
```

鍚姩鍚庯細

- 鍓嶇锛歚http://127.0.0.1:5173`
- API 鏂囨。锛歚http://127.0.0.1:8000/docs`
- 鍋ュ悍妫€鏌ワ細`http://127.0.0.1:8000/health`

`dev.ps1` 浼氬垎鍒惎鍔?API銆乄orker 鍜?Frontend銆傚叧闂搴?PowerShell 绐楀彛鍗冲彲鍋滄銆?

## 2. 楠岃瘉鏈€灏忛棴鐜?

1. 鍦ㄩ〉闈㈠垱寤洪」鐩€?
2. 鍒涘缓涓€涓?`fake.echo` 浠诲姟銆?
3. Worker 棰嗗彇浠诲姟骞剁敓鎴愪笉鍙彉 JSON Artifact銆?
4. 椤甸潰鍒锋柊鍚庡彲鐪嬪埌浠诲姟鐘舵€佺敱 `PENDING` 鍙樹负 `SUCCEEDED`銆?
5. Artifact 鏂囦欢鍐欏叆 `workspace/artifacts/<project_id>/`銆?

## 3. 甯哥敤鍛戒护

```powershell
# 鍙垵濮嬪寲鏁版嵁搴?
.\scripts\init-db.ps1

# 杩愯娴嬭瘯
.\scripts\test.ps1

# 鍙繍琛屼竴娆?Worker锛堟柟渚胯皟璇曪級
.\.venv\Scripts\python.exe -m app.worker --once
```

## 4. 褰撳墠鐩綍

```text
ai-novel-deconstruction/
鈹溾攢 backend/               FastAPI銆乄orker銆侀鍩熶笌鎸佷箙鍖?
鈹溾攢 frontend/              React + TypeScript + Vite
鈹溾攢 docs/                  Roadmap銆両ssue Backlog銆丄DR銆佺爺绌惰拷韪?
鈹溾攢 prompts/               鍚庣画 Prompt Registry
鈹溾攢 schemas/               Structured Output JSON Schema
鈹溾攢 fixtures/              灏忓瀷鍙噸澶嶆祴璇曡鏂?
鈹溾攢 scripts/               Windows 寮€鍙戣剼鏈?
鈹溾攢 workspace/             鏈湴鏁版嵁搴撱€丄rtifact 涓庣敤鎴锋暟鎹紙涓嶈繘 Git锛?
鈹斺攢 .env.example
```

## 5. 褰撳墠缂栫爜椤哄簭

鍏堝畬鎴?`docs/ISSUE_BACKLOG.md` 涓殑 **M0 Ready Queue**锛岄殢鍚庤繘鍏?Vertical Slice 01锛?

```text
瀵煎叆 2鈥? 绔?
鈫?EvidenceSpan
鈫?涓€涓疄浣撳€欓€夎矾绾?
鈫?涓€涓簨浠?LLM 璺嚎
鈫?Source Alignment
鈫?Candidate / Issue
鈫?绠€鍗?Claim
鈫?Evidence Inspector
```

## 6. 鐮旂┒鎴愭灉濡備綍杩涘叆浠ｇ爜

鏈粨搴撲笉鏄粠 P01鈥擯18 涓€夋嫨涓€涓」鐩?Fork 鑰屾潵锛岃€屾槸渚濇嵁璺ㄩ」鐩璁＄粨璁鸿繘琛?clean-room 鐙珛瀹炵幇銆?

- [鐮旂┒杩借釜鐭╅樀](docs/RESEARCH_TRACEABILITY.md)锛歅xx / M / G 鈫?妯″潡 鈫?Issue 鈫?楠岃瘉
- [绗笁鏂逛唬鐮佺櫥璁癩(docs/THIRD_PARTY_CODE.md)锛氬綋鍓嶆湭澶嶅埗 P01鈥擯18 婧愮爜锛涙湭鏉ュ紩鍏ュ繀椤诲浐瀹氭潵婧愩€佺増鏈拰璁稿彲璇?
- [寮€鍙戣矾绾垮浘](docs/ROADMAP.md)
- [Issue Backlog](docs/ISSUE_BACKLOG.md)
- [M0 Windows 冷启动与最小闭环验证记录](docs/M0_COLD_START_VALIDATION.md)

## 7. 鏂囨。鏉冨▉

1. `銆婁骇鍝佸畾涔変笌 Quality Mode 鍘熷瀷鏂规 V0.1銆媊锛氫骇鍝佽寖鍥翠笌楠屾敹
2. `銆婂€欓€夌郴缁熸灦鏋勪笌鎶€鏈璁?V0.1銆媊锛氭灦鏋勪笌鎶€鏈竟鐣?
3. `銆婃満鍒舵紨杩涘彴璐?V0.16銆媊锛氱爺绌舵満鍒剁姸鎬?
4. `銆奝01鈥擯17 闃舵鎬诲璁℃姤鍛?V1.0銆媊 涓?P01鈥擯18 鍗曢」鐩。妗堬細鐮旂┒璇佹嵁
5. 鏈?README锛氬紑鍙戝叆鍙ｏ紝涓嶆浛浠ｄ笂杩版寮忔枃妗?

## 8. License

椤圭洰褰撳墠浠嶄负绉佹湁浠撳簱锛岃鍙瘉灏氭湭鏈€缁堢‘瀹氥€傚湪姝ｅ紡鍏紑鎴栧垎鍙戝墠琛ュ厖 `LICENSE`锛屽苟瀹屾垚绗笁鏂逛緷璧栦笌閫氱煡澶嶆牳銆?
