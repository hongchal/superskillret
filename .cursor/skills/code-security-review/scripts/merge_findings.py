#!/usr/bin/env python3
"""
Stage 3: Merge raw SAST tool outputs into unified-findings.json

Parses outputs from Semgrep, Gitleaks, Trivy, and AI analysis,
normalizes them into a unified schema, deduplicates, and generates
stable fingerprint hashes for each finding.

Usage:
    python3 merge_findings.py \
        --repo-name <name> \
        --raw-dir <path> \
        --date <YYYY-MM-DD> \
        --output <path-to-unified-findings.json>
"""

import argparse
import hashlib
import json
from pathlib import Path


def generate_hash(tool: str, rule_id: str, file: str, code_snippet: str) -> str:
    raw = f"{tool}:{rule_id}:{file}:{code_snippet}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
    "INFO": "info",
    "WARNING": "medium",
    "ERROR": "high",
}

SEMGREP_SEVERITY_MATRIX: dict[tuple[str, str], str] = {
    ("HIGH", "HIGH"): "critical",
    ("HIGH", "MEDIUM"): "high",
    ("HIGH", "LOW"): "high",
    ("MEDIUM", "HIGH"): "high",
    ("MEDIUM", "MEDIUM"): "high",
    ("MEDIUM", "LOW"): "medium",
    ("LOW", "HIGH"): "high",
    ("LOW", "MEDIUM"): "medium",
    ("LOW", "LOW"): "low",
}

CWE_MIN_SEVERITY = {
    "CWE-78": "high",   # OS Command Injection
    "CWE-94": "high",   # Code Injection
    "CWE-95": "high",   # Eval Injection
    "CWE-89": "high",   # SQL Injection
    "CWE-502": "high",  # Deserialization of Untrusted Data
    "CWE-611": "high",  # XXE
    "CWE-918": "high",  # SSRF
}

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def normalize_severity(severity: str) -> str:
    return SEVERITY_MAP.get(severity.strip().upper(), "medium")


def semgrep_severity(impact: str, likelihood: str, cwe: str) -> str:
    key = (impact.strip().upper(), likelihood.strip().upper())
    severity = SEMGREP_SEVERITY_MATRIX.get(key)
    if severity is None:
        severity = normalize_severity(impact if impact else "MEDIUM")

    cwe_base = cwe.split(":")[0].strip() if cwe else ""
    cwe_min = CWE_MIN_SEVERITY.get(cwe_base)
    if cwe_min and SEVERITY_RANK.get(severity, 0) < SEVERITY_RANK.get(cwe_min, 0):
        severity = cwe_min

    return severity


def rule_id_to_title(rule_id: str) -> str:
    last = rule_id.rsplit(".", 1)[-1]
    return last.replace("-", " ").replace("_", " ").title()


def strip_path_prefix(file_path: str, repo_name: str) -> str:
    if not file_path:
        return file_path
    file_path = file_path.lstrip("./")
    if repo_name and repo_name in file_path:
        parts = file_path.split(repo_name, 1)
        if len(parts) > 1:
            return parts[1].lstrip("/")
    return file_path


def find_raw_file(raw_dir: Path, repo_name: str, tool: str, date: str) -> Path | None:
    pattern = f"{repo_name}-{tool}-{date}.json"
    candidates = list(raw_dir.glob(pattern))
    return candidates[0] if candidates else None


