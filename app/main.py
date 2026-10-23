import hashlib
import uuid
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import Base, engine, get_db, nosql_store
from app.models import DocumentVersion, LogicalNode, NodeVersion, Selection, SelectionItem
from app.schemas import (
    NodeSummaryResponse,
    NodeDetailResponse,
    NodeDiffResponse,
    SelectionCreateRequest,
    GenerateRequest,
)
from app.parser import parse_pdf_document
from app.versioning import ingest_parsed_sections, compute_node_diff
from app.llm_service import generate_and_validate_test_cases

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Tri9T AI - CardioTrack CT-200 Traceable QA & Versioning API",
    version="1.0.0",
)

def get_latest_version_number(db: Session) -> int:
    latest = db.query(func.max(DocumentVersion.version_number)).scalar()
    if not latest:
        raise HTTPException(status_code=404, detail="No document versions ingested yet.")
    return latest

@app.post("/ingest", summary="Ingest or re-ingest a PDF version (v1, v2, etc.)")
def ingest_pdf(
    pdf_path: str = Query(..., description="Path to PDF file, e.g. data/ct200_manual_v1.pdf"),
    version_number: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    sections = parse_pdf_document(pdf_path)
    doc_ver = ingest_parsed_sections(db, "CT-200", version_number, pdf_path, sections)
    node_count = db.query(NodeVersion).filter_by(version_number=version_number).count()
    return {
        "document": doc_ver.doc_name,
        "version_number": doc_ver.version_number,
        "nodes_ingested": node_count,
        "source_filename": doc_ver.source_filename,
    }

@app.get("/nodes/top-level", response_model=List[NodeSummaryResponse], summary="List top-level sections")
def list_top_level_sections(
    version: Optional[int] = Query(None, description="Defaults to latest ingested version"),
    db: Session = Depends(get_db),
):
    ver = version or get_latest_version_number(db)
    nodes = (
        db.query(NodeVersion)
        .filter(NodeVersion.version_number == ver, NodeVersion.parent_logical_id.is_(None))
        .all()
    )
    return [
        NodeSummaryResponse(
            logical_node_id=n.logical_node_id,
            node_version_id=n.id,
            version_number=n.version_number,
            section_number=n.section_number,
            heading=n.heading,
            level=n.level,
            content_hash=n.content_hash,
            change_status=n.change_status,
        )
        for n in nodes
    ]

@app.get("/nodes/search", response_model=List[NodeSummaryResponse], summary="Search/filter across headings or body text")
def search_nodes(
    q: str = Query(..., min_length=1),
    version: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    ver = version or get_latest_version_number(db)
    pattern = f"%{q}%"
    nodes = (
        db.query(NodeVersion)
        .filter(
            NodeVersion.version_number == ver,
            (NodeVersion.heading.ilike(pattern)) | (NodeVersion.body_text.ilike(pattern)),
        )
        .all()
    )
    return [
        NodeSummaryResponse(
            logical_node_id=n.logical_node_id,
            node_version_id=n.id,
            version_number=n.version_number,
            section_number=n.section_number,
            heading=n.heading,
            level=n.level,
            content_hash=n.content_hash,
            change_status=n.change_status,
        )
        for n in nodes
    ]

@app.get("/nodes/{logical_node_id}", response_model=NodeDetailResponse, summary="Get node by logical ID with children")
def get_node_detail(
    logical_node_id: str,
    version: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    ver = version or get_latest_version_number(db)
    nv = (
        db.query(NodeVersion)
        .filter_by(logical_node_id=logical_node_id, version_number=ver)
        .first()
    )
    if not nv:
        raise HTTPException(status_code=404, detail=f"Node {logical_node_id} not found in version {ver}")
    children = (
        db.query(NodeVersion)
        .filter_by(parent_logical_id=logical_node_id, version_number=ver)
        .all()
    )
    return NodeDetailResponse(
        logical_node_id=nv.logical_node_id,
        node_version_id=nv.id,
        version_number=nv.version_number,
        section_number=nv.section_number,
        heading=nv.heading,
        level=nv.level,
        content_hash=nv.content_hash,
        change_status=nv.change_status,
        parent_logical_id=nv.parent_logical_id,
        canonical_path=nv.logical_node.canonical_path,
        body_text=nv.body_text,
        tables_markdown=nv.tables_markdown,
        figures_captions=nv.figures_captions,
        children=[
            NodeSummaryResponse(
                logical_node_id=c.logical_node_id,
                node_version_id=c.id,
                version_number=c.version_number,
                section_number=c.section_number,
                heading=c.heading,
                level=c.level,
                content_hash=c.content_hash,
                change_status=c.change_status,
            )
            for c in children
        ],
    )

@app.get("/nodes/{logical_node_id}/diff", response_model=NodeDiffResponse, summary="Check if node changed across versions")
def get_node_version_diff(
    logical_node_id: str,
    v1: int = Query(1, ge=1),
    v2: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    target_v2 = v2 or get_latest_version_number(db)
    logical = db.query(LogicalNode).filter_by(id=logical_node_id).first()
    if not logical:
        raise HTTPException(status_code=404, detail="Logical node not found.")
    nv1 = db.query(NodeVersion).filter_by(logical_node_id=logical_node_id, version_number=v1).first()
    nv2 = db.query(NodeVersion).filter_by(logical_node_id=logical_node_id, version_number=target_v2).first()
    diff_info = compute_node_diff(nv1, nv2)
    return NodeDiffResponse(
        logical_node_id=logical_node_id,
        canonical_path=logical.canonical_path,
        v1_version=v1,
        v2_version=target_v2,
        changed=diff_info["changed"],
        change_status=diff_info["change_status"],
        v1_hash=nv1.content_hash if nv1 else None,
        v2_hash=nv2.content_hash if nv2 else None,
        diff_summary=diff_info["diff_summary"],
        numeric_threshold_changed=diff_info["numeric_threshold_changed"],
    )

@app.post("/selections", summary="Create a version-pinned selection of node IDs")
def create_selection(payload: SelectionCreateRequest, db: Session = Depends(get_db)):
    sel = Selection(name=payload.name, pinned_version=payload.version_number)
    db.add(sel)
    db.flush()
    pinned_nodes = []
    for lid in dict.fromkeys(payload.logical_node_ids):
        nv = (
            db.query(NodeVersion)
            .filter_by(logical_node_id=lid, version_number=payload.version_number)
            .first()
        )
        if not nv:
            raise HTTPException(
                status_code=400,
                detail=f"Logical node {lid} does not exist in version {payload.version_number}",
            )
        item = SelectionItem(
            selection_id=sel.id,
            logical_node_id=lid,
            node_version_id=nv.id,
            pinned_content_hash=nv.content_hash,
        )
        db.add(item)
        pinned_nodes.append(
            {
                "logical_node_id": lid,
                "node_version_id": nv.id,
                "heading": nv.heading,
                "pinned_content_hash": nv.content_hash,
            }
        )
    db.commit()
    return {
        "selection_id": sel.id,
        "name": sel.name,
        "pinned_version": sel.pinned_version,
        "pinned_nodes": pinned_nodes,
    }

@app.post("/selections/{selection_id}/generate", summary="Generate LLM QA test cases for a pinned selection")
def generate_tests_for_selection(
    selection_id: str,
    req: GenerateRequest = GenerateRequest(),
    db: Session = Depends(get_db),
):
    sel = db.query(Selection).filter_by(id=selection_id).first()
    if not sel:
        raise HTTPException(status_code=404, detail="Selection not found.")
    node_snapshots = []
    for item in sel.items:
        nv = item.node_version
        node_snapshots.append(
            {
                "logical_node_id": item.logical_node_id,
                "node_version_id": nv.id,
                "pinned_version": sel.pinned_version,
                "canonical_path": nv.logical_node.canonical_path,
                "heading": nv.heading,
                "body_text": nv.body_text,
                "tables_markdown": nv.tables_markdown,
                "pinned_content_hash": item.pinned_content_hash,
            }
        )
    combined_hash = hashlib.sha256(
        "|".join(sorted(s["pinned_content_hash"] for s in node_snapshots)).encode("utf-8")
    ).hexdigest()
    if not req.force_regenerate:
        existing_gen = nosql_store.find_by_selection_and_hash(selection_id, combined_hash)
        if existing_gen:
            return {"cached": True, **existing_gen}
    try:
        validated_suite = generate_and_validate_test_cases(node_snapshots)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    gen_doc = {
        "generation_id": str(uuid.uuid4()),
        "selection_id": sel.id,
        "selection_name": sel.name,
        "pinned_version": sel.pinned_version,
        "combined_source_hash": combined_hash,
        "node_snapshots": node_snapshots,
        "test_cases": [tc.model_dump() for tc in validated_suite.test_cases],
    }
    saved = nosql_store.insert_generation(gen_doc)
    return {"cached": False, **saved}

def enrich_with_staleness(gen_doc: dict, db: Session) -> dict:
    latest_ver = get_latest_version_number(db)
    snapshot_by_lid = {s["logical_node_id"]: s for s in gen_doc["node_snapshots"]}
    node_staleness_map = {}
    for lid, snap in snapshot_by_lid.items():
        latest_nv = (
            db.query(NodeVersion)
            .filter_by(logical_node_id=lid, version_number=latest_ver)
            .first()
        )
        old_nv = db.query(NodeVersion).filter_by(id=snap["node_version_id"]).first()
        if not latest_nv:
            node_staleness_map[lid] = {
                "is_stale": True,
                "staleness_reason": f"Section was removed in v{latest_ver}",
                "severity": "HIGH_IMPACT_REMOVED",
                "diff_summary": "Requirement deleted from latest manual.",
            }
        elif latest_nv.content_hash != snap["pinned_content_hash"]:
            diff_info = compute_node_diff(old_nv, latest_nv)
            node_staleness_map[lid] = {
                "is_stale": True,
                "staleness_reason": f"Hash mismatch between pinned v{gen_doc['pinned_version']} and latest v{latest_ver}",
                "severity": "CRITICAL_THRESHOLD_CHANGE" if diff_info["numeric_threshold_changed"] else "LOW_WORDING_CHANGE",
                "diff_summary": diff_info["diff_summary"],
            }
        else:
            node_staleness_map[lid] = {
                "is_stale": False,
                "staleness_reason": None,
                "severity": "NONE",
                "diff_summary": "Unchanged in latest version.",
            }
    enriched_cases = []
    for tc in gen_doc["test_cases"]:
        src_lid = tc["source_logical_node_id"]
        st = node_staleness_map.get(src_lid, {"is_stale": True, "staleness_reason": "Unknown source node"})
        enriched_cases.append({**tc, "staleness": st})
    any_stale = any(v["is_stale"] for v in node_staleness_map.values())
    return {
        **gen_doc,
        "evaluated_against_latest_version": latest_ver,
        "selection_is_stale": any_stale,
        "node_staleness_breakdown": node_staleness_map,
        "test_cases": enriched_cases,
    }

@app.get("/generations/by-selection/{selection_id}", summary="Retrieve generated test cases by selection ID with live staleness check")
def get_generations_by_selection(selection_id: str, db: Session = Depends(get_db)):
    docs = nosql_store.find_by_selection_id(selection_id)
    if not docs:
        raise HTTPException(status_code=404, detail="No generations found for this selection_id")
    return [enrich_with_staleness(d, db) for d in docs]

@app.get("/generations/by-node/{logical_node_id}", summary="Retrieve generated test cases by node ID with live staleness check")
def get_generations_by_node(logical_node_id: str, db: Session = Depends(get_db)):
    docs = nosql_store.find_by_logical_node_id(logical_node_id)
    if not docs:
        raise HTTPException(status_code=404, detail="No generations found referencing this logical_node_id")
    return [enrich_with_staleness(d, db) for d in docs]
