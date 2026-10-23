import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.parser import parse_hierarchy_from_lines
from app.versioning import ingest_parsed_sections, compute_node_diff
from app.models import NodeVersion

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_duplicate_headings_under_different_parents_produce_distinct_logical_nodes(db_session):
    lines = [
        "2. Cuff Operation",
        "Inflate cuff smoothly.",
        "2.1 General Warnings",
        "Do not inflate beyond 300 mmHg.",
        "3. Battery & Power",
        "Use 4x AA alkaline batteries.",
        "3.1 Power Warnings",
        "Remove batteries if device is unused for 3 months.",
    ]
    parsed = parse_hierarchy_from_lines(lines)
    ingest_parsed_sections(db_session, "CT-200", 1, "v1.pdf", parsed)
    warnings_nodes = db_session.query(NodeVersion).filter(NodeVersion.heading.ilike("%Warnings%"), NodeVersion.version_number == 1).all()
    assert len(warnings_nodes) == 2
    assert warnings_nodes[0].logical_node_id != warnings_nodes[1].logical_node_id

def test_page_headers_footers_and_inline_tables_preserved_without_breaking_hierarchy():
    lines = [
        "4. Error Codes and Alarms",
        "4.1 Pressure Faults",
        "The monitor triggers an audible alarm when pressure limits are exceeded.",
        "Page 4 of 12",
        "CardioTrack CT-200 Manual",
        "Figure 4.1: Emergency Auto-Deflate Solenoid Valve",
        "Verify valve opens completely after E3 triggers.",
    ]
    parsed = parse_hierarchy_from_lines(lines)
    assert len(parsed) == 2
    sec_4_1 = parsed[1]
    assert sec_4_1.section_number == "4.1"
    assert "Page 4 of 12" not in sec_4_1.body_text
    assert "Verify valve opens completely" in sec_4_1.body_text

def test_v1_to_v2_reingestion_keeps_unchanged_nodes_and_flags_modified_thresholds(db_session):
    v1_lines = [
        "1. Overview",
        "The CT-200 measures systolic and diastolic pressure.",
        "2. Safety Limits",
        "Maximum cuff pressure is 300 mmHg with auto-deflate within 2 seconds.",
    ]
    v2_lines = [
        "1. Overview",
        "The CT-200 measures systolic and diastolic pressure.",
        "2. Safety Limits",
        "Maximum cuff pressure is 290 mmHg with auto-deflate within 1.5 seconds.",
    ]
    ingest_parsed_sections(db_session, "CT-200", 1, "v1.pdf", parse_hierarchy_from_lines(v1_lines))
    ingest_parsed_sections(db_session, "CT-200", 2, "v2.pdf", parse_hierarchy_from_lines(v2_lines))
    v1_safety = db_session.query(NodeVersion).filter_by(heading="Safety Limits", version_number=1).first()
    v2_safety = db_session.query(NodeVersion).filter_by(heading="Safety Limits", version_number=2).first()
    v2_overview = db_session.query(NodeVersion).filter_by(heading="Overview", version_number=2).first()
    assert v1_safety.logical_node_id == v2_safety.logical_node_id
    assert v2_overview.change_status == "UNCHANGED"
    assert v2_safety.change_status == "MODIFIED"
    diff = compute_node_diff(v1_safety, v2_safety)
    assert diff["changed"] is True
    assert diff["numeric_threshold_changed"] is True