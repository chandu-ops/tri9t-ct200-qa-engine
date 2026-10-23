import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ct200_traceability.db")

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class NoSQLGenerationStore:
    def __init__(self, filepath: str = "./nosql_generations.json"):
        self.filepath = Path(filepath)
        if not self.filepath.exists():
            self.filepath.write_text("[]", encoding="utf-8")

    def _read_all(self) -> List[Dict[str, Any]]:
        return json.loads(self.filepath.read_text(encoding="utf-8"))

    def _write_all(self, docs: List[Dict[str, Any]]) -> None:
        self.filepath.write_text(json.dumps(docs, indent=2), encoding="utf-8")

    def find_by_selection_and_hash(self, selection_id: str, combined_source_hash: str) -> Optional[Dict[str, Any]]:
        for doc in self._read_all():
            if doc["selection_id"] == selection_id and doc["combined_source_hash"] == combined_source_hash:
                return doc
        return None

    def insert_generation(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        docs = self._read_all()
        doc["created_at"] = datetime.now(timezone.utc).isoformat()
        docs.append(doc)
        self._write_all(docs)
        return doc

    def find_by_selection_id(self, selection_id: str) -> List[Dict[str, Any]]:
        return [d for d in self._read_all() if d["selection_id"] == selection_id]

    def find_by_logical_node_id(self, logical_node_id: str) -> List[Dict[str, Any]]:
        results = []
        for doc in self._read_all():
            for snap in doc.get("node_snapshots", []):
                if snap["logical_node_id"] == logical_node_id:
                    results.append(doc)
                    break
        return results

nosql_store = NoSQLGenerationStore()
