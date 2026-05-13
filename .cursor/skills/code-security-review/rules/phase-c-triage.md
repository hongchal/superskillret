# Phase C: FP Triage (confidence + groups)

> Phase C sub-agent (generalPurpose)가 읽는 규칙 파일.
> `merge_findings.py`가 생성한 `unified-findings.json`을 읽고, **FP 판단 → confidence 부여 → 그룹화 분석** 후 같은 파일에 덮어쓰기.
> **정규화, 심각도 매핑(Semgrep Impact×Likelihood 매트릭스 + CWE 최소 보장), 중복 병합, hash 생성은 merge_findings.py가 이미 완료.** Phase C는 판단만 수행.

---

## 역할

```
입력                                    출력
┌──────────────────────┐                ┌──────────────────────┐
│ unified-findings.json│                │ unified-findings.json│
│ (merge_findings.py   │ ── Phase C ──→ │ (+ confidence        │
│  출력, hash/개수 확정)│   (FP 판단     │  + confidence_note   │
│                      │    + groups)   │  + groups 배열)      │
│ 대상 소스 (코드 참조)│                │                      │
└──────────────────────┘                └──────────────────────┘
```

---

## 절대 금지 (불변 제약)

> merge_findings.py가 확정한 데이터는 Phase C에서 변경 불가.

| 금지 사항 | 이유 |
|-----------|------|
| findings 배열에서 항목 추가/삭제 | 개수 정합성 보장 (counts.txt와 일치) |
| findings 배열 재정렬 | id 번호와 순서가 고정 |
| `hash` 필드 변경 | Notion DB 매칭 키 |
| `id`, `severity`, `tool`, `rule_id`, `file`, `line` 변경 | 결정적 출력 보존 |
| `title`, `code_snippet`, `description`, `remediation` 변경 | 원본 데이터 보존 |
| `cwe`, `owasp` 변경 | 원본 데이터 보존 |
| `metadata` 전체 변경 (`repository`, `scan_date`, `total_findings`, `tool_counts`) | 검증 기준 |

---

## 처리 순서

| 단계 | 작업 | 상세 |
|------|------|------|
| 1 | **unified-findings.json 읽기** | merge_findings.py 출력 파일을 Read 도구로 읽기 |
| 2 | **FP 판단** | 도구별 전략 적용 (아래 참조). Critical/High Semgrep은 코드 Read |
| 3 | **confidence 부여** | 각 finding에 `confidence` + `confidence_note` 추가 |
| 4 | **그룹화 분석** | 5개 조건 비교 → `groups` 배열 생성 (아래 참조) |
| 5 | **출력** | unified-findings.json을 같은 경로에 Write (덮어쓰기) |

---

## FP 판단 기준

### 도구별 판단 전략

| 도구 | 전략 | 참조 정보 |
|------|------|-----------|
| **Gitleaks** | Match 내용 + File 경로로 판단 | `rule_id`, `code_snippet`, `file` |
| **Trivy** | Target 경로 + rule_id로 판단 | `rule_id`, `file`, `severity` |
| **Semgrep (Critical/High)** | **해당 파일:라인 코드를 반드시 Read**하여 맥락 판단. 코드 Read 없이 confidence=high 기본 설정 금지 | `rule_id`, `file`, `line`, `code_snippet` + **실제 코드 Read 필수** |
| **Semgrep (Medium/Low)** | `code_snippet` + `rule_id` 기반 판단. 테스트/생성 코드 여부 확인 | `rule_id`, `file`, `code_snippet` |
| **AI-Only** | AI가 발견한 취약점의 `code_snippet` + `file`을 확인하여 판단 | `rule_id`, `file`, `code_snippet` |

### Semgrep Critical/High 코드 맥락 FP 판단 (필수)

> **Critical/High이라도 FP 가능성은 존재합니다.** 심각도와 FP 가능성은 독립적인 판단입니다.
> Semgrep Critical/High finding에 대해 **해당 파일의 코드를 반드시 Read**하여 맥락 기반으로 confidence를 판단합니다.
> **코드를 Read하지 않고 confidence=high로 기본 설정하는 것은 금지**입니다.

판단 시 고려 사항:
- 매칭된 코드가 실제 사용자 입력을 받는지, 아니면 내부 데이터만 처리하는지
- 프레임워크가 이미 방어를 제공하는지 (예: ORM의 자동 이스케이프)
- 테스트/모킹/예시 코드인지
- MCP/JSON-RPC 등 특수 프로토콜 핸들러에서의 정상 패턴인지
- **GitHub Actions**: `${{ inputs.xxx }}`, `${{ github.event.xxx }}`는 호출자가 제어 가능 → 신뢰 경계에 따라 medium/low 가능
- **generated 파일**: 자동 생성된 매니페스트/코드는 소스가 아닌 빌드 산출물 → medium 가능
- **dev/스크립트 경로**: 프로덕션에 배포되지 않는 도구/스크립트 → medium/low 가능

**판단 결과 예시**:

