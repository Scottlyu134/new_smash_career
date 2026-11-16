from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io_utils import sha256_text
from .schema_validation import validate as validate_json_schema


SKILLS = {
    "python": ["python"], "sql": ["sql"], "r": [" r "], "java": ["java"], "javascript": ["javascript", "typescript"],
    "aws": ["aws", "amazon web services"], "azure": ["azure"], "gcp": ["gcp", "google cloud"],
    "spark": ["spark", "pyspark"], "kafka": ["kafka"], "airflow": ["airflow"], "dbt": ["dbt"],
    "tableau": ["tableau"], "power bi": ["power bi"], "excel": ["excel"],
    "machine learning": ["machine learning"], "deep learning": ["deep learning"], "nlp": ["nlp", "natural language processing"],
    "llm": ["llm", "large language model"], "pytorch": ["pytorch"], "tensorflow": ["tensorflow"],
    "docker": ["docker"], "kubernetes": ["kubernetes"], "git": ["git"], "react": ["react"],
    "statistics": ["statistics", "statistical"], "data visualization": ["data visualization"], "etl": ["etl", "data pipeline"]
}
PREFERRED_HINTS = ("preferred", "preferable", "desirable", "nice to have", "a plus", "bonus", "ideally")


class SchemaValidationError(ValueError):
    pass


def validate_jd_profile(profile: dict, source_text: str, schema: dict | None = None) -> None:
    if schema is not None:
        validate_json_schema(profile, schema)
    required = {"schema_version", "job_id", "job_family", "title", "required_skills", "preferred_skills", "yoe", "seniority", "education", "evidence_spans", "unknown", "provenance"}
    missing = required - profile.keys()
    if missing:
        raise SchemaValidationError(f"Missing required fields: {sorted(missing)}")
    if profile["schema_version"] != "1.0":
        raise SchemaValidationError("Unsupported schema_version")
    if profile["seniority"] not in {"intern", "entry", "mid", "senior", "lead", "executive", "unknown"}:
        raise SchemaValidationError("Invalid seniority")
    evidence = {item["id"]: item for item in profile["evidence_spans"]}
    for eid, item in evidence.items():
        start, end = item["start_char"], item["end_char"]
        if start < 0 or end <= start or source_text[start:end] != item["quote"]:
            raise SchemaValidationError(f"Evidence span {eid} does not match source text")
    for skill in profile["required_skills"] + profile["preferred_skills"]:
        if not skill.get("evidence_ids") or any(eid not in evidence for eid in skill["evidence_ids"]):
            raise SchemaValidationError(f"Skill evidence invalid: {skill}")
    unknown_allowed = {"required_skills", "preferred_skills", "yoe", "seniority", "education"}
    if not set(profile["unknown"]).issubset(unknown_allowed):
        raise SchemaValidationError("Invalid unknown field")
    prov = profile["provenance"]
    if not re.fullmatch(r"[a-f0-9]{64}", prov["source_sha256"]):
        raise SchemaValidationError("Invalid provenance hash")


def _evidence(text: str, field: str, start: int, end: int, items: list[dict]) -> str:
    eid = f"ev-{len(items)+1:03d}"
    items.append({"id": eid, "field": field, "quote": text[start:end], "start_char": start, "end_char": end})
    return eid


