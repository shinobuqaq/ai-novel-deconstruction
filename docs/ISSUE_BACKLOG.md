# GitHub Issue Backlog

鏈枃浠舵槸宸ョ▼ Backlog 涓?GitHub Issue 鐨勮寖鍥村熀绾裤€侻0-01鈥擬0-08 搴斿垎鍒缓绔嬫垨淇涓虹嫭绔?Issue锛涙瘡涓?Issue 蹇呴』鍖呭惈 Goal銆丷esearch basis銆丄dopted銆丷ejected銆丏eliverables銆丄cceptance criteria 鍜?Validation銆?

## Ready Queue锛氬厛鍋氳繖浜?

### M0-01 浠撳簱涓庡紑鍙戣剼鏈?
- [x] Monorepo 鐩綍
- [x] `.gitignore` / `.env.example`
- [x] `setup.ps1` / `dev.ps1` / `test.ps1`
- [ ] 鍦ㄧ敤鎴?Windows 鏈哄櫒涓婂畬鎴愪竴娆″喎鍚姩楠岃瘉

**Done:** 鏂版満鍣ㄦ寜 README 鍙湪 15 鍒嗛挓鍐呭惎鍔ㄤ笁杩涚▼銆?

### M0-02 閰嶇疆涓?Workspace
- [x] Pydantic Settings
- [x] Workspace 涓?Artifact 鐩綍
- [ ] Secret file 绾﹀畾
- [ ] 閰嶇疆閿欒鐨勫彲璇婃柇鎻愮ず

### M0-03 SQLite 涓?Migration
- [x] SQLite WAL / foreign keys
- [x] Alembic 鍒濆 migration
- [x] Migration smoke test锛堟竻鐞嗗鍏ュ墠宸插湪闅旂鐜楠岃瘉锛?
- [ ] 鏁版嵁搴撳浠?鎭㈠鑴氭湰

### M0-04 Task Lease
- [x] PENDING / RUNNING / SUCCEEDED / FAILED
- [x] lease owner / expiry / attempts
- [ ] 杩涚▼宕╂簝鍚庣殑杩囨湡绉熺害鎭㈠娴嬭瘯
- [ ] CANCEL command

### M0-05 Artifact Registry
- [x] 鍐呭鍝堝笇
- [x] 涓存椂鏂囦欢 + 鍘熷瓙鏇挎崲
- [x] 鍐呭鍘婚噸
- [ ] Artifact lineage / dependency edge
- [ ] DIRTY propagation baseline

### M0-06 Fake Provider
- [x] 纭畾鎬у搷搴?
- [x] Token 鐢ㄩ噺鍗犱綅璁板綍
- [ ] Provider contract test
- [ ] invalid JSON / timeout / rate-limit fake scenarios

### M0-07 API 涓庢渶灏?Run Center
- [x] Project / Task / Artifact API
- [x] React 鏈€灏忛〉闈?
- [ ] SSE 鎴栬疆璇㈣繘搴﹁鑼?
- [ ] 閿欒鐮佺洰褰曟帴鍏?UI

### M0-08 M0 鎭㈠涓庢祴璇?Gate
- [x] API health test
- [x] Project 鈫?Task 鈫?Artifact integration test
- [ ] Worker 鍦?Provider 鍚庛€丄rtifact commit 鍓嶅穿婧冪殑鎭㈠娴嬭瘯
- [ ] Windows 鍐峰惎鍔ㄦ祴璇曡褰?

---

## Vertical Slice 01

### VS01-01 瀵煎叆 2鈥? 绔?
- TXT/Markdown parser
- SourceDocument / SourceVersion / SourceUnit
- 绔犺妭椤哄簭銆佺┖绔犮€侀噸澶嶇珷 Issue

### VS01-02 EvidenceSpan
- `start_char/end_char/text_snapshot/context_hash`
- 绮剧‘鍥炶创娴嬭瘯
- 澶氬尮閰?Locator 杩斿洖 UNCERTAIN

### VS01-03 鍗曚竴璺疄浣撳€欓€?
- 鍏堝疄鐜?LLM 鎴栬鍒欎腑鐨勪竴鏉?
- Candidate 鍙粦瀹?Evidence锛屼笉鐩存帴 Canonical Write

