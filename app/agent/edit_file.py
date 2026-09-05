"""
Production-quality edit_file module for an AI coding agent.

Safety-first architecture:
  find candidate → evaluate confidence/evidence → validate target →
  construct candidate file → validate candidate → apply edit

Matching Strategy (layered, safest-first):
=============================================
Layer 1 - EXACT MATCH (confidence: 1.0)
    Direct substring search. Zero tolerance for any difference.

Layer 2 - LINE-ENDING NORMALIZED MATCH (confidence: 0.98)
    Normalizes \r\n and \r to \n before matching.

Layer 3 - INDENTATION-NORMALIZED MATCH (confidence: 0.95)
    Strips leading whitespace from each line and compares stripped content.

Layer 4 - WHITESPACE-NORMALIZED MATCH (confidence: 0.92)
    Collapses internal whitespace while preserving structure.

Layer 5 - OPERATOR-SPACING NORMALIZED MATCH (confidence: 0.90)
    Normalizes spacing around operators while preserving string literals.

Layer 6 - STRUCTURAL/LANGUAGE-AWARE MATCH (confidence: 0.88)
    [Extension point] Language-aware structural matching.
    Currently a placeholder for future implementation.

Layer 7 - FUZZY CANDIDATE DISCOVERY (candidates only, NEVER auto-edits)
    Uses difflib.SequenceMatcher to find approximate matches.
    Returns candidates for the LLM to inspect, but NEVER modifies the file.

Operations Supported:
=====================
- replace:  Replace matched text with new content (ONLY supported operation)

Safety Guarantees:
==================
- At EVERY layer, multiple matches -> ambiguous_match error.
- Fuzzy matches NEVER auto-edit; they return candidates for inspection.
- Never modifies content outside the matched region.
- Preserves original file line endings (\n, \r\n, \r).
- Atomic writes via temp-file + rename (crash-safe).
- File size guard (configurable, default 1MB).
- Dry-run mode for previewing edits without writing.
- Structured results with match_type, confidence, line numbers, and context.
- Replacement code is re-indented to match target location.
- Pluggable validation interface for syntax checking.
- Edit success is separated from validation success.
"""

import re
import os
import difflib
import tempfile
from pathlib import Path
from typing import Optional, List, Tuple, Protocol, runtime_checkable
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILE_SIZE = 1_048_576  # 1 MB default limit
FUZZY_THRESHOLD = 0.90
FUZZY_MIN_LINES = 2  # Minimum lines for line-based fuzzy to activate