def mock_extract(jd: dict, model: str = "mock-rules-v1", schema: dict | None = None) -> dict:
    text = jd["description_clean"]
    lower = f" {text.lower()} "
    evidence: list[dict] = []
    required: list[dict] = []
    preferred: list[dict] = []
    for normalized, aliases in SKILLS.items():
        match = None
        for alias in aliases:
            match = re.search(r"(?<![a-z0-9])" + re.escape(alias.strip()) + r"(?![a-z0-9])", lower)
            if match:
                break
        if not match:
            continue
        start = max(0, match.start() - 1)
        end = max(start + 1, match.end() - 1)
        context = lower[max(0, match.start() - 80):match.end() + 40]
        field = "preferred_skills" if any(hint in context for hint in PREFERRED_HINTS) else "required_skills"
        eid = _evidence(text, field, start, end, evidence)
        item = {"name": text[start:end], "normalized": normalized, "evidence_ids": [eid]}
        (preferred if field == "preferred_skills" else required).append(item)

    yoe_match = re.search(
        r"(?i)\b(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\+?\s+years?(?:['’])?(?:\s+of)?(?:\s+[a-z][a-z/-]*){0,6}\s+experience\b",
        text,
    )
    if yoe_match:
        eid = _evidence(text, "yoe", yoe_match.start(), yoe_match.end(), evidence)
        yoe = {"minimum": int(yoe_match.group(1)), "maximum": int(yoe_match.group(2)) if yoe_match.group(2) else None, "status": "explicit", "evidence_ids": [eid]}
    else:
        yoe = {"minimum": None, "maximum": None, "status": "unknown", "evidence_ids": []}

    degree_patterns = [
        ("associate", r"(?i)\bassociate(?:['’]s)?\s+degree\b"),
        ("bachelor", r"(?i)\b(bachelor(?:['’]s)?|b\.?s\.?|b\.?a\.?)\b"),
        ("master", r"(?i)\b(master(?:['’]s|s)|master\s+(?:of|degree)|m\.?s\.?|m\.?sc\.?)\b"),
        ("doctorate", r"(?i)\b(ph\.?d\.?|doctorate)\b"),
    ]
    education = {"minimum_level": "unknown", "preferred_levels": [], "status": "unknown", "evidence_ids": []}
    for level, pattern in degree_patterns:
        match = re.search(pattern, text)
        if match:
            eid = _evidence(text, "education", match.start(), match.end(), evidence)
            nearby = text[max(0, match.start()-60):match.end()+40].lower()
            if any(hint in nearby for hint in PREFERRED_HINTS):
                education["preferred_levels"].append(level)
            elif education["minimum_level"] == "unknown":
                education["minimum_level"] = level
            education["status"] = "explicit"
            education["evidence_ids"].append(eid)

    title = jd["title"].lower()
    source_level = (jd.get("source_experience_level") or "").lower()
    if "intern" in title:
        seniority = "intern"
    elif any(x in title for x in ("principal", "staff", "lead", "manager", "director")):
        seniority = "lead"
    elif any(x in title for x in ("senior", " sr ", "sr.")):
        seniority = "senior"
    elif "entry" in source_level or "junior" in title:
        seniority = "entry"
    elif "mid" in source_level or (yoe["minimum"] is not None and yoe["minimum"] >= 2):
        seniority = "mid"
    else:
        seniority = "unknown"

    unknown = []
    if not required: unknown.append("required_skills")
    if not preferred: unknown.append("preferred_skills")
    if yoe["status"] == "unknown": unknown.append("yoe")
    if seniority == "unknown": unknown.append("seniority")
    if education["status"] == "unknown": unknown.append("education")
    profile = {
        "schema_version": "1.0", "job_id": jd["job_id"], "job_family": jd["job_family"], "title": jd["title"],
        "required_skills": required, "preferred_skills": preferred, "yoe": yoe, "seniority": seniority, "education": education,
        "evidence_spans": evidence, "unknown": unknown,
        "provenance": {"source_job_id": jd["job_id"], "source_sha256": sha256_text(text), "extractor": "mock_rule_extractor", "model": model, "created_at": datetime.now(timezone.utc).isoformat()}
    }
    validate_jd_profile(profile, text, schema)
    return profile


@dataclass
class Budget:
    max_calls: int
    max_estimated_usd: float
    input_usd_per_million: float
    output_usd_per_million: float
    calls: int = 0
    estimated_usd: float = 0.0

    def reserve(self, input_chars: int, expected_output_chars: int = 6000) -> None:
        projected = self.estimated_usd + (input_chars / 4 / 1_000_000 * self.input_usd_per_million) + (expected_output_chars / 4 / 1_000_000 * self.output_usd_per_million)
        if self.calls + 1 > self.max_calls or projected > self.max_estimated_usd:
            raise RuntimeError("Cost/call budget would be exceeded before API request")
        self.calls += 1
        self.estimated_usd = projected


class OpenAICompatibleExtractor:
    def __init__(self, config: dict, schema: dict, logger: logging.Logger):
        self.config, self.schema, self.logger = config, schema, logger
        self.api_key = os.environ.get(config["api_key_env"])
        if not self.api_key:
            raise RuntimeError(f"Missing API key environment variable: {config['api_key_env']}")
        self.budget = Budget(config["max_calls"], config["max_estimated_usd"], config["input_usd_per_million"], config["output_usd_per_million"])

    def extract(self, jd: dict) -> dict:
        text = jd["description_clean"]
        self.budget.reserve(len(text))
        payload = {
            "model": self.config["model"], "temperature": 0,
            "messages": [
                {"role": "system", "content": "Extract only explicitly supported JD facts. Every extracted fact must cite exact character spans. Use unknown rather than guessing."},
                {"role": "user", "content": json.dumps({"job_id": jd["job_id"], "job_family": jd["job_family"], "title": jd["title"], "description": text}, ensure_ascii=False)}
            ],
            "response_format": {"type": "json_schema", "json_schema": {"name": "jd_profile", "strict": True, "schema": self.schema}}
        }
        body = json.dumps(payload).encode()
        url = self.config["base_url"].rstrip("/") + "/chat/completions"
        last_error: Exception | None = None
        for attempt in range(self.config["max_retries"] + 1):
            try:
                req = urllib.request.Request(url, data=body, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=self.config["timeout_seconds"]) as response:
                    result = json.loads(response.read())
                profile = json.loads(result["choices"][0]["message"]["content"])
                validate_jd_profile(profile, text, self.schema)
                self.logger.info(json.dumps({"event": "api_success", "job_id": jd["job_id"], "attempt": attempt + 1, "usage": result.get("usage", {})}))
                return profile
            except (urllib.error.URLError, KeyError, json.JSONDecodeError, SchemaValidationError) as exc:
                last_error = exc
                self.logger.warning(json.dumps({"event": "api_retry", "job_id": jd["job_id"], "attempt": attempt + 1, "error_type": type(exc).__name__}))
                if attempt < self.config["max_retries"]:
                    time.sleep(min(8, 2 ** attempt))
        raise RuntimeError(f"Extraction failed after retries: {type(last_error).__name__}")