def extract_semgrep(raw_dir: Path, repo_name: str, date: str) -> list[dict]:
    path = find_raw_file(raw_dir, repo_name, "semgrep", date)
    if not path:
        print(f"  ⚠️ Semgrep raw not found")
        return []

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠️ Semgrep parse error: {e}")
        return []

    findings = []
    for r in data.get("results", []):
        rule_id = r.get("check_id", "unknown")
        file_path = strip_path_prefix(r.get("path", ""), repo_name)
        line = r.get("start", {}).get("line", 0)
        message = r.get("extra", {}).get("message", "")
        severity = r.get("extra", {}).get("severity", "MEDIUM")
        metadata = r.get("extra", {}).get("metadata", {})
        code = r.get("extra", {}).get("lines", "")

        impact = metadata.get("impact", "")
        likelihood = metadata.get("likelihood", "")

        cwe_raw = metadata.get("cwe", [])
        if isinstance(cwe_raw, list) and cwe_raw:
            cwe = cwe_raw[0].split(":")[0].strip() if ":" in str(cwe_raw[0]) else str(cwe_raw[0])
        elif cwe_raw:
            cwe = str(cwe_raw)
        else:
            cwe = ""

        owasp_raw = metadata.get("owasp", [])
        owasp = owasp_raw[0] if isinstance(owasp_raw, list) and owasp_raw else str(owasp_raw) if owasp_raw else ""

        code_str = str(code).strip()[:500]

        if impact and likelihood:
            resolved_severity = semgrep_severity(impact, likelihood, cwe)
        else:
            resolved_severity = normalize_severity(str(severity))

        finding = {
            "hash": generate_hash("semgrep", rule_id, file_path, code_str),
            "tool": "semgrep",
            "rule_id": rule_id,
            "severity": resolved_severity,
            "title": rule_id_to_title(rule_id),
            "file": file_path,
            "line": line,
            "code_snippet": code_str,
            "cwe": cwe,
            "owasp": owasp,
            "description": message,
            "remediation": "",
        }
        findings.append(finding)

    return findings


def extract_gitleaks(raw_dir: Path, repo_name: str, date: str) -> list[dict]:
    path = find_raw_file(raw_dir, repo_name, "gitleaks", date)
    if not path:
        print(f"  ⚠️ Gitleaks raw not found")
        return []

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠️ Gitleaks parse error: {e}")
        return []

    if not isinstance(data, list):
        return []

    findings = []
    for r in data:
        rule_id = r.get("RuleID", "unknown")
        file_path = strip_path_prefix(r.get("File", ""), repo_name)
        line = r.get("StartLine", 0)
        match_str = r.get("Match", "")
        description = r.get("Description", "")

        masked = match_str[:8] + "***" if len(match_str) > 8 else "***"

        finding = {
            "hash": generate_hash("gitleaks", rule_id, file_path, masked),
            "tool": "gitleaks",
            "rule_id": rule_id,
            "severity": "critical",
            "title": rule_id_to_title(rule_id),
            "file": file_path,
            "line": line,
            "code_snippet": masked,
            "cwe": "CWE-798",
            "owasp": "A02:2021",
            "description": description,
            "remediation": "",
        }
        findings.append(finding)

    return findings


def extract_trivy(raw_dir: Path, repo_name: str, date: str) -> list[dict]:
    path = find_raw_file(raw_dir, repo_name, "trivy", date)
    if not path:
        print(f"  ⚠️ Trivy raw not found")
        return []

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠️ Trivy parse error: {e}")
        return []

    findings = []
    for result in data.get("Results", []):
        target = strip_path_prefix(result.get("Target", ""), repo_name)

        for m in result.get("Misconfigurations", []):
            rule_id = m.get("ID", "unknown")
            severity = m.get("Severity", "MEDIUM")
            title = m.get("Title", "")
            message = m.get("Message", "")
            resolution = m.get("Resolution", "")

            finding = {
                "hash": generate_hash("trivy", rule_id, target, message[:100]),
                "tool": "trivy",
                "rule_id": rule_id,
                "severity": normalize_severity(severity),
                "title": title[:200],
                "file": target,
                "line": 0,
                "code_snippet": message[:500],
                "cwe": "",
                "owasp": "",
                "description": "",
                "remediation": resolution[:500] if resolution else "",
            }
            findings.append(finding)

        for v in result.get("Vulnerabilities", []):
            vuln_id = v.get("VulnerabilityID", "unknown")
            severity = v.get("Severity", "MEDIUM")
            title = v.get("Title", vuln_id)
            pkg = v.get("PkgName", "")
            installed = v.get("InstalledVersion", "")
            fixed = v.get("FixedVersion", "")

            finding = {
                "hash": generate_hash("trivy", vuln_id, target, f"{pkg}@{installed}"),
                "tool": "trivy",
                "rule_id": vuln_id,
                "severity": normalize_severity(severity),
                "title": f"{vuln_id}: {title[:150]}",
                "file": target,
                "line": 0,
                "code_snippet": f"{pkg}@{installed}",
                "cwe": "",
                "owasp": "A06:2021",
                "description": "",
                "remediation": f"Update {pkg} to {fixed}" if fixed else "",
            }
            findings.append(finding)

    return findings