# ---------------------------------------------------------------------------
# ValidationResult & CodeValidator Protocol (Phase 3/4 extension point)
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Result from a validation check."""
    passed: bool
    validator_name: str = "none"
    message: Optional[str] = None
    details: Optional[dict] = None

    def to_dict(self) -> dict:
        d = {"passed": self.passed, "validator": self.validator_name}
        if self.message:
            d["message"] = self.message
        if self.details:
            d["details"] = self.details
        return d


@runtime_checkable
class CodeValidator(Protocol):
    """Pluggable validation interface for language-specific checks.

    Implementations can validate syntax, lint, run tests, etc.
    The core editing engine does NOT hard-code any language behavior.
    """

    def validate(self, file_path: str, content: str) -> ValidationResult:
        """Validate candidate content for the given file.

        Args:
            file_path: Path to the file being edited (for language detection).
            content: The full candidate file content after edit.

        Returns:
            ValidationResult indicating pass/fail with details.
        """
        ...


class NoOpValidator:
    """Default validator that always passes (no validation configured)."""

    def validate(self, file_path: str, content: str) -> ValidationResult:
        return ValidationResult(passed=True, validator_name="noop")


# ---------------------------------------------------------------------------
# MatchCandidate
# ---------------------------------------------------------------------------

@dataclass
class MatchCandidate:
    """A single candidate match location."""
    start_offset: int
    end_offset: int
    start_line: int
    end_line: int
    confidence: float
    strategy: str

    def to_dict(self) -> dict:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "confidence": round(self.confidence, 4),
            "strategy": self.strategy,
        }


# ---------------------------------------------------------------------------
# MatchResult (structured evidence from matching)
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """Structured result from the matching engine with full evidence."""
    found: bool = False
    start: Optional[int] = None
    end: Optional[int] = None
    strategy: str = "none"
    confidence: float = 0.0
    match_count: int = 0
    safe: bool = False
    candidates: List[MatchCandidate] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    error: Optional[str] = None
    message: Optional[str] = None
    hint: Optional[str] = None
    suggested_action: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "found": self.found,
            "strategy": self.strategy,
            "confidence": round(self.confidence, 4),
            "match_count": self.match_count,
            "safe": self.safe,
        }
        if self.start is not None:
            d["start_offset"] = self.start
        if self.end is not None:
            d["end_offset"] = self.end
        if self.candidates:
            d["candidates"] = [c.to_dict() for c in self.candidates]
        if self.evidence:
            d["evidence"] = self.evidence
        if self.error:
            d["error"] = self.error
        if self.message:
            d["message"] = self.message
        if self.hint:
            d["hint"] = self.hint
        if self.suggested_action:
            d["suggested_action"] = self.suggested_action
        return d


# ---------------------------------------------------------------------------
# EditPlan (separates matching from modification)
# ---------------------------------------------------------------------------

@dataclass
class EditPlan:
    """Describes a planned edit before it is applied."""
    file_path: str
    operation: str
    match: MatchResult
    original_content: str
    replacement_text: Optional[str] = None
    # Computed after planning
    new_content: Optional[str] = None
    diff_preview: Optional[str] = None
    lines_added: int = 0
    lines_removed: int = 0

    @property
    def is_valid(self) -> bool:
        return self.new_content is not None


# ---------------------------------------------------------------------------
# EditResult (enhanced with validation and diff info)
# ---------------------------------------------------------------------------

@dataclass
class EditResult:
    """Structured result from an edit operation."""
    success: bool
    match_type: str = "none"
    confidence: float = 0.0
    error: Optional[str] = None
    message: Optional[str] = None
    hint: Optional[str] = None
    suggested_action: Optional[str] = None
    match_count: int = 0
    operations_applied: int = 0
    # Location info
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    # Preview
    preview_before: Optional[str] = None
    preview_after: Optional[str] = None
    # New fields for richer LLM feedback
    candidates: List[dict] = field(default_factory=list)
    edit_applied: bool = False
    validation: Optional[dict] = None
    diff_preview: Optional[str] = None
    lines_added: int = 0
    lines_removed: int = 0

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "match_type": self.match_type,
            "confidence": self.confidence,
        }
        if self.error:
            d["error"] = self.error
        if self.message:
            d["message"] = self.message
        if self.hint:
            d["hint"] = self.hint
        if self.suggested_action:
            d["suggested_action"] = self.suggested_action
        if self.match_count > 0:
            d["match_count"] = self.match_count
        if self.operations_applied > 0:
            d["operations_applied"] = self.operations_applied
        if self.start_line is not None:
            d["start_line"] = self.start_line
        if self.end_line is not None:
            d["end_line"] = self.end_line
        if self.preview_before is not None:
            d["preview_before"] = self.preview_before
        if self.preview_after is not None:
            d["preview_after"] = self.preview_after
        if self.candidates:
            d["candidates"] = self.candidates
        if self.edit_applied:
            d["edit_applied"] = True
        if self.validation:
            d["validation"] = self.validation
        if self.diff_preview:
            d["diff_preview"] = self.diff_preview
        if self.lines_added > 0:
            d["lines_added"] = self.lines_added
        if self.lines_removed > 0:
            d["lines_removed"] = self.lines_removed
        return d


# ---------------------------------------------------------------------------
# Line-ending utilities
# ---------------------------------------------------------------------------

def _detect_line_ending(content: str) -> str:
    """Detect the dominant line ending style in the content."""
    crlf_count = content.count('\r\n')
    lf_count = content.count('\n') - crlf_count
    cr_count = content.count('\r') - crlf_count

    if crlf_count >= lf_count and crlf_count >= cr_count and crlf_count > 0:
        return '\r\n'
    elif cr_count > lf_count:
        return '\r'
    return '\n'


def _normalize_to_lf(content: str) -> str:
    """Normalize all line endings to \n for matching purposes."""
    return content.replace('\r\n', '\n').replace('\r', '\n')


def _offset_to_line(content: str, offset: int) -> int:
    """Convert a character offset to a 1-based line number."""
    return content.count('\n', 0, offset) + 1


def _get_line_offsets(content: str) -> List[Tuple[int, int]]:
    """Return list of (start, end) offsets for each line (end excludes newline)."""
    lines = []
    start = 0
    content_len = len(content)
    while start < content_len:
        end = content.find('\n', start)
        if end == -1:
            lines.append((start, content_len))
            break
        lines.append((start, end))
        start = end + 1
    if not lines:
        lines.append((0, 0))
    return lines


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str, encoding: str = 'utf-8') -> None:
    """Write content to file atomically using temp file + rename."""
    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding=encoding, newline='') as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Re-indentation utilities
# ---------------------------------------------------------------------------

def _get_indentation(line: str) -> str:
    """Extract the leading whitespace of a line."""
    return line[:len(line) - len(line.lstrip())]


def _reindent_replacement(
    replacement: str,
    target_indent: str,
    original_matched_text: str
) -> str:
    """Re-indent replacement code to match the target location.

    Strategy:
    1. Determine the base indentation of the target (from the matched text).
    2. Determine the minimum indentation of the replacement block.
    3. Shift all replacement lines by the difference.
    4. Preserve relative indentation between lines.
    5. Preserve blank lines.
    """
    if not replacement or not replacement.strip():
        return replacement

    replace_lines = replacement.split('\n')

    # Find minimum indentation of non-empty replacement lines
    min_indent = None
    for line in replace_lines:
        if line.strip():  # Non-empty line
            indent = _get_indentation(line)
            if min_indent is None or len(indent) < len(min_indent):
                min_indent = indent

    if min_indent is None:
        # All blank lines
        return replacement

    # Determine target indentation from the matched text's first line
    if not target_indent:
        target_indent = ""

    # Calculate the shift needed
    # If replacement min indent == target indent, no shift needed
    # If replacement has more indent, strip the excess
    # If replacement has less indent, add the deficit

    result_lines = []
    for line in replace_lines:
        if not line.strip():
            # Preserve blank lines (but give them target indent if they had any)
            result_lines.append("")
            continue

        line_indent = _get_indentation(line)
        line_content = line[len(line_indent):]

        # Calculate relative indentation from replacement's minimum
        if line_indent.startswith(min_indent):
            relative_indent = line_indent[len(min_indent):]
        else:
            # Indentation doesn't share prefix; compute relative by length
            relative_len = max(0, len(line_indent) - len(min_indent))
            # Use the same indent character style as target
            if target_indent and '\t' in target_indent:
                relative_indent = '\t' * relative_len
            else:
                relative_indent = ' ' * relative_len

        new_line = target_indent + relative_indent + line_content
        result_lines.append(new_line)

    return '\n'.join(result_lines)


# ---------------------------------------------------------------------------
# Layer 1: Exact Match
# ---------------------------------------------------------------------------

def _find_exact_matches(content: str, search: str) -> List[int]:
    """Find all positions where search appears exactly in content."""
    positions = []
    start = 0
    while True:
        idx = content.find(search, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions


# ---------------------------------------------------------------------------
# Layer 2: Line-Ending Normalized Match
# ---------------------------------------------------------------------------
def _find_line_ending_normalized_matches(
    content: str, search: str
) -> List[Tuple[int, int]]:
    """
    Find matches after normalizing line endings (\r\n and \r to \n).
    Returns list of (start_offset, end_offset) in the original content.
    This layer handles files with mixed or non-LF line endings where
    the search text uses a different line ending style.
    """
    # Build a mapping from normalized offsets to original offsets
    # Each \r\n in original becomes \n in normalized (loses 1 char)
    # Each standalone \r becomes \n (no length change)
    offset_map = []  # offset_map[normalized_pos] = original_pos
    i = 0
    content_len = len(content)
    while i < content_len:
        offset_map.append(i)
        if content[i] == '\r':
            if i + 1 < content_len and content[i + 1] == '\n':
                i += 2  # \r\n -> \n (skip 2 chars in original, 1 in normalized)
            else:
                i += 1  # \r -> \n (skip 1 char)
        else:
            i += 1
    offset_map.append(content_len)  # sentinel for end

    # Normalize both content and search to LF
    normalized_content = content.replace('\r\n', '\n').replace('\r', '\n')
    normalized_search = search.replace('\r\n', '\n').replace('\r', '\n')

    # If normalization didn't change anything, this layer adds nothing
    if normalized_content == content and normalized_search == search:
        return []

    # Find exact matches in the normalized content
    positions = []
    start = 0
    while True:
        idx = normalized_content.find(normalized_search, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1

    if not positions:
        return []

    # Map normalized offsets back to original offsets
    matches = []
    for norm_start in positions:
        norm_end = norm_start + len(normalized_search)
        if norm_start < len(offset_map) and norm_end < len(offset_map):
            orig_start = offset_map[norm_start]
            orig_end = offset_map[norm_end]
            if orig_end > orig_start:
                matches.append((orig_start, orig_end))

    return matches



# ---------------------------------------------------------------------------
# Layer 3: Indentation-Normalized Match
# ---------------------------------------------------------------------------

def _find_indentation_normalized_matches(
    content: str, search: str
) -> List[Tuple[int, int]]:
    """
    Find matches ignoring leading whitespace differences.
    Returns list of (start_offset, end_offset) in the original content.
    """
    search_stripped_lines = [line.lstrip() for line in search.rstrip('\n').split('\n')]
    if not search_stripped_lines:
        return []

    content_lines_raw = content.split('\n')
    content_stripped_lines = [line.lstrip() for line in content_lines_raw]
    num_search = len(search_stripped_lines)

    if num_search > len(content_lines_raw):
        return []

    # Precompute cumulative line lengths for fast offset calculation
    line_lengths = [len(line) + 1 for line in content_lines_raw]
    cumulative = [0] * (len(content_lines_raw) + 1)
    for i, ll in enumerate(line_lengths):
        cumulative[i + 1] = cumulative[i] + ll

    matches = []
    for i in range(len(content_lines_raw) - num_search + 1):
        if content_stripped_lines[i:i + num_search] == search_stripped_lines:
            start_offset = cumulative[i]
            end_offset = cumulative[i + num_search] - 1
            if end_offset > start_offset:
                matches.append((start_offset, end_offset))

    return matches


# ---------------------------------------------------------------------------
# Layer 4: Whitespace-Normalized Match
# ---------------------------------------------------------------------------

def _normalize_whitespace_line(line: str) -> str:
    """Normalize internal whitespace in a line while preserving structure.

    Collapses multiple spaces/tabs to single space, strips leading/trailing.
    Does NOT modify string literal contents.
    """
    # Split by string literals to preserve them
    parts = _STRING_LITERAL_RE.split(line)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # String literal - preserve exactly
            result.append(part)
        else:
            # Normalize whitespace in non-string parts
            normalized = re.sub(r'[ \t]+', ' ', part)
            result.append(normalized)
    combined = ''.join(result)
    return combined.strip()


def _find_whitespace_normalized_matches(
    content: str, search: str
) -> List[Tuple[int, int]]:
    """
    Find matches ignoring whitespace differences (internal spacing).
    Preserves string literal contents.
    Returns list of (start_offset, end_offset).
    """
    search_lines = [_normalize_whitespace_line(l) for l in search.rstrip('\n').split('\n')]
    if not search_lines:
        return []

    content_lines_raw = content.split('\n')
    content_normalized = [_normalize_whitespace_line(l) for l in content_lines_raw]
    num_search = len(search_lines)

    if num_search > len(content_lines_raw):
        return []

    line_lengths = [len(line) + 1 for line in content_lines_raw]
    cumulative = [0] * (len(content_lines_raw) + 1)
    for i, ll in enumerate(line_lengths):
        cumulative[i + 1] = cumulative[i] + ll

    matches = []
    for i in range(len(content_lines_raw) - num_search + 1):
        if content_normalized[i:i + num_search] == search_lines:
            start_offset = cumulative[i]
            end_offset = cumulative[i + num_search] - 1
            if end_offset > start_offset:
                matches.append((start_offset, end_offset))

    return matches


# ---------------------------------------------------------------------------
# Layer 5: Operator-Spacing Normalized Match
# ---------------------------------------------------------------------------

_OPERATORS = [
    '==', '!=', '<=', '>=', '+=', '-=', '*=', '/=', '//=', '%=', '**=',
    '<<=', '>>=', '&=', '|=', '^=',
    '->', ':=',
    '&&', '||',
    '<', '>', '=', '+', '-', '*', '/', '%', '&', '|', '^', '~',
]

_STRING_LITERAL_RE = re.compile(
    r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')'
)

_KEYWORDS_BEFORE_PAREN = re.compile(
    r'\b(if|elif|while|for|return|yield|assert|import|from|in|not|and|or|is|del|'
    r'with|as|lambda|def|class|try|except|finally|raise|global|nonlocal)\('
)


def _normalize_operator_spacing(line: str) -> str:
    """
    Normalize spacing around operators in a single line.
    Preserves content inside string literals.
    """
    parts = _STRING_LITERAL_RE.split(line)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # String literal - preserve exactly
            result.append(part)
        else:
            normalized = part
            for op in _OPERATORS:
                escaped = re.escape(op)
                normalized = re.sub(
                    r'\s*' + escaped + r'\s*',
                    f' {op} ',
                    normalized
                )
            # Clean up multiple spaces
            normalized = re.sub(r'  +', ' ', normalized)
            # Remove space before brackets/parens
            normalized = re.sub(r'\s+\(', '(', normalized)
            normalized = re.sub(r'\s+\)', ')', normalized)
            normalized = re.sub(r'\s+\[', '[', normalized)
            normalized = re.sub(r'\s+\]', ']', normalized)
            # Restore space after keywords
            normalized = _KEYWORDS_BEFORE_PAREN.sub(r'\1 (', normalized)
            result.append(normalized)
    return ''.join(result)


def _find_operator_spacing_matches(
    content: str, search: str
) -> List[Tuple[int, int]]:
    """
    Find matches ignoring operator spacing differences.
    Also normalizes leading indentation (combines indentation + operator).
    Returns list of (start_offset, end_offset).
    """
    search_lines = search.rstrip('\n').split('\n')
    search_normalized = [_normalize_operator_spacing(l.lstrip()) for l in search_lines]

    content_lines_raw = content.split('\n')
    content_normalized = [_normalize_operator_spacing(l.lstrip()) for l in content_lines_raw]

    num_search = len(search_normalized)
    if not search_normalized or num_search > len(content_lines_raw):
        return []

    line_lengths = [len(line) + 1 for line in content_lines_raw]
    cumulative = [0] * (len(content_lines_raw) + 1)
    for i, ll in enumerate(line_lengths):
        cumulative[i + 1] = cumulative[i] + ll

    matches = []
    for i in range(len(content_lines_raw) - num_search + 1):
        if content_normalized[i:i + num_search] == search_normalized:
            start_offset = cumulative[i]
            end_offset = cumulative[i + num_search] - 1
            if end_offset > start_offset:
                matches.append((start_offset, end_offset))

    return matches


# ---------------------------------------------------------------------------
# Layer 7: Fuzzy Match (Candidate Discovery Only - NEVER auto-edits)
# ---------------------------------------------------------------------------

def _find_fuzzy_matches(
    content: str, search: str, threshold: float = FUZZY_THRESHOLD
) -> List[Tuple[int, int, float]]:
    """
    Find approximate matches using line-based SequenceMatcher.
    Returns list of (start_offset, end_offset, confidence).

    IMPORTANT: This is candidate discovery only. Results from this layer
    are NEVER used to automatically modify the file.
    """
    search_lines = [l.rstrip() for l in search.rstrip('\n').split('\n')]
    if not search_lines:
        return []

    num_search = len(search_lines)

    # For very short searches (< 2 lines), fall back to character-level
    if num_search < FUZZY_MIN_LINES:
        return _find_fuzzy_char_level(content, search, threshold)

    content_lines_raw = content.split('\n')
    if num_search > len(content_lines_raw):
        return []

    line_lengths = [len(line) + 1 for line in content_lines_raw]
    cumulative = [0] * (len(content_lines_raw) + 1)
    for i, ll in enumerate(line_lengths):
        cumulative[i + 1] = cumulative[i] + ll

    matches = []
    search_text = '\n'.join(search_lines)

    for i in range(len(content_lines_raw) - num_search + 1):
        candidate_lines = [l.rstrip() for l in content_lines_raw[i:i + num_search]]
        candidate_text = '\n'.join(candidate_lines)

        # Quick length check to skip obviously bad candidates
        len_ratio = min(len(search_text), len(candidate_text)) / max(
            len(search_text), len(candidate_text), 1
        )
        if len_ratio < threshold * 0.8:
            continue

        ratio = difflib.SequenceMatcher(None, search_text, candidate_text).ratio()
        if ratio >= threshold:
            start_offset = cumulative[i]
            end_offset = cumulative[i + num_search] - 1
            matches.append((start_offset, end_offset, ratio))

    if not matches:
        return []

    # Sort by confidence descending
    matches.sort(key=lambda x: x[2], reverse=True)

    # Remove overlapping matches - keep best per region
    filtered = []
    for m in matches:
        overlaps = False
        for existing in filtered:
            overlap_start = max(m[0], existing[0])
            overlap_end = min(m[1], existing[1])
            if overlap_end > overlap_start:
                overlap_ratio = (overlap_end - overlap_start) / max(
                    min(m[1] - m[0], existing[1] - existing[0]), 1
                )
                if overlap_ratio > 0.5:
                    overlaps = True
                    break
        if not overlaps:
            filtered.append(m)

    return filtered


def _find_fuzzy_char_level(
    content: str, search: str, threshold: float
) -> List[Tuple[int, int, float]]:
    """
    Character-level fuzzy match for very short search strings.
    Uses a sliding window approach.
    """
    search_len = len(search)
    if search_len == 0:
        return []

    window_sizes = [search_len]
    if search_len > 10:
        window_sizes.append(int(search_len * 0.9))
    window_sizes.append(int(search_len * 1.1))

    matches = []
    seen_ranges = set()

    for window_size in window_sizes:
        if window_size < 1 or window_size > len(content):
            continue
        step = max(1, window_size // 2)
        for start in range(0, len(content) - window_size + 1, step):
            candidate = content[start:start + window_size]
            ratio = difflib.SequenceMatcher(None, search, candidate).ratio()
            if ratio >= threshold:
                end = start + window_size
                range_key = (start, end)
                if range_key not in seen_ranges:
                    seen_ranges.add(range_key)
                    matches.append((start, end, ratio))

    if not matches:
        return []

    matches.sort(key=lambda x: x[2], reverse=True)

    # Deduplicate overlapping
    filtered = []
    for m in matches:
        overlaps = False
        for existing in filtered:
            overlap_start = max(m[0], existing[0])
            overlap_end = min(m[1], existing[1])
            if overlap_end > overlap_start:
                overlap_ratio = (overlap_end - overlap_start) / max(
                    min(m[1] - m[0], existing[1] - existing[0]), 1
                )
                if overlap_ratio > 0.5:
                    overlaps = True
                    break
        if not overlaps:
            filtered.append(m)

    return filtered


# ---------------------------------------------------------------------------
# Context matching helpers
# ---------------------------------------------------------------------------

def _validate_context(
    content: str,
    match_start: int,
    match_end: int,
    context_before: Optional[str],
    context_after: Optional[str]
) -> Tuple[bool, dict]:
    """Validate that context_before/context_after match the file around the target.

    Returns (is_valid, evidence_dict).
    Context increases confidence only when it actually matches.
    """
    evidence = {}
    lines = content.split('\n')
    match_start_line = content.count('\n', 0, match_start)
    match_end_line = content.count('\n', 0, match_end)

    context_before_valid = True
    context_after_valid = True

    if context_before:
        ctx_lines = context_before.rstrip('\n').split('\n')
        num_ctx = len(ctx_lines)
        ctx_start_line = match_start_line - num_ctx

        if ctx_start_line < 0:
            context_before_valid = False
            evidence["context_before"] = "not_enough_lines_before"
        else:
            file_ctx = lines[ctx_start_line:match_start_line]
            # Compare stripped versions
            file_ctx_stripped = [l.strip() for l in file_ctx]
            search_ctx_stripped = [l.strip() for l in ctx_lines]
            if file_ctx_stripped == search_ctx_stripped:
                context_before_valid = True
                evidence["context_before"] = "matched"
            else:
                context_before_valid = False
                evidence["context_before"] = "mismatch"

    if context_after:
        ctx_lines = context_after.rstrip('\n').split('\n')
        num_ctx = len(ctx_lines)
        ctx_start_line = match_end_line + 1
        ctx_end_line = ctx_start_line + num_ctx

        if ctx_end_line > len(lines):
            context_after_valid = False
            evidence["context_after"] = "not_enough_lines_after"
        else:
            file_ctx = lines[ctx_start_line:ctx_end_line]
            file_ctx_stripped = [l.strip() for l in file_ctx]
            search_ctx_stripped = [l.strip() for l in ctx_lines]
            if file_ctx_stripped == search_ctx_stripped:
                context_after_valid = True
                evidence["context_after"] = "matched"
            else:
                context_after_valid = False
                evidence["context_after"] = "mismatch"

    is_valid = context_before_valid and context_after_valid
    return is_valid, evidence


def _filter_matches_by_context(
    content: str,
    matches: List[Tuple[int, int]],
    context_before: Optional[str],
    context_after: Optional[str]
) -> List[Tuple[int, int]]:
    """Filter match candidates using context_before/context_after.

    Only keeps matches where the surrounding context actually matches.
    """
    if not context_before and not context_after:
        return matches

    filtered = []
    for start, end in matches:
        is_valid, _ = _validate_context(
            content, start, end, context_before, context_after
        )
        if is_valid:
            filtered.append((start, end))

    return filtered


# ---------------------------------------------------------------------------
# Unified Matching Engine (returns structured MatchResult)
# ---------------------------------------------------------------------------

def resolve_target(
    content: str,
    search: str,
    context_before: Optional[str] = None,
    context_after: Optional[str] = None,
) -> MatchResult:
    """
    Unified matching engine. Searches through all layers and returns
    a structured MatchResult with full evidence.

    Safety rules:
    - Exact/indentation/whitespace/operator matches are considered safe.
    - Fuzzy matches are NEVER safe for auto-editing (candidates only).
    - Multiple matches at any layer -> ambiguous_match error.
    """
    if not search or not content:
        return MatchResult(
            found=False,
            error="empty_search" if not search else "empty_content",
            message="Search text or file content is empty.",
            suggested_action="read_file",
        )

    # Layer 1: Exact match
    positions = _find_exact_matches(content, search)
    if positions:
        if context_before or context_after:
            positions_tuples = [(p, p + len(search)) for p in positions]
            positions_tuples = _filter_matches_by_context(
                content, positions_tuples, context_before, context_after
            )
            positions = [p for p, _ in positions_tuples]

        if len(positions) == 1:
            start = positions[0]
            end = start + len(search)
            return MatchResult(
                found=True,
                start=start,
                end=end,
                strategy="exact",
                confidence=1.0,
                match_count=1,
                safe=True,
                evidence={"layer": 1, "method": "substring"},
            )
        elif len(positions) > 1:
            candidates = _build_candidates(content, [(p, p + len(search)) for p in positions], "exact", 1.0)
            return MatchResult(
                found=False,
                strategy="exact",
                confidence=1.0,
                match_count=len(positions),
                safe=False,
                candidates=candidates,
                error="ambiguous_match",
                message=f"Found {len(positions)} exact matches. Cannot determine which one to edit.",
                hint="Provide more surrounding context (context_before/context_after) "
                     "or include more of the function/class body to uniquely identify the target.",
                suggested_action="read_file",
            )

    # Layer 2: Line-ending normalized
    le_matches = _find_line_ending_normalized_matches(content, search)
    if le_matches:
        if context_before or context_after:
            le_matches = _filter_matches_by_context(
                content, le_matches, context_before, context_after
            )
        if len(le_matches) == 1:
            return MatchResult(
                found=True,
                start=le_matches[0][0],
                end=le_matches[0][1],
                strategy="line_ending_normalized",
                confidence=0.98,
                match_count=1,
                safe=True,
                evidence={"layer": 2, "method": "line_ending_normalization"},
            )
        elif len(le_matches) > 1:
            candidates = _build_candidates(content, le_matches, "line_ending_normalized", 0.98)
            return MatchResult(
                found=False,
                strategy="line_ending_normalized",
                confidence=0.98,
                match_count=len(le_matches),
                safe=False,
                candidates=candidates,
                error="ambiguous_match",
                message=f"Found {len(le_matches)} matches using line-ending-normalized strategy.",
                hint="Provide context_before/context_after or more surrounding code to disambiguate.",
                suggested_action="read_file",
            )

    # Layer 3: Indentation-normalized
    indent_matches = _find_indentation_normalized_matches(content, search)
    if indent_matches:
        if context_before or context_after:
            indent_matches = _filter_matches_by_context(
                content, indent_matches, context_before, context_after
            )

        if len(indent_matches) == 1:
            return MatchResult(
                found=True,
                start=indent_matches[0][0],
                end=indent_matches[0][1],
                strategy="indentation_normalized",
                confidence=0.95,
                match_count=1,
                safe=True,
                evidence={"layer": 3, "method": "line_stripped_comparison"},
            )
        elif len(indent_matches) > 1:
            candidates = _build_candidates(content, indent_matches, "indentation_normalized", 0.95)
            return MatchResult(
                found=False,
                strategy="indentation_normalized",
                confidence=0.95,
                match_count=len(indent_matches),
                safe=False,
                candidates=candidates,
                error="ambiguous_match",
                message=f"Found {len(indent_matches)} matches using indentation-normalized strategy.",
                hint="Provide context_before/context_after or more surrounding code to disambiguate.",
                suggested_action="read_file",
            )

    # Layer 4: Whitespace-normalized
    ws_matches = _find_whitespace_normalized_matches(content, search)
    if ws_matches:
        if context_before or context_after:
            ws_matches = _filter_matches_by_context(
                content, ws_matches, context_before, context_after
            )

        if len(ws_matches) == 1:
            return MatchResult(
                found=True,
                start=ws_matches[0][0],
                end=ws_matches[0][1],
                strategy="whitespace_normalized",
                confidence=0.92,
                match_count=1,
                safe=True,
                evidence={"layer": 4, "method": "whitespace_collapsed_comparison"},
            )
        elif len(ws_matches) > 1:
            candidates = _build_candidates(content, ws_matches, "whitespace_normalized", 0.92)
            return MatchResult(
                found=False,
                strategy="whitespace_normalized",
                confidence=0.92,
                match_count=len(ws_matches),
                safe=False,
                candidates=candidates,
                error="ambiguous_match",
                message=f"Found {len(ws_matches)} matches using whitespace-normalized strategy.",
                hint="Provide context_before/context_after or more surrounding code to disambiguate.",
                suggested_action="read_file",
            )

    # Layer 5: Operator-spacing normalized
    op_matches = _find_operator_spacing_matches(content, search)
    if op_matches:
        if context_before or context_after:
            op_matches = _filter_matches_by_context(
                content, op_matches, context_before, context_after
            )

        if len(op_matches) == 1:
            return MatchResult(
                found=True,
                start=op_matches[0][0],
                end=op_matches[0][1],
                strategy="operator_spacing_normalized",
                confidence=0.90,
                match_count=1,
                safe=True,
                evidence={"layer": 5, "method": "operator_spacing_comparison"},
            )
        elif len(op_matches) > 1:
            candidates = _build_candidates(content, op_matches, "operator_spacing_normalized", 0.90)
            return MatchResult(
                found=False,
                strategy="operator_spacing_normalized",
                confidence=0.90,
                match_count=len(op_matches),
                safe=False,
                candidates=candidates,
                error="ambiguous_match",
                message=f"Found {len(op_matches)} matches using operator-spacing-normalized strategy.",
                hint="Provide context_before/context_after or more surrounding code to disambiguate.",
                suggested_action="read_file",
            )

    # Layer 6: Structural/language-aware matching [Extension Point]
    # Future: Add language-aware structural matching here.
    # This would use AST parsing to match code structure regardless of formatting.
    # For now, this layer is a placeholder that passes through to fuzzy.

    # Layer 7: Fuzzy candidate discovery (NEVER auto-edits)
    fuzzy_matches = _find_fuzzy_matches(content, search)
    if fuzzy_matches:
        candidates = [
            MatchCandidate(
                start_offset=s,
                end_offset=e,
                start_line=_offset_to_line(content, s),
                end_line=_offset_to_line(content, e),
                confidence=conf,
                strategy="fuzzy",
            )
            for s, e, conf in fuzzy_matches
        ]
        return MatchResult(
            found=False,
            strategy="fuzzy",
            confidence=fuzzy_matches[0][2] if fuzzy_matches else 0.0,
            match_count=len(fuzzy_matches),
            safe=False,  # NEVER safe for auto-editing
            candidates=candidates,
            error="unsafe_match",
            message="Fuzzy matching found possible target(s), but automatic editing "
                    "is disabled for fuzzy-only matches. The match is not safe enough "
                    "for automatic modification.",
            hint="Read the file at the indicated lines and retry with the exact code, "
                 "or provide context_before/context_after to confirm the target.",
            suggested_action="read_file",
            evidence={
                "layer": 7,
                "method": "sequence_matcher",
                "threshold": FUZZY_THRESHOLD,
            },
        )

    # No match found at any layer
    return MatchResult(
        found=False,
        strategy="none",
        confidence=0.0,
        match_count=0,
        safe=False,
        error="no_match",
        message="The search text was not found in the file using any matching strategy "
                "(exact, indentation-normalized, whitespace-normalized, "
                "operator-normalized, fuzzy).",
        hint="Verify the search text matches the actual file content. "
             "Use read_file to inspect the file first.",
        suggested_action="read_file",
    )


def _build_candidates(
    content: str,
    matches: List[Tuple[int, int]],
    strategy: str,
    confidence: float
) -> List[MatchCandidate]:
    """Build MatchCandidate list from offset tuples."""
    return [
        MatchCandidate(
            start_offset=s,
            end_offset=e,
            start_line=_offset_to_line(content, s),
            end_line=_offset_to_line(content, e),
            confidence=confidence,
            strategy=strategy,
        )
        for s, e in matches
    ]


# ---------------------------------------------------------------------------
# Edit Plan Construction
# ---------------------------------------------------------------------------

def create_edit_plan(
    file_path: str,
    content: str,
    match: MatchResult,
    operation: str,
    replacement: Optional[str],
) -> EditPlan:
    """Create an edit plan from a resolved match.

    This separates matching from modification. The plan computes the
    new content in memory without writing to disk.
    """
    plan = EditPlan(
        file_path=file_path,
        operation=operation,
        match=match,
        original_content=content,
        replacement_text=replacement,
    )

    if not match.found or match.start is None or match.end is None:
        return plan

    start = match.start
    end = match.end

    if operation == 'replace':
        # Determine target indentation from the matched region
        matched_text = content[start:end]
        matched_first_line_start = content.rfind('\n', 0, start) + 1
        target_indent = _get_indentation(content[matched_first_line_start:start + len(matched_text.split('\n')[0])])

        # Re-indent replacement to match target
        if replacement:
            reindented = _reindent_replacement(replacement, target_indent, matched_text)
        else:
            reindented = ""

        plan.new_content = content[:start] + reindented + content[end:]
        plan.replacement_text = reindented



    # Compute diff stats
    if plan.new_content is not None:
        old_lines = content.split('\n')
        new_lines = plan.new_content.split('\n')
        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            lineterm='', n=3
        ))
        plan.diff_preview = '\n'.join(diff) if diff else None
        plan.lines_added = sum(1 for l in diff if l.startswith('+') and not l.startswith('+++'))
        plan.lines_removed = sum(1 for l in diff if l.startswith('-') and not l.startswith('---'))

    return plan


# ---------------------------------------------------------------------------
# Diff generation
# ---------------------------------------------------------------------------

def generate_diff(
    original: str,
    modified: str,
    file_path: str = "file"
) -> str:
    """Generate a unified diff between original and modified content."""
    old_lines = original.splitlines(keepends=True)
    new_lines = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
    )
    return ''.join(diff)


# ---------------------------------------------------------------------------
# Validation pipeline
# ---------------------------------------------------------------------------

def validate_candidate(
    file_path: str,
    content: str,
    validators: Optional[List[CodeValidator]] = None,
) -> dict:
    """Run validation pipeline on candidate content.

    Returns a dict with validation results:
    {
        "syntax": "passed" | "failed" | "not_run",
        "lint": "not_run",
        "tests": "not_run",
        "details": [...]
    }
    """
    result = {
        "syntax": "not_run",
        "lint": "not_run",
        "tests": "not_run",
        "details": [],
    }

    if not validators:
        return result

    for validator in validators:
        try:
            vr = validator.validate(file_path, content)
            if not vr.passed:
                result["syntax"] = "failed"
                result["details"].append(vr.to_dict())
                return result
            else:
                result["syntax"] = "passed"
                result["details"].append(vr.to_dict())
        except Exception as e:
            result["syntax"] = "failed"
            result["details"].append({
                "validator": type(validator).__name__,
                "passed": False,
                "message": f"Validator error: {e}",
            })
            return result

    return result


# ---------------------------------------------------------------------------
# Context helper
# ---------------------------------------------------------------------------

def _get_context_lines(content: str, start: int, end: int, context: int = 3) -> str:
    """Get surrounding context lines for preview."""
    lines = content.split('\n')
    start_line = content.count('\n', 0, start)
    end_line = content.count('\n', 0, end)

    ctx_start = max(0, start_line - context)
    ctx_end = min(len(lines), end_line + context + 1)

    numbered = []
    for i in range(ctx_start, ctx_end):
        marker = '>>>' if start_line <= i <= end_line else '   '
        numbered.append(f"{marker} {i + 1:4d} | {lines[i]}")

    return '\n'.join(numbered)


# ---------------------------------------------------------------------------
# Main API: edit_file
# ---------------------------------------------------------------------------

def edit_file(
    file_path: str,
    search: Optional[str] = None,
    replace: Optional[str] = None,
    operation: str = "replace",
    context_before: Optional[str] = None,
    context_after: Optional[str] = None,
    symbol: Optional[str] = None,
    dry_run: bool = False,
    max_file_size: int = MAX_FILE_SIZE,
    validators: Optional[List[CodeValidator]] = None,
) -> EditResult:
    """
    Perform a replace edit operation on a file.

    Args:
        file_path:       Absolute or relative path to the file.
        search:          The text to search for in the file.
        replace:         The replacement text.
        operation:       Must be 'replace' (only supported operation).
        context_before:  Optional context lines that should appear before the target.
        context_after:   Optional context lines that should appear after the target.
        symbol:          Optional symbol path (e.g., 'Class.method') to scope the search.
        dry_run:         If True, compute the edit but don't write to disk.
        max_file_size:   Maximum allowed file size in bytes.
        validators:      Optional list of CodeValidator instances for candidate validation.

    Returns:
        EditResult with structured success/failure information.
    """
    valid_ops = ('replace',)
    if operation not in valid_ops:
        return EditResult(
            success=False,
            error="invalid_operation",
            message=f"Unknown operation '{operation}'. Only 'replace' is supported.",
            suggested_action="check_documentation",
        )

    # --- Validate inputs ---
    if not search:
        return EditResult(
            success=False,
            error="empty_search",
            message="Search text is empty. Provide the code block you want to target.",
            suggested_action="provide_search_text",
        )

    if replace is None:
        return EditResult(
            success=False,
            error="missing_replace",
            message="Replace text is required for 'replace' operation.",
            suggested_action="provide_replace_text",
        )





    # --- Read file ---
    try:
        path = Path(file_path)
        if not path.exists():
            return EditResult(
                success=False,
                error="file_not_found",
                message=f"File not found: {file_path}",
                hint="Check the file path. Use list_directory or glob to find the correct path.",
                suggested_action="verify_path",
            )

        file_size = path.stat().st_size
        if file_size > max_file_size:
            return EditResult(
                success=False,
                error="file_too_large",
                message=f"File is {file_size:,} bytes (limit: {max_file_size:,}). "
                        f"Consider editing a smaller section or splitting the file.",
                suggested_action="edit_smaller_section",
            )

        # Read with newline='' to preserve original line endings for detection
        with open(path, 'r', encoding='utf-8', newline='') as f:
            content = f.read()
    except PermissionError:
        return EditResult(
            success=False,
            error="permission_denied",
            message=f"Permission denied reading file: {file_path}",
            suggested_action="check_permissions",
        )
    except UnicodeDecodeError:
        return EditResult(
            success=False,
            error="encoding_error",
            message=f"File is not valid UTF-8: {file_path}",
            suggested_action="verify_encoding",
        )
    except OSError as e:
        return EditResult(
            success=False,
            error="os_error",
            message=f"OS error reading file: {e}",
            suggested_action="check_file_system",
        )

    # Detect line ending for preservation
    line_ending = _detect_line_ending(content)

    # Normalize content to LF for matching
    content_lf = _normalize_to_lf(content)
    search_lf = _normalize_to_lf(search) if search else None

    # --- Symbol-aware scoping (optional, Phase 3 extension point) ---
    if symbol:
        scoped_content = _resolve_symbol_scope(content_lf, symbol)
        if scoped_content is None:
            return EditResult(
                success=False,
                error="symbol_not_found",
                message=f"Could not resolve symbol '{symbol}' in the file.",
                hint="Verify the symbol name. Use format 'ClassName.method_name' for methods.",
                suggested_action="read_file",
            )
        # For now, we search within the full content but could narrow scope
        # This is an extension point for future enhancement

    # --- Resolve target using unified matching engine ---
    match = resolve_target(
        content_lf, search_lf,
        context_before=_normalize_to_lf(context_before) if context_before else None,
        context_after=_normalize_to_lf(context_after) if context_after else None,
    )

    # --- Handle match failures ---
    if not match.found:
        candidates_dicts = [c.to_dict() for c in match.candidates] if match.candidates else []

        if match.error == "no_match":
            hint = _build_no_match_hint(content_lf, search_lf)
            return EditResult(
                success=False,
                error="no_match",
                message=match.message,
                hint=hint,
                suggested_action="read_file",
            )
        elif match.error == "ambiguous_match":
            return EditResult(
                success=False,
                error="ambiguous_match",
                message=match.message,
                hint=match.hint,
                suggested_action="read_file",
                match_count=match.match_count,
                candidates=candidates_dicts,
            )
        elif match.error == "unsafe_match":
            return EditResult(
                success=False,
                error="unsafe_match",
                match_type="fuzzy",
                confidence=match.confidence,
                message=match.message,
                hint=match.hint,
                suggested_action="read_file",
                match_count=match.match_count,
                candidates=candidates_dicts,
            )
        else:
            return EditResult(
                success=False,
                error=match.error or "unknown_error",
                message=match.message or "Matching failed.",
                hint=match.hint,
                suggested_action=match.suggested_action,
            )

    # --- Create edit plan (separates matching from modification) ---
    plan = create_edit_plan(
        file_path=file_path,
        content=content_lf,
        match=match,
        operation=operation,
        replacement=replace,
    )

    if not plan.is_valid:
        return EditResult(
            success=False,
            error="plan_failed",
            message="Failed to construct edit plan from the resolved match.",
            suggested_action="read_file",
        )

    # --- Validate candidate before writing ---
    validation_result = validate_candidate(
        file_path, plan.new_content, validators
    )

    if validation_result.get("syntax") == "failed":
        return EditResult(
            success=False,
            error="validation_failed",
            message="The candidate file content failed validation. "
                    "The edit was NOT applied.",
            hint="The replacement code may introduce syntax errors. "
                 "Review the replacement and ensure it is valid code.",
            suggested_action="fix_replacement",
            validation=validation_result,
            diff_preview=plan.diff_preview,
        )

    # --- Apply the edit ---
    try:
        new_content = plan.new_content

        # Restore original line endings
        if line_ending != '\n':
            new_content = new_content.replace('\n', line_ending)

        # Build preview
        preview_before = _get_context_lines(content_lf, match.start, match.end)
        preview_after = None

        if not dry_run:
            _atomic_write(path, new_content)

            # Re-read to confirm
            preview_after = _get_context_lines(
                _normalize_to_lf(new_content),
                match.start,
                match.start + len(plan.replacement_text or "")
            )

        start_line = _offset_to_line(content_lf, match.start)
        end_line = _offset_to_line(content_lf, match.end)

        return EditResult(
            success=True,
            match_type=match.strategy,
            confidence=match.confidence,
            operations_applied=1,
            start_line=start_line,
            end_line=end_line,
            preview_before=preview_before,
            preview_after=preview_after,
            candidates=[],
            edit_applied=not dry_run,
            validation=validation_result,
            diff_preview=plan.diff_preview,
            lines_added=plan.lines_added,
            lines_removed=plan.lines_removed,
            message=f"{'[DRY RUN] ' if dry_run else ''}Successfully {operation}d text "
                    f"at lines {start_line}-{end_line} ({match.strategy} match, "
                    f"{match.confidence:.0%} confidence)."
        )

    except PermissionError:
        return EditResult(
            success=False,
            error="permission_denied",
            message=f"Permission denied writing file: {file_path}",
            suggested_action="check_permissions",
        )
    except OSError as e:
        return EditResult(
            success=False,
            error="os_error",
            message=f"OS error writing file: {e}",
            suggested_action="check_file_system",
        )


# ---------------------------------------------------------------------------
# Symbol resolution (Phase 3 extension point)
# ---------------------------------------------------------------------------

def _resolve_symbol_scope(content: str, symbol: str) -> Optional[Tuple[int, int]]:
    """Resolve a symbol path to a scope (start, end) in the content.

    Supports basic Python symbol resolution:
    - 'function_name' -> finds the function definition
    - 'ClassName.method_name' -> finds the method within the class

    Returns (start_offset, end_offset) or None if not found.
    This is an extension point for future language-aware implementations.
    """
    parts = symbol.split('.')

    if len(parts) == 1:
        # Simple function/class name
        pattern = re.compile(
            r'^(\s*)(?:def|class)\s+' + re.escape(parts[0]) + r'\s*[\(:]',
            re.MULTILINE
        )
        m = pattern.search(content)
        if m:
            return (m.start(), len(content))  # Scope extends to end (simplified)
        return None

    elif len(parts) == 2:
        # ClassName.method_name
        class_pattern = re.compile(
            r'^(\s*)class\s+' + re.escape(parts[0]) + r'\s*[\(:]',
            re.MULTILINE
        )
        class_match = class_pattern.search(content)
        if not class_match:
            return None

        # Search for method after class definition
        method_pattern = re.compile(
            r'^(\s*)def\s+' + re.escape(parts[1]) + r'\s*\(',
            re.MULTILINE
        )
        method_match = method_pattern.search(content, class_match.end())
        if method_match:
            return (method_match.start(), len(content))
        return None

    return None


# ---------------------------------------------------------------------------
# Operation handlers
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Hint builder for no-match errors
# ---------------------------------------------------------------------------

def _build_no_match_hint(content: str, search: str) -> str:
    """Build a helpful hint when no match is found."""
    hints = [
        "Verify the search text matches the actual file content exactly.",
        "Use read_file to inspect the file first.",
    ]

    # Check if search lines exist partially
    search_lines = search.strip().split('\n')
    content_lines = content.split('\n')

    if search_lines:
        first_line = search_lines[0].strip()
        if first_line:
            partial_matches = [
                i + 1 for i, line in enumerate(content_lines)
                if first_line in line.strip()
            ]
            if partial_matches:
                hints.append(
                    f"The first line of your search text appears near line(s) "
                    f"{partial_matches[:5]}. Check if surrounding lines differ."
                )

    # Check for common issues
    if '\t' in search and '\t' not in content:
        hints.append(
            "Your search text contains tabs but the file uses spaces. "
            "Match the file's indentation style."
        )
    elif '    ' in search and '\t' in content and '    ' not in content:
        hints.append(
            "Your search text uses spaces but the file uses tabs. "
            "Match the file's indentation style."
        )

    return ' '.join(hints)


# ---------------------------------------------------------------------------
# Legacy-compatible interface
# ---------------------------------------------------------------------------

def replace_code(file_path: str, old_code: str, new_code: str) -> dict:
    """
    Replace the first unique occurrence of old_code with new_code in the file.

    This is the enhanced replacement for the legacy replace_code function.
    Returns a structured dict instead of True/False.
    """
    result = edit_file(
        file_path=file_path,
        search=old_code,
        replace=new_code,
        operation="replace"
    )
    return result.to_dict()
