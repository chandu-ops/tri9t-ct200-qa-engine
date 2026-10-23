import difflib
import re
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from app.models import DocumentVersion, LogicalNode, NodeVersion
from app.parser import ParsedSection

NUMERIC_UNIT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mmHg|s|sec|seconds|ms|bpm|kPa|%|E\d+)\b", re.IGNORECASE)

def normalize_heading_key(path: str) -> str:
    return re.sub(r"\s+", " ", path.lower()).strip()

def find_best_logical_node_match(
    db: Session, parsed: ParsedSection, existing_nodes: List[LogicalNode], claimed_ids: set
) -> Optional[LogicalNode]:
    for node in existing_nodes:
        if node.id not in claimed_ids and normalize_heading_key(node.canonical_path) == normalize_heading_key(parsed.canonical_path):
            return node
    best_node = None
    best_ratio = 0.0
    for node in existing_nodes:
        if node.id in claimed_ids:
            continue
        ratio = difflib.SequenceMatcher(
            None, normalize_heading_key(node.canonical_path), normalize_heading_key(parsed.canonical_path)
        ).ratio()
        if ratio >= 0.86 and ratio > best_ratio:
            best_ratio = ratio
            best_node = node
    return best_node

def ingest_parsed_sections(
    db: Session, doc_name: str, version_number: int, source_filename: str, sections: List[ParsedSection]
) -> DocumentVersion:
    existing_ver = db.query(DocumentVersion).filter_by(version_number=version_number).first()
    if existing_ver:
        return existing_ver
    doc_ver = DocumentVersion(doc_name=doc_name, version_number=version_number, source_filename=source_filename)
    db.add(doc_ver)
    db.flush()
    existing_logical_nodes = db.query(LogicalNode).all()
    claimed_logical_ids = set()
    index_to_logical_id: Dict[int, str] = {}
    for idx, sec in enumerate(sections):
        matched_logical = find_best_logical_node_match(db, sec, existing_logical_nodes, claimed_logical_ids)
        if matched_logical is None:
            matched_logical = LogicalNode(
                canonical_path=sec.canonical_path,
                created_in_version=version_number
            )
            db.add(matched_logical)
            db.flush()
            change_status = "ADDED"
        else:
            claimed_logical_ids.add(matched_logical.id)
            prev_nv = (
                db.query(NodeVersion)
                .filter(NodeVersion.logical_node_id == matched_logical.id, NodeVersion.version_number < version_number)
                .order_by(NodeVersion.version_number.desc())
                .first()
            )
            if prev_nv and prev_nv.content_hash == sec.content_hash:
                change_status = "UNCHANGED"
            else:
                change_status = "MODIFIED"
        index_to_logical_id[idx] = matched_logical.id
        parent_logical_id = index_to_logical_id.get(sec.parent_index) if sec.parent_index is not None else None
        nv = NodeVersion(
            logical_node_id=matched_logical.id,
            version_number=version_number,
            parent_logical_id=parent_logical_id,
            section_number=sec.section_number,
            heading=sec.heading,
            level=sec.level,
            body_text=sec.body_text,
            tables_markdown="\n".join(sec.tables_markdown) if sec.tables_markdown else None,
            figures_captions="\n".join(sec.figures_captions) if sec.figures_captions else None,
            content_hash=sec.content_hash,
            change_status=change_status,
        )
        db.add(nv)
    db.commit()
    db.refresh(doc_ver)
    return doc_ver

def compute_node_diff(nv_old: Optional[NodeVersion], nv_new: Optional[NodeVersion]) -> dict:
    if not nv_old and nv_new:
        return {
            "changed": True,
            "change_status": "ADDED",
            "diff_summary": f"Section '{nv_new.heading}' was newly added in v{nv_new.version_number}.",
            "numeric_threshold_changed": bool(NUMERIC_UNIT_RE.search(nv_new.body_text)),
        }
    if nv_old and not nv_new:
        return {
            "changed": True,
            "change_status": "DELETED",
            "diff_summary": f"Section '{nv_old.heading}' present in v{nv_old.version_number} was removed.",
            "numeric_threshold_changed": True,
        }
    if nv_old.content_hash == nv_new.content_hash:
        return {
            "changed": False,
            "change_status": "UNCHANGED",
            "diff_summary": "No semantic or textual changes across versions.",
            "numeric_threshold_changed": False,
        }
    old_units = set(NUMERIC_UNIT_RE.findall(nv_old.body_text))
    new_units = set(NUMERIC_UNIT_RE.findall(nv_new.body_text))
    numeric_changed = old_units != new_units
    diff_lines = list(
        difflib.unified_diff(
            nv_old.body_text.splitlines(),
            nv_new.body_text.splitlines(),
            fromfile=f"v{nv_old.version_number}",
            tofile=f"v{nv_new.version_number}",
            lineterm="",
        )
    )
    compact_diff = "\n".join(diff_lines[2:12])
    severity_note = (
        "[CRITICAL PARAMETER CHANGE: Numeric/clinical thresholds modified] "
        if numeric_changed
        else "[EDITORIAL/TEXT CHANGE: Wording updated without threshold change] "
    )
    return {
        "changed": True,
        "change_status": "MODIFIED",
        "diff_summary": severity_note + compact_diff,
        "numeric_threshold_changed": numeric_changed,
    }
