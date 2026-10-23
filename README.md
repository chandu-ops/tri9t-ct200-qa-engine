# CardioTrack CT-200 Traceable QA & Versioning Backend

Backend engine for parsing the CardioTrack CT-200 medical device manual into a versioned hierarchy, creating version-pinned requirement selections, generating validated QA test cases via LLM, and detecting stale traceability when `v2` of the manual is ingested.

## 1. Setup & Installation

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt