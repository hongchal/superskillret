# Phase D: 보안 리뷰 리포트 작성 규칙

> Phase D sub-agent (generalPurpose)가 읽는 규칙 파일.
> 프롬프트에서 지정된 Findings JSON을 읽고 **리포트 형식에 맞춰** MD 파일로 작성.
> 이 파일의 **모든 규칙을 엄격히** 따라 리포트를 작성하세요.

---

## 입력

| 파일 | 설명 |
|------|------|
| Findings JSON (프롬프트에서 경로 지정) | merge → Phase C(FP 판단+그룹화) → 선택적으로 Notion 분류(_status) 결과 |
| `counts.txt` | Phase A raw 개수 |
| 대상 소스 경로 | 수정 코드 작성 시 소스 참조 |

> **병합은 merge_findings.py, FP 판단/그룹화는 Phase C에서 완료됨.** Phase D는 Findings JSON을 그대로 사용합니다.
> - `groups` 배열: Phase C가 산출한 그룹 정보. Phase D는 이 배열을 따라 `### [#N-M]` 형식으로 렌더링.
> - `confidence` / `confidence_note`: 리포트의 `FP 가능성` 컬럼으로 변환하여 표기.
> - `findings` 배열: 개별 항목. `groups`에 속하지 않는 finding은 개별 상세 섹션으로 작성.
> - `_status` 필드 (선택적): Notion DB 분류 결과. 존재하면 아래 **_status 조건부 렌더링** 규칙을 적용.

---

## _status 조건부 렌더링

> Findings JSON의 **첫 번째 finding**에 `_status` 필드가 존재하는지 확인합니다.
> 존재하면 아래 규칙을 적용합니다. 존재하지 않으면 이 섹션 전체를 건너뜁니다.
> (Local 스킬 실행 시 `_status`가 없을 수 있으며, 이 경우 기존 리포트 형식 그대로 출력됩니다.)

### 적용 조건 판별

```
if findings[0]._status exists:
    → 아래 legend + 뱃지 + FP/Accepted-Risk 접힘 규칙 적용
else:
    → 이 섹션 전체 건너뜀 (기존 리포트 형식 그대로)
```

### Legend (리포트 최상단)

> `_status`가 존재하면 **리포트 제목 바로 아래**에 다음 legend를 삽입합니다.

```markdown
> **분류 범례**: 🆕 New | 🔄 Recurring | 🔕 FP | 🛡️ Accepted-Risk
```

### 뱃지 접두사 (테이블 + 상세 섹션)

> 테이블과 상세 섹션의 `#` 번호 앞에 상태 뱃지를 접두사로 붙입니다.

| _status | 뱃지 | 테이블 예시 | 상세 예시 |
|---------|------|------------|----------|
| `New` | 🆕 | `🆕 #1` | `### Critical 🆕 [#1] SQL Injection` |
| `Recurring` | 🔄 | `🔄 #2-5` | `### High 🔄 [#2-5] Hardcoded Secrets (4개)` |
| `FP` | 🔕 | `🔕 #6` | (FP 접힘 섹션으로 이동 — 아래 참조) |
| `Accepted-Risk` | 🛡️ | `🛡️ #7` | (FP 접힘 섹션으로 이동 — 아래 참조) |

> 그룹(`#N-M`) 내 모든 항목의 `_status`가 동일하면 그룹 뱃지 1개만 표시.
> 그룹 내 `_status`가 혼합되면 대표 상태(New 우선)를 사용하고 `(상태 혼합)` 주석.

### FP / Accepted-Risk 접힘 섹션

> `_status`가 `FP` 또는 `Accepted-Risk`인 항목은 **메인 취약점 목록 테이블과 상세 섹션에서 제외**하고,
> 리포트 마지막 **검증 결과 섹션 바로 앞**에 접힘(`<details>`) 섹션으로 이동합니다.

```markdown
<details>
<summary>🔕 FP / Accepted-Risk 항목 (N건)</summary>

| # | 심각도 | 취약점 | 파일:라인 | 출처 | 상태 | 사유 |
|---|--------|--------|----------|------|------|------|
| 🔕 #6 | Medium | XSS in test file | test/xss.js:10 | [Semgrep] | FP | 테스트 코드 |
| 🛡️ #7 | Low | Missing HSTS | deploy.yaml:5 | [Trivy] | Accepted-Risk | 내부 전용 |

</details>
```

