import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Text, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base

def gen_uuid() -> str:
    return str(uuid.uuid4())

class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id = Column(String, primary_key=True, default=gen_uuid)
    doc_name = Column(String, index=True, nullable=False, default="CT-200")
    version_number = Column(Integer, nullable=False, unique=True)
    source_filename = Column(String, nullable=False)
    ingested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class LogicalNode(Base):
    __tablename__ = "logical_nodes"
    id = Column(String, primary_key=True, default=gen_uuid)
    canonical_path = Column(String, unique=True, index=True, nullable=False)
    created_in_version = Column(Integer, nullable=False)
    
    versions = relationship("NodeVersion", foreign_keys="NodeVersion.logical_node_id", back_populates="logical_node", cascade="all, delete-orphan")

class NodeVersion(Base):
    __tablename__ = "node_versions"
    id = Column(String, primary_key=True, default=gen_uuid)
    logical_node_id = Column(String, ForeignKey("logical_nodes.id"), nullable=False, index=True)
    version_number = Column(Integer, ForeignKey("document_versions.version_number"), nullable=False, index=True)
    parent_logical_id = Column(String, ForeignKey("logical_nodes.id"), nullable=True, index=True)
    section_number = Column(String, nullable=True)
    heading = Column(String, nullable=False, index=True)
    level = Column(Integer, nullable=False)
    body_text = Column(Text, nullable=False, default="")
    tables_markdown = Column(Text, nullable=True)
    figures_captions = Column(Text, nullable=True)
    content_hash = Column(String, nullable=False, index=True)
    change_status = Column(String, nullable=False, default="ADDED")
    
    logical_node = relationship("LogicalNode", foreign_keys=[logical_node_id], back_populates="versions")

    __table_args__ = (
        UniqueConstraint("logical_node_id", "version_number", name="uq_logical_node_version"),
    )

class Selection(Base):
    __tablename__ = "selections"
    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    pinned_version = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    items = relationship("SelectionItem", back_populates="selection", cascade="all, delete-orphan")

class SelectionItem(Base):
    __tablename__ = "selection_items"
    id = Column(String, primary_key=True, default=gen_uuid)
    selection_id = Column(String, ForeignKey("selections.id"), nullable=False, index=True)
    logical_node_id = Column(String, ForeignKey("logical_nodes.id"), nullable=False)
    node_version_id = Column(String, ForeignKey("node_versions.id"), nullable=False)
    pinned_content_hash = Column(String, nullable=False)
    selection = relationship("Selection", back_populates="items")
    node_version = relationship("NodeVersion")