def extract_ai_analysis(raw_dir: Path, repo_name: str, date: str) -> list[dict]:
    path = find_raw_file(raw_dir, repo_name, "ai-analysis", date)
    if not path:
        print(f"  ⚠️ AI analysis raw not found")
        return []

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠️ AI analysis parse error: {e}")
        return []

    if isinstance(data, list):
        raw_findings = data
    elif isinstance(data, dict):
        raw_findings = data.get("findings", [])
    else:
        print(f"  ⚠️ AI analysis: unexpected data type {type(data).__name__}")
        return []

    findings = []
    for r in raw_findings:
        file_path = strip_path_prefix(r.get("file", ""), repo_name)
        code = r.get("code_snippet", "")
        category = r.get("category", "")

        finding = {
            "hash": generate_hash("ai", category, file_path, code),
            "tool": "ai",
            "rule_id": r.get("id", "AI-unknown"),
            "category": category,
            "severity": normalize_severity(r.get("severity", "medium")),
            "title": r.get("title", ""),
            "file": file_path,
            "line": r.get("line", 0),
            "code_snippet": code[:500],
            "cwe": r.get("cwe", ""),
            "owasp": r.get("owasp", ""),
            "description": "",
            "remediation": r.get("remediation", r.get("recommendation", "")),
        }
        findings.append(finding)

    return findings


def main():
    parser = argparse.ArgumentParser(description="Merge SAST raw outputs into unified-findings.json")
    parser.add_argument("--repo-name", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    print(f"📦 Merging findings for {args.repo_name} ({args.date})...")

    semgrep = extract_semgrep(raw_dir, args.repo_name, args.date)
    gitleaks = extract_gitleaks(raw_dir, args.repo_name, args.date)
    trivy = extract_trivy(raw_dir, args.repo_name, args.date)
    ai = extract_ai_analysis(raw_dir, args.repo_name, args.date)

    all_findings = semgrep + gitleaks + trivy + ai

    seen: dict[str, dict] = {}
    unique = []
    merged_lines = 0
    for f in all_findings:
        h = f["hash"]
        if h not in seen:
            seen[h] = f
            unique.append(f)
        else:
            dup_line = f.get("line", 0)
            if dup_line:
                existing = seen[h]
                cur = existing.get("line", 0)
                if isinstance(cur, list):
                    if dup_line not in cur:
                        cur.append(dup_line)
                        cur.sort()
                        merged_lines += 1
                elif cur:
                    if dup_line != cur:
                        existing["line"] = sorted([cur, dup_line])
                        merged_lines += 1
                else:
                    existing["line"] = dup_line

    for idx, f in enumerate(unique, start=1):
        f["id"] = idx

    result = {
        "metadata": {
            "repository": args.repo_name,
            "scan_date": args.date,
            "total_findings": len(unique),
            "tool_counts": {
                "semgrep": len(semgrep),
                "gitleaks": len(gitleaks),
                "trivy": len(trivy),
                "ai": len(ai),
            },
        },
        "findings": unique,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    dedup_count = len(all_findings) - len(unique)
    print(f"✅ Unified findings: {len(unique)} items (dedup from {len(all_findings)}, {dedup_count} merged)")
    if merged_lines:
        print(f"   📍 {merged_lines} additional line locations merged into existing findings")
    print(f"   Semgrep: {len(semgrep)}, Gitleaks: {len(gitleaks)}, Trivy: {len(trivy)}, AI: {len(ai)}")


if __name__ == "__main__":
    main()
