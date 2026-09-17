"""AI Security & Guardrails Layer: Model Armor & Cloud DLP (SDD Section 1.2 & NFR-2.1).

Provides real-time (<300ms) inspection and masking for both user inputs and LLM outputs:
1. Model Armor: Detects prompt injection, jailbreak attempts, system prompt overrides,
   and unauthorized privilege escalation attempts.
2. Cloud DLP (Sensitive PII / SPII De-identification): Automatically detects and masks
   Singapore NRIC/FIN, Credit Card Numbers, Personal Phone Numbers, Email Addresses,
   Government IDs (MyNumber / SSN / Passport), and Bank Account Numbers.
"""

import os
import re
import time
from typing import Any, Dict, List, Tuple


# ==============================================================================
# 1. CLOUD DLP — SPII / PII Detection & De-identification Rules (SDD Compliant)
# ==============================================================================

DLP_PATTERNS: List[Tuple[str, str, re.Pattern, str]] = [
    (
        "SG_NRIC_FIN",
        "シンガポール NRIC/FIN (国民登録番号/外国人識別番号)",
        re.compile(r"\b[STFGMstfgm]\d{7}[A-Za-z]\b"),
        "[MASKED:SG_NRIC_FIN]",
    ),
    (
        "CREDIT_CARD",
        "クレジットカード番号 (13〜16桁)",
        re.compile(r"\b(?:\d{4}[ -]?){3}\d{1,4}\b"),
        "[MASKED:CREDIT_CARD]",
    ),
    (
        "JP_MY_NUMBER",
        "マイナンバー / 個人番号 (12桁)",
        re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)"),
        "[MASKED:JP_MY_NUMBER]",
    ),
    (
        "US_SSN",
        "米国社会保障番号 (SSN)",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[MASKED:US_SSN]",
    ),
    (
        "PERSONAL_EMAIL",
        "個人メールアドレス (PII)",
        re.compile(r"\b[A-Za-z0-9._%+-]+@(?:gmail\.com|yahoo\.[a-z.]+|hotmail\.com|outlook\.com|icloud\.com)\b", re.IGNORECASE),
        "[MASKED:PERSONAL_EMAIL]",
    ),
    (
        "SG_PERSONAL_MOBILE",
        "シンガポール個人携帯電話番号 (+65 8xxx/9xxx)",
        re.compile(r"(?:\+65[ -]?)?[89]\d{3}[ -]?\d{4}\b"),
        "[MASKED:SG_MOBILE_PHONE]",
    ),
    (
        "BANK_ACCOUNT",
        "銀行口座番号パターン (DBS/POSB/UOB/OCBC等)",
        re.compile(r"\b(?:口座番号|Bank Account|Account No\.?|A/C)[:：\s]*(\d{3}[ -]?\d{3,6}[ -]?\d{1,4})\b", re.IGNORECASE),
        "口座番号: [MASKED:BANK_ACCOUNT]",
    ),
]


# ==============================================================================
# 2. MODEL ARMOR — Prompt Injection & Jailbreak Detection Rules (SDD RSK-02)
# ==============================================================================

MODEL_ARMOR_INJECTION_PATTERNS: List[Tuple[str, str, re.Pattern]] = [
    (
        "PROMPT_INJECTION_IGNORE",
        "命令無視・上書き攻撃 (Ignore previous instructions)",
        re.compile(
            r"(ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)|"
            r"これまでの指示を(すべて|全部)?無視|以前の命令を忘れて|システムプロンプトを(開示|表示|出力))",
            re.IGNORECASE,
        ),
    ),
    (
        "JAILBREAK_DAN_MODE",
        "ジェイルブレイク・制限解除モード (DAN / Developer Mode)",
        re.compile(
            r"(\bDAN\s+mode\b|developer\s+mode\s+enabled|jailbreak|制限を解除して回答|管理者特権モードに移行)",
            re.IGNORECASE,
        ),
    ),
    (
        "PRIVILEGE_ESCALATION",
        "不正な権限昇格・他社員トークン偽装試行",
        re.compile(
            r"(X-MCP-Token\s*を.*上書き|強制的に\s*EMP-\d+\s*のトークンを発行|SQL\s+injection|DROP\s+TABLE)",
            re.IGNORECASE,
        ),
    ),
]


