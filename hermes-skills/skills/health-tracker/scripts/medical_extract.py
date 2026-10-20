#!/usr/bin/env python3
"""Native PDF extraction. No database access; output is an unconfirmed draft."""
import argparse
import base64
import datetime
import json
import math
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request
import yaml

MODEL = "gemini-3.8-flash-high"
MAX_PDF = 20 * 1024 * 1024


def strict_json(text):
    text = text.strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
        if not match:
            raise ValueError("invalid JSON fence")
        text = match.group(1)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("non-finite JSON number")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def validate(payload, kind, allow_missing_date=False):
    if not isinstance(payload, dict):
        raise ValueError("draft must be an object")
    date = payload.get("report_date")
    if date is None and allow_missing_date:
        pass
    elif not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("report_date requires YYYY-MM-DD; obtain date from owner")
    else:
        datetime.date.fromisoformat(date)
    for key in ("document_title", "lab_name", "doctor_name", "specialization", "source_message_id"):
        if payload.get(key) is not None and not isinstance(payload[key], str):
            raise ValueError(f"{key} must be text or null")
    if kind == "doctor":
        if not isinstance(payload.get("conclusion"), str) or not payload["conclusion"].strip():
            raise ValueError("doctor requires non-empty conclusion")
        return payload
    rows = payload.get("results")
    if not isinstance(rows, list) or not rows:
        raise ValueError("lab requires non-empty results")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("result must be an object")
        for key in ("parameter", "test_type"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"result requires {key}")
        if row.get("unit") is not None and not isinstance(row["unit"], str):
            raise ValueError("unit must be text")
        for key in ("value", "ref_min", "ref_max"):
            value = row.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError(f"{key} must be finite numeric or null")
        if row.get("ref_min") is not None and row.get("ref_max") is not None and row["ref_min"] > row["ref_max"]:
            raise ValueError("reversed reference range")
        if not isinstance(row.get("is_critical", False), bool):
            raise ValueError("is_critical must be boolean")
    return payload


def contained(path, root):
    root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("source escapes source root or is not a file")
    return resolved


def configured_endpoint():
    config_path = Path(os.environ.get("HERMES_HOME", "/opt/data")) / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    provider = config["providers"]["newapi"]
    if not isinstance(provider, dict):
        raise ValueError("newapi provider must be configured")
    if provider.get("key_env", "NEWAPI_API_KEY") != "NEWAPI_API_KEY":
        raise ValueError("newapi requires NEWAPI_API_KEY")
    base = provider["api"]
    if not isinstance(base, str):
        raise ValueError("newapi.api must be a URL")
    return endpoint(base)


def endpoint(base):
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("invalid configured New API base")
    if parsed.scheme == "http" and not (parsed.hostname in ("localhost", "127.0.0.1", "::1")):
        raise ValueError("remote New API requires HTTPS")
    path = parsed.path.rstrip("/")
    if path not in ("", "/v1", "/v1beta"):
        raise ValueError("unsupported New API base path")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, f"/v1beta/models/{MODEL}:generateContent", "", ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("API redirects are forbidden")


def extract(source, root, kind):
    source = contained(source, root)
    with source.open("rb") as stream:
        pdf = stream.read(MAX_PDF + 1)
    if not pdf.startswith(b"%PDF-") or len(pdf) > MAX_PDF:
        raise ValueError("invalid PDF signature or PDF exceeds 20 MiB")
    url = configured_endpoint()
    key = os.environ["NEWAPI_API_KEY"]
    if not key.strip():
        raise ValueError("API key not configured")
    prompt = ('Extract this medical original faithfully. Return ONLY a JSON object. '
              'No invented values. report_date is YYYY-MM-DD or null if unreadable; document_title is the recognized title. '
              'For lab: lab_name, results [{test_type,parameter,value (number or null),unit,ref_min (number or null),ref_max (number or null),is_critical (boolean)}]. '
              'For doctor use top-level keys: doctor_name, specialization, conclusion (full text). '
              f'Document kind: {kind}. Do not use any external database as a reference.')
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}, {"inlineData": {"mimeType": "application/pdf", "data": base64.b64encode(pdf).decode("ascii")}}]}], "generationConfig": {"responseMimeType": "application/json"}}
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=120) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("API response too large")
    envelope = strict_json(raw.decode("utf-8"))
    candidates = envelope.get("candidates", [])
    if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
        raise ValueError("empty, refused or incomplete API response")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
    raw_payload = strict_json(text)
    if kind == "doctor" and "doctor" in raw_payload and isinstance(raw_payload["doctor"], dict):
        doc_data = raw_payload.pop("doctor")
        for k in ("doctor_name", "specialization", "conclusion"):
            if k in doc_data and k not in raw_payload:
                raw_payload[k] = doc_data[k]
    payload = validate(raw_payload, kind, allow_missing_date=True)
    if payload.get("report_date") is None and not payload.get("document_title", "").strip():
        raise ValueError("missing date requires recognized document_title")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("lab", "doctor"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = extract(args.source, args.source_root, args.kind)
        print(json.dumps({"status": "date_required" if payload.get("report_date") is None else "confirmation_required", "source_name": args.source.name, "draft": payload}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        # API errors may contain credentials or medical content. Never print them.
        print(json.dumps({"status": "error", "message": "extraction failed", "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
