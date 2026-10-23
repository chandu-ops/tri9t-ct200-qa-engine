import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional
from pypdf import PdfReader

@dataclass
class ParsedSection:
    section_number: Optional[str]
    heading: str
    level: int
    body_lines: List[str] = field(default_factory=list)
    tables_markdown: List[str] = field(default_factory=list)
    figures_captions: List[str] = field(default_factory=list)
    parent_index: Optional[int] = None
    canonical_path: str = ""

    @property
    def body_text(self) -> str:
        return "\n".join(line for line in self.body_lines if line.strip()).strip()

    @property
    def content_hash(self) -> str:
        normalized = re.sub(r"\s+", " ", f"{self.heading.strip()} || {self.body_text} || {' '.join(self.tables_markdown)}").strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

NUMBERED_HEADING_RE = re.compile(r"^(?P<num>\d+(?:\.\d+)*)\.?\s+(?P<title>[A-Z][A-Za-z0-9\s\-\/\(\),:]{2,80})$")
UNNUMBERED_HEADING_RE = re.compile(r"^(?P<title>(?:SECTION\s+\d+\s*[:\-]\s*)?[A-Z][A-Z0-9\s\-\/]{3,60})$")
CAPTION_RE = re.compile(r"^(?:Figure|Fig\.|Table)\s+\d+[\.\-:]?\s+.+$", re.IGNORECASE)
PAGE_ARTIFACT_RE = re.compile(r"^(?:Page\s+\d+(?:\s+of\s+\d+)?|CardioTrack\s+CT-200\s+Manual|CONFIDENTIAL.*)$", re.IGNORECASE)

def parse_hierarchy_from_lines(lines: List[str]) -> List[ParsedSection]:
    sections: List[ParsedSection] = []
    ancestry_stack: List[tuple[int, int]] = []

    def push_section(sec_num: Optional[str], title: str, level: int):
        while ancestry_stack and ancestry_stack[-1][0] >= level:
            ancestry_stack.pop()
        parent_idx = ancestry_stack[-1][1] if ancestry_stack else None
        parent_path = sections[parent_idx].canonical_path if parent_idx is not None else "ROOT"
        base_segment = f"{sec_num + ' ' if sec_num else ''}{title.strip()}"
        candidate_path = f"{parent_path} > {base_segment}"
        sibling_collision_count = sum(1 for s in sections if s.canonical_path == candidate_path)
        if sibling_collision_count > 0:
            candidate_path = f"{candidate_path} [{sibling_collision_count + 1}]"
        sec = ParsedSection(
            section_number=sec_num,
            heading=title.strip(),
            level=level,
            parent_index=parent_idx,
            canonical_path=candidate_path,
        )
        idx = len(sections)
        sections.append(sec)
        ancestry_stack.append((level, idx))

    for raw_line in lines:
        line = raw_line.strip()
        if not line or PAGE_ARTIFACT_RE.match(line):
            continue
        m_num = NUMBERED_HEADING_RE.match(line)
        if m_num:
            sec_num = m_num.group("num")
            title = m_num.group("title").strip()
            level = len(sec_num.split("."))
            push_section(sec_num, title, level)
            continue
        m_unnum = UNNUMBERED_HEADING_RE.match(line)
        if m_unnum and len(line.split()) <= 7 and not line.endswith("."):
            title = m_unnum.group("title").title()
            current_numbered_level = 1
            for lvl, s_idx in reversed(ancestry_stack):
                if sections[s_idx].section_number is not None:
                    current_numbered_level = lvl + 1
                    break
            push_section(None, title, current_numbered_level)
            continue
        if not sections:
            push_section("0", "Document Preamble", 1)
        sections[-1].body_lines.append(line)
    return sections

def parse_pdf_document(pdf_path: str) -> List[ParsedSection]:
    reader = PdfReader(pdf_path)
    all_lines = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            for line in text.splitlines():
                if line.strip():
                    all_lines.append(line.strip())
    return parse_hierarchy_from_lines(all_lines)