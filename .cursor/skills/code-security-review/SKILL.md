---
name: code-security-review
description: SAST 보안 코드 리뷰 — Sub-agent Dispatch + Rules File Read 기반 Context Isolation. Semgrep + Gitleaks + Trivy + AI 분석. '보안 점검', '보안 리뷰', '코드 리뷰', 'security review', '취약점 점검', '취약점 스캔' 요청 시 사용.
---

# 보안 코드 리뷰 (SAST) — Sub-agent Dispatch

> **v3.0** | Orchestrator + Phase A/B/C/D Sub-agent Isolation + Ralph Loop 기반 라운드 분할
> **원칙**: 도구가 잘 탐지하는 영역은 도구에 위임. AI는 9개 카테고리를 3 라운드로 분할하여 각각 fresh context로 분석. Merge(merge_findings.py)에서 결정적 병합, Phase C에서 FP Triage, Phase D에서 리포트 작성.
> **Context 보호**: Phase B를 3 라운드로 분할 dispatch → 각 sub-agent가 3개 카테고리만 담당 → context 고갈 방지.
>
> **GHA vs Local 차이**: GitHub Actions 워크플로(`weekly-security-review.yml`)에서는 Phase B를 단일 `agent` 세션으로 실행합니다 (GHA에서는 Task 도구 병렬 dispatch가 불가). Local Cursor 오케스트레이션에서만 3라운드 병렬 dispatch를 사용합니다.

---

## 호출 명령어

| 명령어 | 모드 | 설명 |
|--------|------|------|
| `[대상] 보안 점검해줘` | 자동 판별 | 대상에 따라 간단/감사 모드 결정 |
| `수정된 파일 보안 점검` | 간단 모드 | Git 변경 파일만 스캔 -> 채팅창 출력 |
| `[리포] 전체 보안 점검` | 감사 모드 | 전체 리포 스캔 -> raw JSON + 리포트 MD 저장 |

---

## 실행 모드

| 모드 | 트리거 키워드 | 출력 |
|------|--------------|------|
| **간단 모드** | 수정된, staged, 변경된, 커밋, diff | 채팅창 출력 -> "수정해줘" -> 바로 적용 |
| **감사 모드** | 전체, 리포, 감사, audit, all, 프로젝트 | raw JSON + 리포트 MD 파일 저장 |

---

## 아키텍처

```
Orchestrator (상태 관리 + Dispatch + 검증)
  │
  ├── Phase A: Tool Scan (Orchestrator 직접 실행)
  │     → rules/phase-a-scan.md 읽고 Shell로 도구 실행
  │     → 출력: raw JSON 3개 + counts.txt → 디스크 저장
  │     → context 영향 낮음: Shell 출력은 파일로 저장, context에 누적 안 됨
  │
  ├── Phase B: AI Analysis (3 라운드 × generalPurpose sub-agent)
  │     → 각 라운드가 rules/phase-b-analysis.md 해당 라운드 섹션 실행
  │     → 출력: ai-r1.json, ai-r2.json, ai-r3.json → 병합 → ai-analysis.json
  │     → Ralph Loop: 라운드 분할로 context 고갈 방지 + 전 카테고리 커버리지
  │
  ├── Merge: merge_findings.py (Orchestrator 직접 실행)
  │     → Shell로 merge_findings.py 실행
  │     → 입력: raw JSONs (semgrep, gitleaks, trivy, ai-analysis)
  │     → 정규화 → 심각도 매핑 → 중복 병합 → hash 생성 (결정적)
  │     → 출력: unified-findings.json (findings 배열, hash/개수 확정) → 디스크 저장
  │
  ├── Phase C: FP Triage (generalPurpose sub-agent)
  │     → rules/phase-c-triage.md 읽고 수행
  │     → 입력: unified-findings.json (merge 출력) + 대상 소스
  │     → FP 판단 (Critical/High는 코드 Read) → confidence 부여 → 그룹화 분석
  │     → 출력: unified-findings.json에 confidence + groups 추가 → 디스크 저장
  │
  ├── Classify: notion_classify.py (GHA에서만 실행)
  │     → Notion DB 조회 → 각 finding에 _status(New/Recurring/FP/Accepted-Risk) 부여
  │     → 출력: classified-findings.json → 디스크 저장
  │     → Local 실행 시 건너뜀 (Phase D는 _status 유무를 자동 감지)
  │
  └── Phase D: Report Writer (generalPurpose sub-agent)
        → rules/phase-d-report.md 읽고 수행
        → 입력: Findings JSON + counts.txt + 대상 소스
          - GHA: classified-findings.json (_status 포함 → 뱃지/접힘 렌더링)
          - Local: unified-findings.json (_status 없음 → 기존 형식 그대로)
        → 출력: final-report.md → 디스크 저장
        → sub-agent 필수: 깨끗한 context에서 규칙 엄격 적용
```