### VS01-04 鍗曚竴璺簨浠跺€欓€?
- Structured Output Schema
- Trigger phrase + source text
- Character Locator

### VS01-05 Source Alignment Gate
- 0 鍖归厤锛歊EJECTED
- 1 鍖归厤锛歏ALID candidate
- 澶氬尮閰嶏細UNCERTAIN Issue

### VS01-06 Candidate / Issue Queue
- Candidate 鐘舵€佹満
- Issue 涓ラ噸搴︿笌瀵硅薄寮曠敤
- 鐢ㄦ埛鎺ュ彈/鎷掔粷鍩虹鍛戒护

### VS01-07 Simple Claim
- Claim text / type / Evidence IDs
- Fake adjudicator
- VERIFIED / UNCERTAIN / REJECTED

### VS01-08 Evidence Inspector
- 鍘熸枃楂樹寒
- Candidate / Claim 璺宠浆
- 灞曠ず鍧愭爣銆佹潵婧愩€佺姸鎬佸拰 Issue

---

## M1 Source / Evidence
- M1-01 Source schema and deterministic IDs
- M1-02 TXT/Markdown importer
- M1-03 DOCX/EPUB adapter锛堝彲寤跺悗锛?
- M1-04 chapter parser and manual correction
- M1-05 character authority and token projection
- M1-06 chunk overlap and dedup
- M1-07 FTS5 index
- M1-08 Source Inspector
- M1-09 source version diff and dirty roots
- M1-10 source gold fixtures

## M2 Entity Identity
- M2-01 EntityMention schema
- M2-02 alias/name route
- M2-03 embedding candidate route
- M2-04 candidate pair feature vector
- M2-05 hard negative guard
- M2-06 pair adjudication
- M2-07 guarded clustering
- M2-08 merge/split/redirect lineage
- M2-09 Entity Explorer
- M2-10 entity benchmark

## M3 Event
- M3-01 EventCandidate adapter
- M3-02 LLM phrase route
- M3-03 rule/state-change route
- M3-04 exact character locator
- M3-05 candidate union
- M3-06 boundary resolver
- M3-07 event type conflict handling
- M3-08 EventMention ID and status
- M3-09 event coreference and CanonicalEvent
- M3-10 Event Timeline and benchmark

## M4 Fact / State / Epistemic
- M4-01 TemporalFact and FactVersion
- M4-02 valid interval and recurrence
- M4-03 contradiction / coexisting / disputed
- M4-04 Fact Writer guard
- M4-05 StatePatch and deterministic reduce
- M4-06 State-at-T query
- M4-07 ActorKnowledge baseline
- M4-08 Fact/State Inspector
- M4-09 temporal benchmark

## M5 Retrieval
- M5-01 TaskProfile
- M5-02 lexical route
- M5-03 vector route
- M5-04 temporal/entity route
- M5-05 graph diffusion route
- M5-06 candidate normalization
- M5-07 RRF and rerank
- M5-08 route quota and diversity
- M5-09 source backfill and landing audit
- M5-10 retrieval ablation

## M6 Claim / Specialist
- M6-01 AnalysisClaim schema
- M6-02 support/counter retrieval
- M6-03 evidence adjudication
- M6-04 conflict-aware aggregation
- M6-05 split/narrow/repair/drop
- M6-06 six specialist contracts
- M6-07 specialist batch execution
- M6-08 Claim Inspector
- M6-09 claim benchmark

## M7 Report / UI
- M7-01 Artifact Compiler
- M7-02 report templates
- M7-03 Project dashboard
- M7-04 Run Center DAG
- M7-05 Issue Queue
- M7-06 Entity/Event/Fact/Claim navigation
- M7-07 diff view
- M7-08 DOCX exporter
- M7-09 publish gate

## M8 Benchmark
- M8-01 30鈥?0 chapter gold corpus
- M8-02 metric harness
- M8-03 ablation runner
- M8-04 cost and latency dashboard
- M8-05 crash recovery suite
- M8-06 dirty recompute measurement
- M8-07 performance profiling
- M8-08 Quality Mode decision report
- M8-09 Balanced/Economy proposal
