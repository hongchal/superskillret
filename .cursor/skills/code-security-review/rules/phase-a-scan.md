# Phase A: 보안 도구 스캔 규칙

> Orchestrator가 직접 읽고 실행하는 규칙 파일.
> 이 파일의 모든 절차를 순서대로 수행하세요.

---

## 도구 요구사항

| 도구 | 역할 | 최소 버전 |
|------|------|----------|
| **Semgrep** | SAST - 코드 패턴 취약점 | 1.0+ |
| **Gitleaks** | Secret - 시크릿 노출 | 8.0+ |
| **Trivy** | SCA/IaC - CVE + 설정 오류 | 0.50+ |

> 3개 도구 모두 설치 완료되어야 스캔 진행 가능. 1개라도 미설치 시 스캔 진행 금지.

### 설치 명령

```bash
brew install semgrep gitleaks trivy

# brew 실패 시 대안:
# pip3 install semgrep
# curl -sSL https://github.com/gitleaks/gitleaks/releases/latest/download/gitleaks_8.18.4_darwin_arm64.tar.gz | tar -xz && sudo mv gitleaks /usr/local/bin/
# curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin v0.69.3

semgrep --version && gitleaks version && trivy --version
```

---

## Trivy 커스텀 데이터

> Trivy misconfig 스캔 시 사내 레지스트리 오탐 방지를 위해 `--config-data` 플래그 사용.
> 데이터 파일 위치: `.github/baselines/trivy-data/` (DevSec-Process 리포)

```bash
TRIVY_DATA_DIR=$(find ~/Workspace -maxdepth 2 -type d -name "Thaki-DevSec-Process" 2>/dev/null | head -1)/.github/baselines/trivy-data
TRIVY_DATA_OPT=""
[ -d "$TRIVY_DATA_DIR" ] && TRIVY_DATA_OPT="--config-data $TRIVY_DATA_DIR"
[ -d "$TRIVY_DATA_DIR" ] && echo "Trivy data: $TRIVY_DATA_DIR" || echo "Trivy data 미발견 - --config-data 생략"
```

---

## 감사 모드 스캔 절차

변수: `REPO`, `TARGET`, `DATE`, `RAW_DIR` (Orchestrator가 전달)

```bash
mkdir -p "$RAW_DIR"
grep -qxF 'report/' .gitignore 2>/dev/null || echo 'report/' >> .gitignore

# 1. Semgrep
semgrep scan "$TARGET" --config auto --json \
  --output="${RAW_DIR}/${REPO}-semgrep-${DATE}.json"

# 2. Gitleaks (exit code 1 = 시크릿 발견, 정상 동작)
gitleaks detect --source "$TARGET" --no-git --report-format json \
  --report-path "${RAW_DIR}/${REPO}-gitleaks-${DATE}.json"

# 3. Trivy
trivy fs "$TARGET" --scanners vuln,misconfig $TRIVY_DATA_OPT \
  --format json --output "${RAW_DIR}/${REPO}-trivy-${DATE}.json"

ls -la "$RAW_DIR"
```

### Raw 개수 집계 (counts.txt)

> 대용량 JSON 처리: Read 도구 100KB 제한이 있으므로 Shell/Python으로 파싱 필수.

```bash
COUNTS="${RAW_DIR}/counts.txt"

# Semgrep 개수
cat "${RAW_DIR}/${REPO}-semgrep-${DATE}.json" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(f'semgrep={len(d.get(\"results\", []))}')" > "$COUNTS"

# Gitleaks 개수
cat "${RAW_DIR}/${REPO}-gitleaks-${DATE}.json" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print(f'gitleaks={len(d) if isinstance(d, list) else 0}')" >> "$COUNTS"

# Trivy 개수 (CVE + Misconfig 분리)
cat "${RAW_DIR}/${REPO}-trivy-${DATE}.json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
vuln = sum(len(r.get('Vulnerabilities', [])) for r in d.get('Results', []))
misc = sum(len(r.get('Misconfigurations', [])) for r in d.get('Results', []))
print(f'trivy_cve={vuln}')
print(f'trivy_misconfig={misc}')
" >> "$COUNTS"

cat "$COUNTS"
```

> 출력된 개수를 기록해두고, 최종 리포트의 테이블 "개수" 컬럼 합계와 비교 검증 필수.

---

## 간단 모드 스캔 절차

변수: `TARGET` (Orchestrator가 전달)

### 스캔 대상 선택

| 트리거 | 대상 | untracked | modified | staged |
|--------|------|:---------:|:--------:|:------:|
| "staged", "커밋" | staged만 | X | X | O |
| "수정된", "변경된" (기본) | 모든 변경 파일 | O | O | O |

```bash
cd "$TARGET"
CHANGED_FILES=$(git diff HEAD --name-only; git ls-files --others --exclude-standard)
# staged만: CHANGED_FILES=$(git diff --staged --name-only)

# Trivy 커스텀 데이터 (위 "Trivy 커스텀 데이터" 섹션 참조)
TRIVY_DATA_DIR=$(find ~/Workspace -maxdepth 2 -type d -name "Thaki-DevSec-Process" 2>/dev/null | head -1)/.github/baselines/trivy-data
TRIVY_DATA_OPT=""
[ -d "$TRIVY_DATA_DIR" ] && TRIVY_DATA_OPT="--config-data $TRIVY_DATA_DIR"

if [ -n "$CHANGED_FILES" ]; then
  # Semgrep: 파일 목록 직접 전달 가능
  echo "$CHANGED_FILES" | xargs semgrep scan --config auto --json

  # Gitleaks: --source는 디렉토리만 지원. 변경 파일의 디렉토리 목록을 추출하여 스캔
  CHANGED_DIRS=$(echo "$CHANGED_FILES" | xargs -I {} dirname {} | sort -u)
  for dir in $CHANGED_DIRS; do
    gitleaks detect --source "$dir" --no-git -v
  done

  # Trivy: 파일 목록 직접 전달 가능
  echo "$CHANGED_FILES" | xargs trivy fs --scanners vuln,misconfig $TRIVY_DATA_OPT --format json
fi
```

**결과 반환**: 각 도구의 JSON 출력을 그대로 반환.