**역할 분리:**

| 구분 | Orchestrator | Phase B/C/D Sub-agent |
|------|-------------|----------------------|
| 역할 | Phase A 직접 실행 + B dispatch(3라운드) + 병합 + Merge(직접) + C dispatch + D dispatch + 검증 | rules 파일 Read + 할당된 Phase/라운드 수행 |
| 도구 | Shell, Read, Task (dispatch) | Shell, Read, Write, Grep, Glob |
| Context | Phase A 실행 결과 (경로 + 개수) | rules 파일 + 디스크 데이터 |

> Phase B/C/D sub-agent는 `generalPurpose` 타입 사용. Phase B만 3개 라운드 병렬 dispatch.
> Phase A는 context 오염이 적어 Orchestrator가 직접 실행.

---

## Rules 파일 경로

> `SKILL_DIR` = 이 SKILL.md가 위치한 디렉토리의 절대 경로.
> 예: 이 파일이 `/a/b/.cursor/skills/code-security-review/SKILL.md`이면
> `SKILL_DIR` = `/a/b/.cursor/skills/code-security-review`
> Orchestrator는 dispatch 시 `{SKILL_DIR}`을 실제 절대 경로로 치환하여 전달.

| Phase | Rules 파일 | 내용 |
|-------|-----------|------|
| A | `{SKILL_DIR}/rules/phase-a-scan.md` | 도구 설치, 스캔 명령, 개수 집계 |
| B | `{SKILL_DIR}/rules/phase-b-analysis.md` | 3 라운드 × 3 카테고리, 라운드별 분석 절차/Grep 패턴, JSON 스키마 |
| C | `{SKILL_DIR}/rules/phase-c-triage.md` | FP 판단 (confidence 부여), 그룹화 분석. 입력: merge_findings.py 출력. 기존 필드 변경 금지 |
| D | `{SKILL_DIR}/rules/phase-d-report.md` | 핵심 규칙 11개, 리포트 형식, groups 렌더링, 품질 금지 패턴, Exit Criteria |

---

## Orchestrator 실행 흐름

### 감사 모드

