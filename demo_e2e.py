import os
import json
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def run_e2e_demo():
    # Automatically resolve v1 filename whether named ct200_manual_v1.pdf or ct200_manual.pdf
    if os.path.exists("data/ct200_manual_v1.pdf"):
        v1_pdf = "data/ct200_manual_v1.pdf"
    elif os.path.exists("data/ct200_manual.pdf"):
        v1_pdf = "data/ct200_manual.pdf"
    else:
        v1_pdf = "data/ct200_manual_v1.pdf"

    v2_pdf = "data/ct200_manual_v2.pdf"

    print("=== STEP 1: Ingesting CT-200 Manual v1 ===")
    r1 = client.post("/ingest", params={"pdf_path": v1_pdf, "version_number": 1})
    print(r1.status_code, r1.json())

    print("\n=== STEP 2: Listing Top-Level Sections (v1) ===")
    top_nodes = client.get("/nodes/top-level", params={"version": 1}).json()
    for n in top_nodes[:5]:
        print(f" - [{n['section_number']}] {n['heading']} (logical_node_id={n['logical_node_id']})")
    
    if not top_nodes:
        print("ERROR: No nodes were parsed from v1 PDF. Check if the PDF file exists in data/.")
        return

    selected_ids = [n["logical_node_id"] for n in top_nodes[:2]]

    print("\n=== STEP 3: Creating Version-Pinned Selection (v1) ===")
    sel_resp = client.post(
        "/selections",
        json={"name": "CT-200 Core Safety Selection v1", "version_number": 1, "logical_node_ids": selected_ids},
    ).json()
    selection_id = sel_resp["selection_id"]
    print(f"Created Pinned Selection ID: {selection_id}")

    print("\n=== STEP 4: Generating Validated QA Test Cases via LLM ===")
    gen_resp = client.post(f"/selections/{selection_id}/generate", json={"force_regenerate": False})
    print(f"Status: {gen_resp.status_code}")
    print(json.dumps(gen_resp.json(), indent=2)[:1000])

    print("\n=== STEP 5: Re-Ingesting Modified Manual (v2) ===")
    r2 = client.post("/ingest", params={"pdf_path": v2_pdf, "version_number": 2})
    print(r2.status_code, r2.json())

    print("\n=== STEP 6: Retrieving Test Cases with Live Staleness Detection ===")
    stale_check = client.get(f"/generations/by-selection/{selection_id}").json()
    print(json.dumps(stale_check, indent=2)[:1500])

if __name__ == "__main__":
    run_e2e_demo()