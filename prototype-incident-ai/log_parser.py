"""Regex-based extraction of operational evidence from unstructured logs."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ParsedLogEvidence:
    error_signatures: list[str]
    status_codes: list[str]
    stack_traces: list[str]

    def to_dict(self) -> dict[str, list[str]]:
        return asdict(self)


_SIGNATURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Out of memory", re.compile(r"\b(?:out[ -]of[ -]memory|oom(?:killed|kill)?|java\.lang\.OutOfMemoryError)\b", re.I)),
    ("Connection timeout", re.compile(r"\b(?:connection|connect|read)\s+(?:timed?\s*out|timeout)\b", re.I)),
    ("Database pool exhausted", re.compile(r"\b(?:connection pool exhausted|pool timeout|unable to acquire (?:a )?connection)\b", re.I)),
    ("Database lock/deadlock", re.compile(r"\b(?:deadlock detected|lock wait timeout|database is locked|could not obtain lock)\b", re.I)),
    ("Disk space exhausted", re.compile(r"\b(?:no space left on device|disk (?:full|usage.*100%))\b", re.I)),
    ("DNS resolution failure", re.compile(r"\b(?:name or service not known|temporary failure in name resolution|nxdomain)\b", re.I)),
    ("TLS/certificate failure", re.compile(r"\b(?:certificate verify failed|ssl handshake failed|certificate has expired)\b", re.I)),
    ("Connection refused", re.compile(r"\bconnection refused\b", re.I)),
)

_HTTP_STATUS = re.compile(r"\b(?:HTTP(?:/\d(?:\.\d)?)?\s*[:=-]?\s*)?(5\d{2})\b", re.I)
_NAMED_STATUS = re.compile(r"\b(HTTP\s+5xx|BAD_GATEWAY|SERVICE_UNAVAILABLE|GATEWAY_TIMEOUT)\b", re.I)
_TRACE_START = re.compile(r"^(?:Traceback \(most recent call last\):|[\w.$]+(?:Error|Exception)(?::|$))")
_TRACE_LINE = re.compile(r"^(?:\s+at\s+|\s*File \"|Caused by:|\s*\.\.\. \d+ more|\s*\^)")


def _unique(items: list[str], limit: int = 50) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
        if len(result) >= limit:
            break
    return result


def _extract_stack_traces(raw_logs: str) -> list[str]:
    lines = raw_logs.splitlines()
    traces: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if not _TRACE_START.search(line.strip()):
            index += 1
            continue
        block = [line]
        index += 1
        while index < len(lines) and (_TRACE_LINE.search(lines[index]) or not lines[index].strip()):
            if lines[index].strip():
                block.append(lines[index].rstrip())
            index += 1
            if len(block) >= 25:
                break
        traces.append("\n".join(block))
    return _unique(traces, limit=10)


def parse_logs(raw_logs: str) -> ParsedLogEvidence:
    """Extract normalized signatures, HTTP 5xx codes, and stack-trace blocks."""

    signatures = [label for label, pattern in _SIGNATURE_PATTERNS if pattern.search(raw_logs)]
    status_codes = [f"HTTP {match}" for match in _HTTP_STATUS.findall(raw_logs)]
    status_codes.extend(match.upper().replace("_", " ") for match in _NAMED_STATUS.findall(raw_logs))
    return ParsedLogEvidence(
        error_signatures=_unique(signatures),
        status_codes=_unique(status_codes),
        stack_traces=_extract_stack_traces(raw_logs),
    )