```
턴 1: 사용자 요청 파싱
      -> REPO, TARGET, DATE 변수 설정
      -> RAW_DIR = "report/security-review/${REPO}/raw"
      -> REPORT_DIR = "report/security-review/${REPO}"
      -> SKILL_DIR = 이 SKILL.md의 디렉토리 절대 경로

턴 2: Phase A — Orchestrator 직접 실행
      -> rules/phase-a-scan.md를 Read
      -> Shell 도구로 도구 설치 확인 + 3개 도구 스캔 + 개수 집계
      -> counts.txt 읽기 -> 개수 기록

턴 3: Phase B — 3 라운드 병렬 dispatch (Ralph Loop)
      -> 3개 generalPurpose sub-agent 동시 dispatch (Task 도구 3회 병렬 호출)
      -> 라운드 1: auth_logic, business_logic, error_handling
      -> 라운드 2: data_flow, concurrency, crypto_misuse
      -> 라운드 3: api_design, llm_ai, pii_handling
      -> 각 라운드 출력: {repo}-ai-r{N}-{date}.json
      -> 모든 라운드 완료 대기

턴 4: Phase B 결과 병합 (Orchestrator 직접)
      -> Shell로 3개 라운드 JSON 읽기 -> findings 병합 -> id 재번호
      -> {repo}-ai-analysis-{date}.json Write
      -> ai_count 기록

턴 5: Merge — Orchestrator 직접 실행
      -> Shell로 merge_findings.py 실행
      -> python3 {SKILL_DIR}/scripts/merge_findings.py \
           --repo-name {REPO} --raw-dir {RAW_DIR} --date {DATE} \
           --output {RAW_DIR}/{REPO}-unified-findings-{DATE}.json
      -> unified-findings.json 존재 + findings 개수 확인 -> merge_count 기록

턴 6: Phase C dispatch — FP Triage (generalPurpose sub-agent)
      -> prompt: "Read 도구로 {SKILL_DIR}/rules/phase-c-triage.md를 읽고 따르세요"
      -> 변수 전달: unified-findings.json 경로, merge_count, TARGET
      -> 완료 대기 -> findings 개수 불변 검증

턴 6.5 (GHA only): Classify — notion_classify.py (Orchestrator 직접 실행)
      -> GHA: notion_classify.py 실행 → classified-findings.json 생성
      -> Local: 이 단계 건너뜀 (Notion 토큰 없음)
      -> Phase D 입력 변수:
         - GHA: classified-findings.json (_status 포함)
         - Local: unified-findings.json (_status 없음)

턴 7: Phase D dispatch — Report Writer (generalPurpose sub-agent)
      -> prompt: "Read 도구로 {SKILL_DIR}/rules/phase-d-report.md를 읽고 따르세요"
      -> 변수 전달: RAW_DIR, REPORT_DIR, REPO, DATE, TARGET, counts (개수들)
      -> Findings JSON 경로: GHA=classified / Local=unified
      -> 완료 대기 -> 리포트 MD 존재 확인

턴 8: Exit Criteria 검증
      -> 리포트 읽기 -> 검증 결과 섹션 확인
      -> 실패 시 Phase D 재dispatch (최대 2회)
      -> 성공 시 사용자에게 완료 보고
```

### 간단 모드