> **주의**: FP/Accepted-Risk 항목도 `#` 번호는 원래 순서 그대로 유지합니다.
>
> **개수 정합 (핵심 규칙 #4) 보완**:
> - FP/Accepted-Risk 항목은 메인 테이블에서 제외되지만, **검증 결과 테이블의 도구별 raw 개수에는 포함**됩니다 (제외하지 않음).
> - 검증 결과 테이블의 "리포트 개수"는 **메인 테이블 개수 합 + FP/Accepted-Risk 접힘 섹션 항목 수**로 산출합니다.
> - 즉: `메인 테이블 개수 합 + FP/Accepted-Risk N건 = raw 합계` 가 성립해야 합니다.
> - 검증 결과 테이블에 `FP/Accepted-Risk: N건 (접힘 섹션)` 행을 추가하여 정합을 명시합니다.

---

## 핵심 규칙 (절대 준수 — 11개)

> **위반 시 리포트 무효.**

| # | 규칙 | 설명 |
|---|------|------|
| 1 | **테이블 <-> 상세 설명 1:1** | 테이블의 각 항목에 대응하는 상세 설명 섹션 필수. `groups` 배열의 그룹은 `### [#N-M]` 형식으로, 그룹에 속하지 않는 개별 항목은 `### [#N]` 형식으로 작성 |
| 2 | **Critical/High → 수정 코드** | 취약 코드 + 수정 코드 필수 포함. 소스 코드 Read하여 작성 |
| 3 | **Medium/Low → 수정 권고** | 최소 수정 권고 포함 |
| 4 | **개수 정합 + 순차 정렬** | 테이블 **"개수" 컬럼 합계 = `metadata.tool_counts` 각 도구 값의 합 (raw 합계)**. 그룹 `#N-M`은 M-N+1개로 계산. **# 최대값 = `metadata.total_findings` (고유 항목 수)**. **테이블 행은 # 번호 오름차순 정렬** (그룹은 시작 번호 기준). 심각도/크기 등으로 재정렬 금지 |
| 5 | **제외 금지** | FP 가능성이 높아도 리포트에서 제외 금지. `FP 가능성` 컬럼에 사유 표기 |
| 6 | **심각도 고정** | `unified-findings.json`의 severity 임의 변경 금지 |
| 7 | **파일:라인 명시** | **Critical~Medium**: 그룹화 시 상세 섹션에 **전체 파일:라인 테이블 필수**. **Low/Info**: 동일 Rule ID 10건 이상은 **대표 3건 + raw 참조** 축약 허용. 추상적 표현 금지 |
| 8 | **검증 결과 섹션** | 리포트 마지막에 **도구별 raw vs 리포트 개수 비교 테이블 필수**. 모든 도구 OK 아니면 제출 불가 |
| 9 | **FP 가능성 표기** | `confidence` → `FP 가능성` 변환하여 테이블에 표기. **모든 상세 섹션에 `**FP 가능성**:` 블록 필수** (`매우 낮음` 포함, 생략 금지). **그룹 섹션은 FP 블록 1줄만 출력** (finding 수만큼 반복 금지 — 그룹 FP 렌더링 규칙 참조) |
| 10 | **groups 준수** | `unified-findings.json`의 `groups` 배열 그대로 렌더링. 임의 그룹 생성/해체 금지 |
| 11 | **품질 규칙 (금지 패턴 + 구조적 검증)** | ① 도구 원문 영문 복붙 금지 ② 모든 설명·권고는 한국어 자연어로 재작성 ③ 파일/기술 맥락 불일치 금지 ④ **보일러플레이트 구조적 검증**: 서로 다른 Rule ID 섹션에 동일 설명/권고 텍스트가 반복되면 위반. 각 설명에 Rule ID 고유의 기술적 메커니즘 포함 필수. 각 권고에 구체적 설정값/함수명/변경 내용 포함 필수 (아래 품질 규칙 참조) |

---

## confidence → FP 가능성 변환

> `unified-findings.json`의 `confidence` / `confidence_note`를 리포트 표기로 변환:

| confidence | confidence_note | 리포트 `FP 가능성` |
|-----------|-----------------|-------------------|
| high | null | `매우 낮음` |
| medium | 사유 문자열 | `낮음 — {사유}` |
| low | 사유 문자열 | `높음 — {사유}` |

---

## 그룹 렌더링 규칙

> **그룹화 분석은 Phase C에서 완료.** Phase D는 `groups` 배열을 **그대로 따라** 렌더링합니다.
> Phase D가 자체적으로 그룹을 만들거나, Phase C의 그룹을 무시하고 개별로 쪼개는 것은 금지입니다.

### 렌더링 절차

```
1. unified-findings.json의 groups 배열을 읽는다
2. 각 group의 finding_ids로 해당 findings를 수집한다
3. 테이블에 #N-M 범위로 1행 표시, 상세에 ### [#N-M] 섹션 작성
4. groups에 속하지 않는 findings는 개별 #N으로 작성
```

**그룹 렌더링 시 필수 포함 내용**:

| 심각도 | 테이블 표기 | 상세 설명 필수 포함 |
|--------|------------|-------------------|
| Critical / High | `#N-M` | `### [#N-M]` 섹션 + 취약점 설명 + **대표 파일 수정 코드** + **영향받는 전체 파일:라인 테이블** |
| Medium | `#N-M` | `### [#N-M]` 섹션 + 취약점 설명 + **수정 권고** + **영향받는 전체 파일:라인 테이블** |
| Low / Info | `#N-M` | 아래 **Low/Info 대량 항목 축약 규칙** 참조 |

### 파일:라인 테이블 형식 (Critical / High / Medium)

> **Critical / High / Medium**: 전체 파일:라인 나열 필수. 생략 금지.

```markdown
**영향받는 파일**:

| # | 파일 | 라인 |
|---|------|-----|
| 18 | app/workload/templates/cronjob_template.yaml | 48 |
| 19 | app/workload/templates/daemonset_template.yaml | 52 |
| 20 | app/workload/templates/deployment_template.yaml | 55 |
```

> `...` 또는 `(이하 생략)` 사용 금지.
> `*(상세 참조)*` 등으로 대체 금지.

### Low/Info 대량 항목 축약 규칙

> 동일 Rule ID 그룹이 **10건 이상**인 Low/Info 항목에 적용.
> 9건 이하는 전체 나열.

**축약 형식**:
- **대표 파일 3건**만 테이블에 나열
- **총 건수** 명시
- **raw JSON 파일 경로 + 전체 조회 명령어** 필수 기재

```markdown
### Low [#87-112] KSV-0039 limit range usage (26건) - [Trivy]
**Rule ID**: KSV-0039 | **심각도**: LOW

**대표 파일** (3/26건):

| # | 파일 | 라인 |
|---|------|-----|
| 87 | app/cluster/templates/k8s-shell/kubectl-shell.yaml | 27 |
| 88 | app/workload/templates/cronjob_template.yaml | 12 |
| 89 | app/workload/templates/deployment_template.yaml | 12 |

> 전체 26건 조회:
> `cat raw/{repo}-trivy-{date}.json | jq '[.Results[].Misconfigurations[] | select(.ID == "KSV-0039")]'`

**수정 권고**: LimitRange 리소스가 네임스페이스에 정의되어 있는지 확인.
```

**도구별 조회 명령어 템플릿**:

| 도구 | 명령어 |
|------|--------|
| Trivy (Rule ID) | `cat raw/{repo}-trivy-{date}.json \| jq '[.Results[].Misconfigurations[] \| select(.ID == "{RULE_ID}")]'` |
| Trivy (Severity) | `cat raw/{repo}-trivy-{date}.json \| jq '[.Results[].Misconfigurations[] \| select(.Severity == "{SEVERITY}")]'` |
| Semgrep (Rule ID) | `cat raw/{repo}-semgrep-{date}.json \| jq '[.results[] \| select(.check_id \| contains("{RULE_ID}"))]'` |

**축약 시 필수 요소**:

| 요소 | 필수 | 설명 |
|------|:----:|------|
| 대표 파일 3건 | O | 첫 3건을 테이블로 나열 |
| 총 건수 | O | `(3/26건)` 형태로 명시 |
| 전체 조회 명령어 | O | 복사-붙여넣기로 바로 실행 가능한 `cat ... \| jq ...` 명령어 |
| 수정 권고 | O | 최소 한 줄 |

> 10건 미만이면 축약 금지 → 전체 나열.
> Critical / High / Medium은 건수와 무관하게 축약 금지 → 전체 나열.

**FP 가능성 블록 (모든 항목 필수)**:

> **모든 상세 섹션**에 FP 가능성 블록을 포함합니다. `매우 낮음`이어도 생략하지 않습니다.

```markdown
**FP 가능성**: 매우 낮음.
**FP 가능성**: 낮음 — {사유}. 수동 검토 권장.
**FP 가능성**: 높음 — {사유}. 수동 검토 권장.
```

예:
- `**FP 가능성**: 매우 낮음.`
- `**FP 가능성**: 낮음 — workflow_dispatch 입력; 호출 경계 신뢰 시 영향 축소. 수동 검토 권장.`
- `**FP 가능성**: 높음 — Gitleaks RuleID=generic-api-key. 파일 경로가 *-secret.example.yaml로 예시 파일임. 수동 검토 권장.`

#### 그룹 상세 섹션의 FP 렌더링

> 그룹(`### [#N-M]`) 상세 섹션에서 FP 블록은 **1줄만** 출력합니다. finding 수만큼 반복하지 않습니다.

| 조건 | 렌더링 |
|------|--------|
| 그룹 내 모든 항목이 동일 FP (level + note) | `**FP 가능성**: {level}` 1줄만 출력 |
| 그룹 내 항목별 FP가 다름 | 대표 FP 1줄 + `(항목별 상이 — 테이블 FP 컬럼 참조)` |

**올바른 예**:
```markdown
### High [#14-22] allow-privilege-escalation (9개) - [Semgrep]
...
**FP 가능성**: 낮음 — generated 매니페스트; 배포 전 securityContext 재검토. 수동 검토 권장.
```

**금지 예** (finding 수만큼 반복):
```markdown
**FP 가능성**: 낮음 — ...
**FP 가능성**: 낮음 — ...
**FP 가능성**: 낮음 — ...
← 이 패턴은 리포트 무효
```

**그룹화 예시 (올바른 형식)**:

```markdown
### High [#18-23] K8s securityContext 미설정 (6개) - [Trivy]
**CWE**: CWE-250 | **Rule ID**: KSV014
**영향받는 파일**:
| # | 파일 | 라인 |
|---|------|-----|
| 18 | app/workload/templates/cronjob_template.yaml | 48 |
| 19 | app/workload/templates/daemonset_template.yaml | 52 |
| 20 | app/workload/templates/deployment_template.yaml | 55 |
| 21 | app/workload/templates/statefulset_template.yaml | 58 |
| 22 | app/cluster/templates/node_pool_template.yaml | 42 |
| 23 | app/cluster/templates/cluster_template.yaml | 38 |
**수정 코드**: `securityContext: { runAsNonRoot: true, allowPrivilegeEscalation: false }`
**FP 가능성**: 낮음 — generated 매니페스트; 배포 전 securityContext 재검토. 수동 검토 권장.
```

**잘못된 예시**: `**영향받는 파일**: cronjob, deployment... 템플릿` → 파일:라인 테이블 없이 추상적 표현 금지

---

## 리포트 형식

### 간단 모드 리포트 (채팅창 출력)

```markdown
## 보안 점검 결과 (간단 모드)
**검토 범위**: Git 변경 파일 N개

### 발견 취약점
| # | 심각도 | 취약점 | 파일:라인 | 출처 | FP 가능성 | CWE | 개수 |
|---|--------|--------|----------|------|-----------|-----|------|
| 1 | Critical | SQL Injection | user.py:45 | [Semgrep] | 매우 낮음 | CWE-89 | 1 |
| 2 | High | IDOR - 소유권 미검증 | user_api.py:83 | [AI-Only] | 매우 낮음 | CWE-639 | 1 |

> **개수 합계**: 2개

### Critical [#1] SQL Injection
**위치**: `user.py:45` | **CWE**: CWE-89 | **OWASP**: A03:2021
**취약 코드**: `query = f"SELECT * FROM users WHERE id = {user_id}"`
**수정 코드**: `cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))`
**FP 가능성**: 매우 낮음.

### High [#2] IDOR - 소유권 미검증
**위치**: `user_api.py:83` | **CWE**: CWE-639 | **OWASP**: A01:2021
**취약 코드**: `user = db.get_user(req.params.id)` — 요청자 본인 확인 없음
**수정 코드**:
    user = db.get_user(req.params.id)
    if user.id != req.session.user_id:
        raise ForbiddenError("접근 권한이 없습니다")
**FP 가능성**: 매우 낮음.

## 수정 체크리스트
| # | 항목 | 수정 |
|---|------|------|
| 1 | SQL Injection - user.py:45 | [ ] |
| 2 | IDOR - user_api.py:83 | [ ] |

> "수정해줘" -> 순차 적용 -> 재스캔 검증
```