| 케이스 | confidence | confidence_note |
|--------|-----------|-----------------|
| fmt.Sprintf로 SQL 쿼리 직접 구성, 사용자 입력 도달 확인 | high | null |
| shell injection이지만 inputs가 workflow_dispatch 제한 | medium | `워크플로우 입력; 호출 경계 신뢰 시 영향 축소` |
| generated YAML의 securityContext 미설정 | medium | `generated 매니페스트; 배포 전 템플릿 재검토` |
| 테스트 파일의 pickle.loads | low | `테스트 코드; 프로덕션 경로 아님` |

### confidence → FP 가능성 매핑

| confidence | FP 가능성 | confidence_note |
|-----------|-----------|-----------------|
| high | `매우 낮음` | `null` |
| medium | `낮음 — {사유}` | 구체적 사유 필수 |
| low | `높음 — {사유}` | 구체적 사유 필수 |

### FP 판단 시 공통 고려 사항

| 도구 | FP 가능성 높은 경우 |
|------|---------------------|
| **Gitleaks** | 템플릿/예시 파일, hash 값, placeholder, test 파일, .example 확장자 |
| **Trivy** | dev 환경 전용, upstream/vendor 경로, 레지스트리 미사용(오프라인), generated YAML |
| **Semgrep** | 테스트 코드, 프레임워크 보호, MCP/RPC 핸들러, 내부 데이터만 처리 |
| **AI-Only** | AI가 추론한 취약점 중 코드 맥락 불충분, 의도적 설계, 테스트/문서 코드 |

---

## 그룹화 분석 규칙

> findings를 대상으로, **기계적 조건 비교**만으로 그룹을 식별합니다.
> AI 추론이 아닌 필드 값 일치 비교입니다.

### 그룹화 조건 (5개 **전부** 충족 시)

| # | 조건 | 비교 대상 필드 |
|---|------|---------------|
| 1 | 동일 도구 | `tool` |
| 2 | 동일/유사 Rule ID | `rule_id` 일치 또는 동일 접두사 (예: `KSV-0xxx` 시리즈) |
| 3 | 동일 CWE | `cwe` |
| 4 | 동일 심각도 | `severity` |
| 5 | 5건 이상 | 조건 1~4 일치 건수 ≥ 5 |

### 그룹 생성 절차

```
1. findings를 (tool, rule_id, cwe, severity) 튜플로 그룹핑
2. 건수 ≥ 5인 그룹만 채택
3. 각 그룹에 group_id 부여 (G1, G2, ...)
4. label = 대표 title (첫 번째 finding의 title)
5. fix_method = 대표 remediation (첫 번째 finding의 remediation)
6. finding_ids = 해당 그룹에 속하는 finding id 목록 (오름차순)
```

### 그룹화 불가 케이스

- Trivy misconfig + Trivy CVE 혼합 → `tool` 동일해도 `cwe`가 다르면 불가
- 같은 CWE + 같은 도구지만 `severity` 다름 → 불가
- 4건 이하 → 불가 (개별 유지)

> **findings 배열은 변경하지 않습니다.** 그룹에 속한 finding도 findings 배열에 그대로 남습니다.

---

## 제외 금지 원칙

> FP 가능성이 높아도 unified-findings.json에서 **제외하지 않습니다.**
> `confidence: "low"`, `confidence_note: "사유"` 를 기록하고 Phase D가 리포트에 포함합니다.

---

## Phase C가 추가하는 필드

> 아래 필드만 각 finding에 추가/수정합니다. 다른 필드는 절대 변경 금지.

| 필드 | 타입 | 설명 |
|------|------|------|
| `confidence` | `"high"` \| `"medium"` \| `"low"` | FP 가능성 판단 결과 |
| `confidence_note` | `string` \| `null` | medium/low일 때 구체적 사유 (high일 때 null) |

> AI-Only finding(`tool: "ai"`)도 다른 도구와 동일하게 Phase C에서 confidence를 부여합니다.
> `code_snippet`과 `file`을 참고하여 FP 가능성을 판단하세요.

---

## 출력 검증

| 항목 | 검증 |
|------|------|
| `findings` 배열 길이 | merge_findings.py 출력과 동일 (변경 금지) |
| `findings` 내 `id` | 1부터 연속 번호, 누락 없음 (변경 금지) |
| 모든 finding에 `hash` | 기존 값 유지 (변경 금지) |
| 모든 finding에 `confidence` | `high`/`medium`/`low` 중 하나 |
| `confidence`가 medium/low이면 `confidence_note` | `null` 아닌 문자열 |
| `metadata` | 모든 하위 필드 (`repository`, `scan_date`, `total_findings`, `tool_counts`) 변경 금지 |
| `groups` 내 `finding_ids` | 모두 `findings` 배열에 존재하는 id |
| `groups` 내 각 그룹 | `finding_ids` 길이 ≥ 5 |
| `groups` 간 `finding_ids` | 중복 없음 (하나의 finding은 하나의 그룹에만) |

> 검증 실패 시 수정 후 다시 저장.

결과 반환: `"unified-findings.json 저장 완료"` + confidence 요약 + groups 수 반환.