> 간단 모드는 변경 파일만 스캔하므로 context가 작다.
> Orchestrator가 Phase A(도구 스캔) + Phase B(AI 분석) + Phase D(리포트)를 직접 수행한다.
> rules/*.md를 직접 Read하여 규칙을 따른다.

```
턴 1: 사용자 요청 파싱 -> 간단 모드 확인
턴 2: Phase A — Orchestrator가 rules/phase-a-scan.md Read -> Shell로 변경 파일만 스캔
턴 3: Orchestrator가 rules/phase-b-analysis.md + rules/phase-d-report.md를 Read
      -> 직접 AI 분석 + 리포트 작성 (채팅창 출력)
      -> 간단 모드는 Phase C (triage) 생략 — context가 작으므로 직접 FP 판단
```

---

## Phase Dispatch 상세

### Phase A — Orchestrator 직접 실행

> Phase A는 sub-agent로 dispatch하지 않는다.
> Orchestrator가 직접 `{SKILL_DIR}/rules/phase-a-scan.md`를 Read 도구로 읽고,
> 해당 파일의 절차에 따라 Shell 도구로 스캔 명령을 실행한다.
>
> **감사 모드**: "감사 모드 스캔 절차" 섹션 실행
> **간단 모드**: "간단 모드 스캔 절차" 섹션 실행
>
> Shell 출력은 파일로 리다이렉트되므로 context 오염이 적다.

### Phase B Pre-scan (Orchestrator 직접 실행, dispatch 전)

> Sub-agent dispatch **전에** Orchestrator가 라운드별 핵심 패턴의 존재 여부를 사전 확인합니다.
> 결과를 각 라운드 sub-agent 프롬프트에 포함하여, sub-agent가 근거 없이 skip하는 것을 방지합니다.

```
Grep 도구로 라운드별 핵심 패턴 검색 (대상: {target_path}, type: go,py,ts,js):

[R1 핵심 패턴 — 인증/비즈니스로직/에러]
  1. Grep: "auth|middleware|jwt|session"
  2. Grep: "admin|internal|debug"
  3. Grep: "SKIP_AUTH|BYPASS_AUTH|AUTH_ENABLED"

[R2 핵심 패턴 — 데이터흐름/동시성/암호화]
  4. Grep: "eval|exec|pickle|yaml\\.load"
  5. Grep: "redirect|postMessage|deserialize"
  6. Grep: "crypto|cipher|hash|bcrypt|argon"

[R3 핵심 패턴 — API설계/LLM/PII]
  7. Grep: "chat|prompt|inference|completion"
  8. Grep: "mcp|tool_call|function_call"
  9. Grep: "cors|BodyLimit|rate.limit|RateLimit"

각 라운드별로 prescan 결과를 기록:
  - r{N}_prescan = { exists: true/false, matched_files: [...] }
```

### Phase B Dispatch (라운드별 × 3 병렬)

3개 라운드를 **동시에** dispatch. 각 라운드 프롬프트에 해당 라운드의 pre-scan 결과를 포함:

```
Task 도구 호출 (라운드 N = 1, 2, 3):
  subagent_type: "generalPurpose"
  description: "Phase B 라운드 {N}: {카테고리 요약}"
  prompt: |
    당신은 보안 코드 분석 전문가입니다.

    **[필수 첫 단계]** Read 도구로 아래 파일을 읽으세요:
    -> {SKILL_DIR}/rules/phase-b-analysis.md

    **이번은 "라운드 {N}"입니다.**
    "라운드 {N}" 섹션의 카테고리, 분석 절차, Grep 패턴만 실행하세요.
    "공통 사항"(특히 "not_applicable 판정 규칙")과 "공통: 출력 JSON 스키마"도 따르세요.
    다른 라운드 섹션은 무시하세요 — 다른 sub-agent가 담당합니다.

    **[중요] Orchestrator Pre-scan 결과**:
    {r{N}_prescan 결과를 여기에 삽입}
    → 매치 파일이 존재하는 카테고리는 반드시 전체 분석하세요.
      해당 파일들을 Read하여 위협 질문에 답하세요.
    → not_applicable 판정 시 evidence 필드에 Grep/Read 결과 근거를 기록하세요.
      evidence 없는 not_applicable은 무효입니다.

    대상 리포지토리: {target_path}
    출력 파일: {raw_dir}/{repo}-ai-r{N}-{date}.json

    결과 반환: summary 객체를 반환하세요.
```

라운드별 description:
- 라운드 1: `"Phase B R1: 인증/비즈니스로직/에러처리"`
- 라운드 2: `"Phase B R2: 데이터흐름/동시성/암호화"`
- 라운드 3: `"Phase B R3: API설계/LLM/PII"`

### Phase B Checklist 검증 (Orchestrator 직접 실행, 병합 전)

> 모든 라운드 완료 후, 병합 전에 Orchestrator가 각 라운드의 checklist를 검증합니다.
> **pre-scan에서 코드가 확인된 카테고리의 항목이 전부 not_applicable이면 해당 라운드를 재실행**합니다.

```
각 라운드(R1, R2, R3)에 대해:
  1. 해당 라운드 결과 JSON을 Read
  2. checklist에서 카테고리별로 not_applicable 비율 확인
  3. 검증 조건:
     - pre-scan에서 매치 파일이 있는 카테고리인데,
       해당 카테고리의 checklist가 **전부 not_applicable**이면:
       → 경고 출력 후 해당 라운드를 동일 프롬프트로 재dispatch (최대 1회 재시도)
     - evidence 필드가 비어있는 not_applicable 항목이 있으면:
       → 경고 출력 (병합은 진행하되 리포트에 "⚠️ 일부 항목 evidence 누락" 메모)
```

### Phase B 결과 병합

모든 라운드 완료 + 검증 통과 후 Orchestrator가 직접 수행:

```
Shell 도구로 Python 실행:
  python3 -c "
  import json, glob
  files = sorted(glob.glob('{raw_dir}/{repo}-ai-r*-{date}.json'))
  merged = {'metadata':{}, 'findings':[], 'summary':{'critical':0,'high':0,'medium':0,'low':0,'info':0}}
  for fp in files:
      d = json.load(open(fp))
      merged['metadata'] = d.get('metadata', {})
      merged['findings'].extend(d.get('findings', []))
      for k in merged['summary']:
          merged['summary'][k] += d.get('summary',{}).get(k,0)
  for i,f in enumerate(merged['findings'],1):
      f['id'] = f'AI-{i:03d}'
  merged['metadata'].pop('round', None)
  json.dump(merged, open('{raw_dir}/{repo}-ai-analysis-{date}.json','w'), indent=2, ensure_ascii=False)
  print(f'ai_total={len(merged[\"findings\"])}')
  "
```

병합 스크립트 출력의 `ai_total=N` 값을 counts.txt에 추가하고 Phase C에 전달.

### Merge — Orchestrator 직접 실행

> merge_findings.py를 Shell로 실행하여 unified-findings.json을 생성합니다.
> 결정적 스크립트이므로 sub-agent가 아닌 Orchestrator가 직접 실행합니다.

```
Shell 도구로 실행:
  python3 {SKILL_DIR}/scripts/merge_findings.py \
    --repo-name "{repo}" \
    --raw-dir "{raw_dir}" \
    --date "{date}" \
    --output "{raw_dir}/{repo}-unified-findings-{date}.json"

출력 확인:
  - unified-findings.json 파일 존재 확인
  - findings 배열 길이 → merge_count로 기록
  - merge_count를 Phase C dispatch에 전달
```

### Phase C Dispatch — FP Triage

```
Task 도구 호출:
  subagent_type: "generalPurpose"
  description: "Phase C: FP Triage"
  prompt: |
    당신은 보안 취약점 분석 전문가입니다.

    **[필수 첫 단계]** 아래 파일을 Read 도구로 읽으세요.
    이 파일의 **모든 규칙을 엄격히** 따라 FP 판단을 수행해야 합니다:
    -> {SKILL_DIR}/rules/phase-c-triage.md

    **입력 파일 (디스크에서 읽기)**:
    - Unified Findings: {raw_dir}/{repo}-unified-findings-{date}.json
      (merge_findings.py가 생성한 결정적 병합 결과, {merge_count}건)
    - 대상 소스: {target_path} (Semgrep Critical/High 코드 맥락 FP 판단용)

    **작업**:
    1. unified-findings.json을 읽으세요
    2. 각 finding에 confidence/confidence_note를 추가하세요
    3. groups 배열을 생성하세요
    4. 수정된 unified-findings.json을 같은 경로에 저장하세요

    **절대 금지**:
    - findings 배열에서 항목을 추가/삭제/재정렬하지 마세요
    - hash, severity, tool, rule_id, file, line 등 기존 필드를 변경하지 마세요
    - 저장 후 findings 배열 길이는 반드시 {merge_count}건이어야 합니다

    **출력 파일**: {raw_dir}/{repo}-unified-findings-{date}.json (같은 경로에 덮어쓰기)

    Write 도구로 저장하세요.
    **결과 반환**: "unified-findings.json 저장 완료" + confidence 요약 + groups 수를 반환하세요.
```

### Phase D Dispatch — Report Writer

```
Task 도구 호출:
  subagent_type: "generalPurpose"
  description: "Phase D: 보안 리뷰 리포트 작성"
  prompt: |
    당신은 보안 리포트 전문가입니다.

    **[필수 첫 단계]** 아래 파일을 Read 도구로 읽으세요.
    이 파일의 **모든 규칙을 엄격히** 따라 리포트를 작성해야 합니다:
    -> {SKILL_DIR}/rules/phase-d-report.md

    파일을 읽은 후, 아래 정보를 사용하여 리포트를 작성하세요:

    **입력 파일 (디스크에서 읽기)**:
    - Findings JSON: {findings_json_path}
      (GHA: classified-findings.json / Local: unified-findings.json)
      (각 finding에 _status 필드가 포함되어 있을 수 있음: New/Recurring/FP/Accepted-Risk)
    - 개수: {raw_dir}/counts.txt
    - 대상 소스: {target_path} (수정 코드 작성 시 소스 참조)

    **출력 파일**:
    - {report_dir}/{repo}-security-review-{date}.md

    **Raw 개수 (counts.txt 기준)**:
    - Semgrep: {semgrep_count}
    - Gitleaks: {gitleaks_count}
    - Trivy CVE: {trivy_cve_count}
    - Trivy Misconfig: {trivy_misconfig_count}
    - AI 분석: {ai_count}
    - raw 합계: {total_count}

    Write 도구로 리포트를 저장하세요.
    **결과 반환**: "리포트 저장 완료" + 스캔 요약 + 심각도 요약을 반환하세요.
```

---

## Orchestrator 검증 (Exit Criteria)

### 감사 모드

> **아래 조건 모두 충족 시 완료**

| 조건 | 검증 |
|------|------|
| raw JSON 3개 + AI 라운드 JSON 3개 + 병합 JSON 1개 저장 | `ls -la` 로 파일 확인 |
| counts.txt 저장 (ai_total 포함) | 파일 존재 + ai_total 라인 확인 |
| unified-findings.json 저장 (merge_findings.py) | 파일 존재 + findings 개수 확인 |
| Phase C 후 findings 개수 불변 | merge 출력 개수 == Phase C 후 개수 |
| 리포트 MD 저장 | 파일 존재 확인 |
| 핵심 규칙 준수 | 아래 검증 절차 수행 |

### Orchestrator 검증 절차

Phase D 완료 후 Orchestrator가 수행:

1. unified-findings.json 존재 확인 (merge_findings.py 생성 + Phase C enrichment)
2. 리포트 파일 존재 확인
3. 리포트 읽기 → "검증 결과" 섹션 존재 확인
4. 검증 결과 테이블에서 모든 도구 OK 확인
5. 테이블 개수 합계 = counts.txt raw 합계 대조
6. **테이블 ↔ 상세 섹션 전수 매칭 검증**:
   - "발견 취약점 목록" 테이블에서 모든 `#N` 또는 `#N-M` 번호 범위를 파싱
   - "상세 설명" 섹션에서 모든 `### ... [#N]` 또는 `### ... [#N-M]` 헤딩을 파싱
   - 테이블의 각 번호 범위가 상세 섹션에 대응하는 헤딩이 있는지 확인
   - 누락된 범위가 1개라도 있으면 **실패** → 누락 목록을 Phase D 재dispatch 시 전달
7. 실패 시 Phase D 재dispatch (최대 2회). 재dispatch 시 누락된 범위 목록을 prompt에 포함

---

## Context 보호 전략

| 전략 | 설명 |
|------|------|
| **Ralph Loop 라운드 분할** | Phase B를 3 라운드로 분할 → 각 sub-agent가 3개 카테고리만 담당 → context 고갈 방지 |
| **Phase Isolation** | Phase B/C/D는 독립 sub-agent. Phase A는 Shell 출력을 파일 저장하여 context 최소화 |
| **Rules File Read** | 규칙 본문은 sub-agent가 자기 Phase의 rules 파일을 직접 Read. Orchestrator context에 규칙 미포함 |
| **디스크 기반 전달** | Phase 간 데이터는 JSON 파일로 전달. Orchestrator context에는 경로/개수만 유지 |
| **Orchestrator 최소 상태** | 경로, 개수, Phase/라운드 완료 상태만 보유. raw JSON/규칙 내용을 context에 넣지 않음 |
| **단일 규칙 소스** | 규칙은 rules/*.md에 한 번만 정의. 라운드 섹션으로 분리하되 파일은 1개 유지 |