**간단 모드 Exit Criteria**:
> 핵심 규칙 11개 전부 적용 (단, #4 개수 정합, #8 검증 결과 섹션, #10 groups 준수는 N/A)
> 추가 조건: 채팅창 리포트 출력 완료 + 수정 체크리스트 포함

**간단 모드 수정 원칙**:
> "수정해줘" 요청 시: Critical/High 즉시 수정, Medium 비즈니스 영향 고려 수정, Low/Info 권고 안내
> 기존 스타일 유지 | 최소 변경 | 수정 후 재스캔

### 감사 모드 리포트 형식

```markdown
# 보안 코드 리뷰 결과

**리포지토리**: {repo} | **검토 일시**: {date}

## 스캔 요약

| 도구 | Raw 개수 |
|------|----------|
| Semgrep | 17 |
| Gitleaks | 4 |
| Trivy (CVE) | 7 |
| Trivy (Misconfig) | 146 |
| AI 분석 | 5 |
| **raw 합계** | **179** |
| **도구 간 중복 병합 후 (고유 항목 수)** | **170** |

> ⚠️ 도구 간 중복 병합: Semgrep + AI 동일 파일:라인 9건 → `[Semgrep+AI]` 병합 (179 → 170)
> ℹ️ 동일 위치 표시: 같은 파일:라인에 동일 도구 Rule 여러 개 → 1행에 `#N-M` 범위로 표시, Rule IDs 상세 섹션에 나열. **# 번호는 개별 부여 (고유 항목 수 유지)**

## 심각도 요약 (고유 항목 수: 170개)

| 심각도 | 개수 |
|--------|------|
| Critical | 4 |
| High | 18 |
| Medium | 20 |
| Low | 120 |
| Info | 8 |

## 발견 취약점 목록

| # | 심각도 | 취약점 | 파일:라인 | 출처 | FP 가능성 | CWE | 개수 |
|---|--------|--------|----------|------|-----------|-----|------|
| 1-4 | Critical | Hardcoded Secrets | `config.py:45` 외 3건 | [Gitleaks] | 매우 낮음 | CWE-798 | 4 |
| 5-11 | High | CVE in dependencies | `go.sum` 외 6건 | [Trivy] | 매우 낮음 | - | 7 |
| 12 | High | SQL Injection | `user_repo.go:80` | [Semgrep] | 매우 낮음 | CWE-89 | 1 |

> **테이블 정렬**: **# 번호 오름차순**. 그룹 `#N-M`은 시작 번호(N) 기준 정렬. 심각도/크기 등 다른 기준으로 재정렬 금지.
> **FP 가능성**: `매우 낮음` / `낮음 — {사유}` / `높음 — {사유}`
> **테이블 개수 합계**: raw 합계와 일치 필수

## 상세 설명

> **상세 섹션 순서**: 테이블과 동일하게 **# 번호 오름차순**으로 작성. 심각도별 재정렬 금지.

### Critical [#1-4] Hardcoded Secrets (4개) - [Gitleaks]
**CWE**: CWE-798 | **OWASP**: A02:2021
**영향받는 파일**:
| # | 파일 | 라인 | Secret Type |
|---|------|-----|-------------|
| 1 | config.py | 45 | Ceph User Key |
| 2 | settings.py | 12 | DB Password |
| 3 | deploy/values.yaml | 88 | API Token |
| 4 | scripts/init.sh | 5 | AWS Access Key |
**수정 코드**: `os.environ.get("CEPH_USER_KEY", "")`
**FP 가능성**: 매우 낮음.

---

## 검증 결과

| 도구 | Raw 개수 | 리포트 개수 | 상태 | 비고 |
|------|----------|-------------|------|------|
| Gitleaks | 4 | 4 | OK | |
| Trivy (CVE) | 7 | 7 | OK | |
| Trivy (Misconfig) | 146 | 146 | OK | |
| Semgrep | 17 | 17 | OK | AI 중복 N건 포함 |
| AI | 5 | 5 | OK | Semgrep 중복 제외 |
| **raw 합계** | **179** | **179** | OK | |
| **도구 간 병합 (고유 항목 수)** | - | **170** | OK | Semgrep+AI 중복 9건 병합. # 최대값 = 170 |

> **검증 규칙**:
> - **테이블 "개수" 컬럼 합계** = raw 합계 (179)
>   - `_status`가 있는 경우: **메인 테이블 개수 합 + FP/Accepted-Risk 접힘 섹션 항목 수** = raw 합계
> - **# 최대값** = 도구 간 중복 병합 후 (고유 항목 수: 170)
> - **도구 간 중복**: Semgrep + AI 동일 파일:라인 → Semgrep에 카운트, AI raw에서 제외
>   - 예: Semgrep 17개 + AI 5개인데 중복 2건 → Semgrep 17개 + AI 3개 = 리포트 20개
> - **동일 위치 표시**: 같은 파일:라인에 동일 도구의 Rule이 N개 → 1행에 `#N-M` 범위로 표시. 고유 항목 수 유지 (# 번호 개별 부여)
> - 모든 도구 OK 확인 후 제출
```

> **리포트 마지막에 필수 포함. 모든 도구가 OK 아니면 리포트 제출 불가.**

---

## 상세 내용 품질 규칙 (금지 패턴)

> **위반 시 리포트 무효.** 아래 패턴이 하나라도 포함되면 해당 상세 섹션 재작성 필수.

### 금지 패턴

| # | 금지 패턴 | 예시 | 올바른 대안 |
|---|----------|------|-----------|
| 1 | **도구 메시지 영문 복붙** | `Set 'containers[].securityContext.runAsNonRoot' to true.` | 한국어로 재작성: `securityContext.runAsNonRoot: true 설정 추가` |
| 2 | **Generic placeholder 수정 권고** | `Semgrep 규칙에 따라 코드를 검토·수정하세요.` | 구체적 수정 방법 제시: `ResponseWriter에 직접 쓰기 대신 json.NewEncoder(w).Encode() 사용` |
| 3 | **도구명만 언급하는 권고** | `Trivy 규칙에 따라 수정하세요.` | 실제 설정 변경 내용 작성 |
| 4 | **설명 없는 수정 코드** | 수정 코드만 있고 왜 취약한지 설명 없음 | 취약 원인 1줄 + 수정 코드 |
| 5 | **remediation 필드 그대로 사용** | unified-findings.json의 `remediation` 영문을 그대로 복사 | 한국어로 번역·재작성 |
| 6 | **동일 수정 권고 반복 복붙** | 서로 다른 취약점에 같은 7항목 나열 패턴 복사 | 각 finding별 구체적 수정 권고 |
| 7 | **파일/기술 맥락 불일치 수정 권고** | A 파일(YAML) 항목의 수정 권고에 B 파일(npm/Go 등) 관련 조언이 섞임 | 해당 finding의 **파일 경로·기술 스택**에 맞는 수정 방법만 작성 |
| 8 | **취약점 설명에 다른 카테고리 설명 혼용** | 특정 CWE 항목에 도구 카테고리 수준의 일반 설명 사용 | **Rule ID·CWE**에 해당하는 구체적 취약점 메커니즘 설명 |

### 맥락 일치 검증 규칙

> 인접 finding의 수정 내용을 복사하다 맥락이 틀어지는 패턴이 빈번한 품질 위반입니다.
> **수정 코드/권고 작성 시 반드시 해당 finding의 파일 경로와 기술 스택을 확인합니다.**

| 검증 항목 | 확인 방법 |
|-----------|----------|
| 파일 확장자/경로와 수정 내용 일치 | finding의 `file` 확장자·경로에 맞는 기술(YAML, Go, Python, Dockerfile 등)의 수정만 작성 |
| 기술 스택 교차 오염 없음 | 다른 기술 스택의 수정 내용이 혼입되지 않았는지 확인 |
| 동일 도구 연속 항목 특히 주의 | 같은 도구의 연속 항목에서 앞 항목 수정 내용이 뒤 항목으로 복붙되지 않도록 확인 |

### 보일러플레이트 방지 — 구조적 검증 규칙

> 특정 문구를 금지하는 것만으로는 agent가 새로운 generic 문구로 우회할 수 있습니다.
> 아래 **구조적 검증 기준**을 적용하여, 어떤 문구든 보일러플레이트이면 자동으로 위반으로 판정합니다.

#### 취약점 설명 품질 기준

| 기준 | 위반 조건 | 올바른 예 |
|------|----------|----------|
| **Rule ID 특이성** | 설명에 해당 Rule ID/CWE가 가리키는 기술적 메커니즘 (공격 방법, 취약 조건, 영향)이 없음 | `gosql-sqli`: "문자열 연결로 SQL 쿼리를 구성하면 공격자가 WHERE 절을 조작하여 인가되지 않은 레코드에 접근할 수 있음" |
| **동일 설명 반복 금지** | 서로 다른 Rule ID의 상세 섹션에 **동일한 설명 텍스트**가 등장 → 보일러플레이트 위반 | 같은 CWE라도 Rule ID가 다르면 메커니즘이 다름. 예: `no-direct-write`와 `no-fprintf` 모두 CWE-79이지만 Write와 Fprintf는 구분하여 설명 |
| **최소 구성 요소** | 설명에 다음 중 1개 이상 포함 필수: ① 공격 시나리오 또는 취약 조건, ② 기술적 영향, ③ 관련 함수/설정/패턴 | "securityContext 미설정 시 컨테이너가 root로 실행되어 호스트 커널 취약점을 통한 권한 탈출이 가능함" |

#### 수정 권고 품질 기준

| 기준 | 위반 조건 | 올바른 예 |
|------|----------|----------|
| **구체적 액션 포함** | 수정 권고에 **해당 파일/기술에 맞는 구체적 설정값, 함수명, 또는 변경 내용**이 1개 이상 없음 | `missing-user`: "Dockerfile에 `USER appuser` 지시어 추가" / `KSV-0110`: "Deployment spec에 `metadata.namespace: {실제 네임스페이스}` 명시" |
| **동일 권고 반복 금지** | 서로 다른 Rule ID의 상세 섹션에 **동일한 수정 권고 텍스트**가 등장 → 보일러플레이트 위반 | Rule ID가 다르면 수정 방법도 다름. 설정 키, 함수, 파라미터가 달라야 함 |
| **심각도별 최소 수준** | Critical/High: 수정 코드 포함 필수 (generic 텍스트만으로는 불충분). Medium: 구체적 설정/함수 언급 필수. Low/Info: 해당 Rule이 점검하는 구체적 항목 1건 이상 명시 | Low `KSV-0110`: "워크로드가 default 네임스페이스에 배포되면 다른 서비스와 네트워크 격리가 안 됨. 전용 네임스페이스를 지정하세요" |

#### 제출 전 보일러플레이트 자가 검증

> 리포트 저장 직전, 아래 절차로 보일러플레이트를 검출합니다.

```
1. 모든 상세 섹션의 **취약점 설명** 텍스트를 수집
2. 서로 다른 Rule ID 섹션에 동일 설명 텍스트가 2회 이상 등장하는지 확인
   → 등장 시: 해당 섹션들을 Rule ID별로 재작성
3. 모든 상세 섹션의 **수정 권고/수정 코드** 텍스트를 수집
4. 서로 다른 Rule ID 섹션에 동일 권고 텍스트가 2회 이상 등장하는지 확인
   → 등장 시: 해당 섹션들을 Rule ID별로 재작성
5. 각 설명·권고에 구체적 액션/메커니즘이 포함되어 있는지 확인
   → 미포함 시: 재작성
6. 검증 통과 후 최종 저장
```

> 이 절차를 생략하면 리포트 무효.

### 영문 자연어 탐지 규칙

> **unified-findings.json의 `remediation` 필드에 영문이 들어있더라도, 리포트에는 반드시 한국어로 재작성해야 합니다.**
> 영문 코드 키워드(함수명, 설정키, 파일명 등)는 허용하되, **영문 자연어 문장은 금지**입니다.

**판별 기준**:
- 마침표(`.`)로 끝나는 영문 문장 → 금지
- `should`, `must`, `is missing`, `detected`, `may run as` 등 영문 조동사/서술문 포함 → 금지
- 영문 코드 키워드(`tls.VersionTLS13`, `runAsNonRoot`, `securityContext` 등) → 허용

**실제 빈출 영문 패턴 → 한국어 변환 필수**:

| 도구 | 영문 원문 (remediation) | 한국어 재작성 |
|------|--------------------------|-------------|
| Semgrep | `Insecure WebSocket Detected. WebSocket Secure (wss) should be used` | `ws:// 대신 wss://를 사용하여 전송 암호화를 적용하세요` |
| Semgrep | `By not specifying a USER, a program in the container may run as 'root'` | `Dockerfile에 USER 지시어를 추가하여 non-root 사용자로 실행하세요` |
| Semgrep | `MinVersion is missing from this TLS configuration` | `tls.Config에 MinVersion: tls.VersionTLS13을 설정하여 최소 TLS 버전을 강제하세요` |
| Trivy | `Set 'containers[].securityContext.runAsNonRoot' to true.` | `securityContext.runAsNonRoot: true를 설정하세요` |
| Trivy | `Ensure that the --anonymous-auth argument is set to false` | `API 서버 인자에 --anonymous-auth=false를 설정하세요` |

> 위 표는 예시입니다. **모든 영문 자연어 문장**에 동일 규칙을 적용하세요.

### 필수 품질 기준

| 심각도 | 설명 | 수정 코드/권고 |
|--------|------|---------------|
| Critical / High | 취약 원인 한국어 설명 (1~2문장) | **소스 코드 Read**하여 실제 컨텍스트 반영한 수정 코드. 그룹은 대표 1건 수정 코드 |
| Medium | 취약 원인 한국어 설명 (1문장) | 구체적 수정 방법 한국어 권고 (설정값, 함수명, 패턴 명시) |
| Low / Info | 한국어 설명 (1문장) | 한국어 권고 (1문장 이상) |

> **모든 설명과 권고는 한국어로 작성합니다.** 도구 원문은 참고만 하고, 개발자가 바로 이해할 수 있는 한국어로 재작성합니다.
> **그룹화된 항목도 대표 수정 코드/권고 1건은 필수입니다.** "상세 참조"만으로 대체 금지.

### 제출 전 영문 검증 절차

> **리포트 저장 직전**, 아래 절차로 영문 자연어 잔존 여부를 확인합니다.

```
1. 리포트의 모든 `**수정 권고**` / `**수정 코드**` / 설명 블록을 스캔
2. 영문 자연어 문장 (주어+동사+목적어 구조, 마침표 종결) 검출
3. 검출 시 → 해당 블록 한국어 재작성 후 저장
4. 미검출 확인 후 최종 저장
```

> 이 절차를 생략하면 리포트 무효.

---

## 심각도별 필수 포함 내용

| 심각도 | 필수 포함 |
|--------|----------|
| Critical / High | 위치, CWE, OWASP, 취약 코드, 설명, **수정 코드** |
| Medium | 위치, 설명, **수정 권고** |
| Low / Info | 위치, 권고 사항 |

**FP 가능성 표기 (모든 출처)**:
- **테이블**: `FP 가능성` 컬럼 — 모든 항목에 표기 (`매우 낮음` / `낮음 — 사유` / `높음 — 사유`)
- **상세**: **모든 상세 섹션**에 `**FP 가능성**:` 블록 필수 (`매우 낮음` 포함, 생략 금지). 그룹 섹션은 1줄만 출력

---

## 제출 전 최종 검증 (11개 항목)

> **리포트 저장 직전 반드시 확인. 하나라도 위반 시 리포트 무효 → 수정 후 저장**

| 규칙 | 검증 방법 |
|------|----------|
| #1 테이블 <-> 상세 1:1 | 테이블의 모든 항목에 대응하는 `### [#N]` 또는 `### [#N-M]` 상세 섹션 존재 확인 |
| #2 Critical/High → 수정 코드 | 해당 항목(개별/그룹 무관)에 `수정 코드` 블록 있는지 확인 |
| #3 Medium/Low → 수정 권고 | 해당 항목(개별/그룹 무관)에 권고 사항 있는지 확인 |
| #4 개수 정합 + 순차 정렬 | 테이블의 **"개수" 컬럼 합계** = `metadata.tool_counts` 각 도구 값의 합. **# 최대값** = `metadata.total_findings`. 그룹 `#N-M`은 M-N+1개. **테이블·상세 모두 # 번호 오름차순 정렬** 확인 |
| #5 제외 금지 | FP/테스트 의심 항목도 리포트에 포함되어 있는지 확인 |
| #6 심각도 고정 | `unified-findings.json`의 severity와 리포트 심각도 일치 확인 |
| #7 파일:라인 명시 | **Critical~Medium**: 전체 파일:라인 테이블 존재. **Low/Info 10건 이상**: 대표 3건 + raw 참조 형식 |
| #8 검증 결과 섹션 | 리포트 마지막에 도구별 raw vs 리포트 비교 테이블 존재, 모든 도구 OK |
| #9 FP 가능성 표기 | 모든 항목의 테이블에 `FP 가능성` 컬럼 반영. **모든 상세 섹션**에 `**FP 가능성**:` 블록 존재 (`매우 낮음` 포함, 생략 금지). **그룹 섹션은 FP 블록 1줄만** (finding 수만큼 반복 없음) 확인 |
| #10 groups 준수 | `groups` 배열의 그룹이 리포트에 `### [#N-M]` 형식으로 반영. Phase D가 임의로 그룹 추가/해체 안 했는지 확인 |
| #11 품질 규칙 (구조적 검증) | ① 영문 자연어 문장 잔존 여부 스캔 ② **보일러플레이트 구조적 검증**: 서로 다른 Rule ID 섹션에 동일 설명 텍스트 2회 이상 등장 → 위반. 동일 권고 텍스트 2회 이상 등장 → 위반 ③ 각 설명에 Rule ID 고유의 기술적 메커니즘 (공격 시나리오/취약 조건/영향 중 1개 이상) 포함 확인 ④ 각 권고에 구체적 설정값/함수명/변경 내용 포함 확인 ⑤ 파일/기술 맥락 일치 확인 |

**하나라도 위반 시 리포트 무효 → 수정 후 저장 필수**