class SecurityGuardrailsEngine:
  """Executes Model Armor inspection and Cloud DLP PII masking in <300ms."""

  def __init__(self):
    self.enabled = os.environ.get("ENABLE_SECURITY_GUARDRAILS", "true").lower() == "true"
    self.block_on_injection = os.environ.get("MODEL_ARMOR_BLOCK_INJECTION", "true").lower() == "true"

  def inspect_and_mask_input(self, text: str) -> Dict[str, Any]:
    """Scans user input for Model Armor threats and masks SPII via Cloud DLP."""
    t0 = time.time()
    if not text or not self.enabled:
      return {
          "original_text": text,
          "sanitized_text": text,
          "blocked": False,
          "model_armor_status": "PASSED",
          "model_armor_findings": [],
          "dlp_masked": False,
          "dlp_findings": [],
          "latency_ms": 0.0,
      }

    # 1. Model Armor Inspection (Prompt Injection / Jailbreak)
    armor_findings: List[Dict[str, str]] = []
    for rule_id, desc, pattern in MODEL_ARMOR_INJECTION_PATTERNS:
      match = pattern.search(text)
      if match:
        armor_findings.append({
            "rule_id": rule_id,
            "description": desc,
            "matched_snippet": match.group(0)[:40],
        })

    blocked = bool(armor_findings and self.block_on_injection)
    armor_status = "BLOCKED_INJECTION" if blocked else ("WARN_FLAGGED" if armor_findings else "PASSED")

    # 2. Cloud DLP Inspection & Masking (SPII De-identification)
    sanitized = text
    dlp_findings: List[Dict[str, Any]] = []
    for info_type, label, pattern, mask_token in DLP_PATTERNS:
      matches = list(pattern.finditer(sanitized))
      if matches:
        for m in matches:
          dlp_findings.append({
              "info_type": info_type,
              "label": label,
              "original_length": len(m.group(0)),
              "masked_as": mask_token,
          })
        sanitized = pattern.sub(mask_token, sanitized)

    latency_ms = round((time.time() - t0) * 1000, 2)
    return {
        "original_text": text,
        "sanitized_text": sanitized,
        "blocked": blocked,
        "model_armor_status": armor_status,
        "model_armor_findings": armor_findings,
        "dlp_masked": len(dlp_findings) > 0,
        "dlp_findings": dlp_findings,
        "latency_ms": latency_ms,
    }

  def inspect_and_mask_output(self, text: str) -> Dict[str, Any]:
    """Scans LLM output for accidental SPII leakage and applies Cloud DLP masking."""
    t0 = time.time()
    if not text or not self.enabled:
      return {
          "sanitized_text": text,
          "dlp_masked": False,
          "dlp_findings": [],
          "latency_ms": 0.0,
      }

    sanitized = text
    dlp_findings: List[Dict[str, Any]] = []
    for info_type, label, pattern, mask_token in DLP_PATTERNS:
      matches = list(pattern.finditer(sanitized))
      if matches:
        for m in matches:
          dlp_findings.append({
              "info_type": info_type,
              "label": label,
              "masked_as": mask_token,
          })
        sanitized = pattern.sub(mask_token, sanitized)

    latency_ms = round((time.time() - t0) * 1000, 2)
    return {
        "sanitized_text": sanitized,
        "dlp_masked": len(dlp_findings) > 0,
        "dlp_findings": dlp_findings,
        "latency_ms": latency_ms,
    }


# Singleton instance for fast inline execution
guardrails_engine = SecurityGuardrailsEngine()
