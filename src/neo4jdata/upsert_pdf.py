"""
PDF Document Version Upserter for Neo4j Knowledge Graph

Manages the upload and tracking of PDF documents as DocVersions with their SlideUnits
and Change nodes between versions.

Graph Structure:
    Course -> Semester -> Resource (type: pdf) -> DocVersion -> SlideUnit
    
Change Tracking:
    Change nodes connect SlideUnits across versions with relationships:
    - [:FROM_SLIDE] -> SlideUnit (0, 1, or many - for splits)
    - [:TO_SLIDE] -> SlideUnit (0, 1, or many - for merges)
    
Change Types:
    - unchanged: High similarity (>0.95)
    - modified: Changed content (1→1)
    - added: New slide (0→1)
    - removed: Deleted slide (1→0)
    - split: One slide becomes multiple (1→N)
    - merge: Multiple slides become one (N→1)
"""

import json
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

import sys
sys.path.append(str(Path(__file__).parent.parent))

from src.neo4jdata.start import Neo4jConnection
from src.neo4jdata.embedder import TextEmbedder
from src.utils import Helpers


class PDFLoader:
    """Handles uploading PDF DocVersions with change tracking to Neo4j"""
    
    def __init__(self, neo_connection: Neo4jConnection, embedder: TextEmbedder):
        self.neo = neo_connection
        self.db = neo_connection.db
        self.embedder = embedder
        self._create_constraints()
        self._create_indexes()
        self._create_vector_index()
    
    def _create_constraints(self):
        """Create unique constraints for PDF document nodes"""
        constraints = [
            "CREATE CONSTRAINT resource_id IF NOT EXISTS FOR (r:Resource) REQUIRE r.resource_id IS UNIQUE",
            "CREATE CONSTRAINT docversion_id IF NOT EXISTS FOR (d:DocVersion) REQUIRE d.docversion_id IS UNIQUE",
            "CREATE CONSTRAINT slideunit_id IF NOT EXISTS FOR (s:SlideUnit) REQUIRE s.slideunit_id IS UNIQUE",
            "CREATE CONSTRAINT change_id IF NOT EXISTS FOR (c:Change) REQUIRE c.change_id IS UNIQUE",
            "CREATE CONSTRAINT course_slug IF NOT EXISTS FOR (c:Course) REQUIRE c.course_slug IS UNIQUE"
        ]
        
        for constraint in constraints:
            try:
                self.db.run(constraint)
            except Exception:
                pass  # Constraint already exists
        
        print("✅ PDF document constraints created")
    
    def _create_indexes(self):
        """Create indexes for better search performance"""
        indexes = [
            "CREATE INDEX resource_type IF NOT EXISTS FOR (r:Resource) ON (r.resource_type)",
            "CREATE INDEX docversion_file IF NOT EXISTS FOR (d:DocVersion) ON (d.document_file)",
            "CREATE INDEX slideunit_page IF NOT EXISTS FOR (s:SlideUnit) ON (s.page_number)",
            "CREATE INDEX slideunit_title IF NOT EXISTS FOR (s:SlideUnit) ON (s.title)",
            "CREATE INDEX change_type IF NOT EXISTS FOR (c:Change) ON (c.change_type)"
        ]
        
        for index in indexes:
            try:
                self.db.run(index)
            except Exception:
                pass  # Index already exists
        
        print("✅ PDF document indexes created")
    
    def _create_vector_index(self):
        """Create vector index for SlideUnit embeddings"""
        try:
            self.db.run("""
                CREATE VECTOR INDEX slideunit_embedding IF NOT EXISTS
                FOR (s:SlideUnit) ON (s.embedding)
                OPTIONS {
                    indexConfig: {
                        `vector.dimensions`: $dims,
                        `vector.similarity_function`: 'cosine'
                    }
                }
            """, dims=self.embedder.dims)
            print(f"✅ SlideUnit vector index (dims={self.embedder.dims}) ready")
        except Exception as e:
            print(f"⚠️ Vector index already exists or error: {e}")
    
    def _create_or_update_course(self, course_name: str) -> str:
        """Create or update Course node, returns course_slug"""
        course_slug = Helpers.normalize_course_title(course_name)
        
        self.db.run("""
            MERGE (c:Course {course_slug: $course_slug})
            SET c.course_name = $course_name
        """, {
            'course_slug': course_slug,
            'course_name': course_name
        })
        
        return course_slug
    
    def _create_or_update_semester(self, course_id: str, course_name: str, 
                                   semester_label: str, course_slug: str):
        """Create or update Semester node and link to Course"""
        self.db.run("""
            MERGE (s:Semester {course_id: $course_id})
            SET s.semester_label = $semester_label,
                s.course_name = $course_name
        """, {
            'course_id': course_id,
            'semester_label': semester_label,
            'course_name': course_name
        })
        
        # Link Course -> Semester
        self.db.run("""
            MATCH (c:Course {course_slug: $course_slug})
            MATCH (s:Semester {course_id: $course_id})
            MERGE (c)-[:HAS_SEMESTER]->(s)
        """, {
            'course_slug': course_slug,
            'course_id': course_id
        })
    
    def _create_or_update_resource(self, resource_name: str, resource_id: str,
                                  course_slug: str) -> str:
        """
        Create or update Resource node (PDF), returns resource_id.
        
        IMPORTANT: Resource is now identified by resource_id from pairs.json.
        Each pair in pairs.json represents the same resource across different versions.
        The resource_id is deterministic and shared across all versions of the same resource.
        
        Args:
            resource_name: Clean name for the resource (from pairs.json)
            resource_id: Unique identifier for this resource (from pairs.json pair_id)
            course_slug: Course slug for linking
            
        Example:
            pairs.json entry:
            {
                "old": "data/course_30422/document/files_pdf/30422_002_01_document.pdf",
                "new": "data/course_40280/document/files_pdf/40280_001_02_document.pdf",
                "pair_id": "introprog_orga_v1_v2",
                "resource_name": "VL01: Organisation"
            }
            
            This creates ONE Resource with:
            - resource_id = "introprog_orga_v1_v2" (from pair_id)
            - resource_name = "VL01: Organisation"
            - Multiple Semesters can link to this same Resource
        """
        self.db.run("""
            MERGE (r:Resource {resource_id: $resource_id})
            SET r.resource_type = 'pdf',
                r.resource_name = $resource_name,
                r.course_slug = $course_slug
        """, {
            'resource_id': resource_id,
            'resource_name': resource_name,
            'course_slug': course_slug
        })
        
        return resource_id
    
    def _create_docversion(self, slides_data: List[Dict[str, Any]], 
                          resource_id: str, generate_embeddings: bool = True,
                          document_url: Optional[str] = None,
                          moodle_url: Optional[str] = None,
                          is_latest: bool = False) -> str:
        """
        Create DocVersion node and all its SlideUnits.
        
        Args:
            slides_data: List of slide dictionaries
            resource_id: ID of the parent Resource
            generate_embeddings: If True, generate embeddings for SlideUnits (default: True)
            document_url: Document URL (overrides metadata if provided)
            moodle_url: Moodle URL (overrides metadata if provided)
            is_latest: If True, marks this version as latest (searchable via fulltext/vector)
        
        Returns:
            docversion_id
        """
        if not slides_data:
            raise ValueError("No slides data provided")
        
        # Extract metadata from first slide
        first_slide = slides_data[0]
        metadata = first_slide.get('metadata', {})
        
        # Generate docversion_id from document file + ingest timestamp
        doc_file = metadata.get('document_file', 'unknown')
        ingest_ts = metadata.get('ingest_ts', datetime.now(timezone.utc).isoformat())
        docversion_id = hashlib.md5(f"{doc_file}:{ingest_ts}".encode()).hexdigest()
        
        # Use provided URLs or fall back to metadata
        final_doc_url = document_url if document_url is not None else metadata.get('document_url')
        final_moodle_url = moodle_url if moodle_url is not None else metadata.get('moodle_url')
        
        # Create DocVersion node
        self.db.run("""
            MERGE (d:DocVersion {docversion_id: $docversion_id})
            SET d.source = $source,
                d.document_file = $document_file,
                d.document_title = $document_title,
                d.document_url = $document_url,
                d.moodle_url = $moodle_url,
                d.ingest_ts = $ingest_ts,
                d.page_count = $page_count,
                d.course_id = $course_id,
                d.course_name = $course_name,
                d.course_semester = $course_semester,
                d.is_latest = $is_latest,
                d.created_at = datetime()
        """, {
            'docversion_id': docversion_id,
            'source': metadata.get('source', 'pdf'),
            'document_file': metadata.get('document_file'),
            'document_title': metadata.get('document_title'),
            'document_url': final_doc_url,
            'moodle_url': final_moodle_url,
            'ingest_ts': ingest_ts,
            'page_count': len(slides_data),
            'course_id': metadata.get('course_id'),
            'course_name': metadata.get('course_name'),
            'course_semester': metadata.get('course_semester'),
            'is_latest': is_latest
        })
        
        # Link Resource -> DocVersion and update Resource URLs
        # Keep the most recent URLs on Resource for chatbot source references
        self.db.run("""
            MATCH (r:Resource {resource_id: $resource_id})
            MATCH (d:DocVersion {docversion_id: $docversion_id})
            MERGE (r)-[:HAS_VERSION]->(d)
            SET r.document_url = $document_url,
                r.moodle_url = $moodle_url
        """, {
            'resource_id': resource_id,
            'docversion_id': docversion_id,
            'document_url': final_doc_url,
            'moodle_url': final_moodle_url
        })
        
        # Create SlideUnits (with or without embeddings)
        if generate_embeddings:
            print(f"📄 Creating {len(slides_data)} SlideUnits with embeddings...")
        else:
            print(f"📄 Creating {len(slides_data)} SlideUnits (no embeddings)...")
        self._create_slideunits(slides_data, docversion_id, generate_embeddings)
        
        return docversion_id
    
    def _create_slideunits(self, slides_data: List[Dict[str, Any]], docversion_id: str, 
                          generate_embeddings: bool = True):
        """
        Create SlideUnit nodes and link them to DocVersion.
        
        Args:
            slides_data: List of slide dictionaries
            docversion_id: ID of the parent DocVersion
            generate_embeddings: If True, generate and store embeddings (default: True)
        """
        slide_texts = []
        slide_ids = []
        
        for slide in slides_data:
            metadata = slide.get('metadata', {})
            chunk_id = metadata.get('chunk_id')
            
            if not chunk_id:
                # Generate fallback chunk_id
                content = slide.get('content', '')
                chunk_id = hashlib.md5(content.encode()).hexdigest()
            
            slideunit_id = f"{docversion_id}:{chunk_id}"
            slide_ids.append(slideunit_id)
            
            # Prepare text for embedding (title + content)
            title = slide.get('title', '')
            content = slide.get('content', '')
            combined_text = f"{title}\n\n{content}".strip()
            slide_texts.append(combined_text if combined_text else "Empty slide")
            
            # Create SlideUnit node (without embedding first)
            self.db.run("""
                MERGE (s:SlideUnit {slideunit_id: $slideunit_id})
                SET s.page_number = $page_number,
                    s.title = $title,
                    s.content = $content,
                    s.content_lines = $content_lines,
                    s.chunk_id = $chunk_id,
                    s.prev_chunk_id = $prev_chunk_id,
                    s.next_chunk_id = $next_chunk_id,
                    s.urls = $urls,
                    s.has_tables = $has_tables,
                    s.created_at = datetime()
            """, {
                'slideunit_id': slideunit_id,
                'page_number': slide.get('page_number'),
                'title': title,
                'content': content,
                'content_lines': slide.get('content_lines', []),
                'chunk_id': chunk_id,
                'prev_chunk_id': metadata.get('prev_chunk_id'),
                'next_chunk_id': metadata.get('next_chunk_id'),
                'urls': metadata.get('urls', []),
                'has_tables': len(metadata.get('tables', [])) > 0
            })
            
            # Link DocVersion -> SlideUnit
            self.db.run("""
                MATCH (d:DocVersion {docversion_id: $docversion_id})
                MATCH (s:SlideUnit {slideunit_id: $slideunit_id})
                MERGE (d)-[:HAS_SLIDE]->(s)
            """, {
                'docversion_id': docversion_id,
                'slideunit_id': slideunit_id
            })
        
        # Create navigation relationships between SlideUnits (NEXT_SLIDE/PREV_SLIDE)
        print("🔗 Creating navigation relationships between SlideUnits...")
        for i, slide in enumerate(slides_data):
            metadata = slide.get('metadata', {})
            chunk_id = metadata.get('chunk_id')
            if not chunk_id:
                content = slide.get('content', '')
                chunk_id = hashlib.md5(content.encode()).hexdigest()
            
            current_slideunit_id = f"{docversion_id}:{chunk_id}"
            
            # Link to previous slide
            if i > 0:
                prev_metadata = slides_data[i - 1].get('metadata', {})
                prev_chunk_id = prev_metadata.get('chunk_id')
                if not prev_chunk_id:
                    prev_content = slides_data[i - 1].get('content', '')
                    prev_chunk_id = hashlib.md5(prev_content.encode()).hexdigest()
                
                prev_slideunit_id = f"{docversion_id}:{prev_chunk_id}"
                
                self.db.run("""
                    MATCH (current:SlideUnit {slideunit_id: $current_id})
                    MATCH (prev:SlideUnit {slideunit_id: $prev_id})
                    MERGE (current)-[:PREV_SLIDE]->(prev)
                """, {
                    'current_id': current_slideunit_id,
                    'prev_id': prev_slideunit_id
                })
            
            # Link to next slide
            if i < len(slides_data) - 1:
                next_metadata = slides_data[i + 1].get('metadata', {})
                next_chunk_id = next_metadata.get('chunk_id')
                if not next_chunk_id:
                    next_content = slides_data[i + 1].get('content', '')
                    next_chunk_id = hashlib.md5(next_content.encode()).hexdigest()
                
                next_slideunit_id = f"{docversion_id}:{next_chunk_id}"
                
                self.db.run("""
                    MATCH (current:SlideUnit {slideunit_id: $current_id})
                    MATCH (next:SlideUnit {slideunit_id: $next_id})
                    MERGE (current)-[:NEXT_SLIDE]->(next)
                """, {
                    'current_id': current_slideunit_id,
                    'next_id': next_slideunit_id
                })
        
        # Generate embeddings in batch only if requested
        if generate_embeddings:
            print(f"🔢 Generating embeddings for {len(slide_texts)} slides...")
            embeddings = self.embedder.embed_list(slide_texts)
            
            # Store embeddings
            for slideunit_id, embedding in zip(slide_ids, embeddings, strict=False):
                self.db.run("""
                    MATCH (s:SlideUnit {slideunit_id: $slideunit_id})
                    SET s.embedding = $embedding
                """, {
                    'slideunit_id': slideunit_id,
                    'embedding': embedding.tolist()
                })
            
            print(f"✅ Created {len(slides_data)} SlideUnits with embeddings")
        else:
            print(f"✅ Created {len(slides_data)} SlideUnits (embeddings skipped)")
    
    def _create_changes(self, changes_data: Dict[str, Any], 
                       old_docversion_id: str, new_docversion_id: str):
        """
        Create Change nodes tracking differences between two DocVersions.
        Links Change nodes to SlideUnits with FROM_SLIDE and TO_SLIDE relationships.
        """
        detailed_changes = changes_data.get('detailed_changes', [])
        
        if not detailed_changes:
            print("⚠️ No detailed changes found in changes data")
            return
        
        print(f"📊 Creating {len(detailed_changes)} Change nodes...")
        
        # Get SlideUnit mappings for both versions
        old_slides = self._get_slideunit_mapping(old_docversion_id)
        new_slides = self._get_slideunit_mapping(new_docversion_id)
        
        for change in detailed_changes:
            change_type = change.get('change_type')
            old_idx = change.get('old_slide_index')
            new_idx = change.get('new_slide_index')
            old_indices = change.get('old_slide_indices', [])
            new_indices = change.get('new_slide_indices', [])
            
            # Generate unique change_id
            change_id_base = f"{old_docversion_id}:{new_docversion_id}:{change_type}"
            if old_idx is not None:
                change_id_base += f":old{old_idx}"
            if new_idx is not None:
                change_id_base += f":new{new_idx}"
            if old_indices:
                change_id_base += f":oldM{'-'.join(map(str, old_indices))}"
            if new_indices:
                change_id_base += f":newM{'-'.join(map(str, new_indices))}"
            
            change_id = hashlib.md5(change_id_base.encode()).hexdigest()
            
            # Create Change node
            self.db.run("""
                MERGE (c:Change {change_id: $change_id})
                SET c.change_type = $change_type,
                    c.old_slide_index = $old_slide_index,
                    c.new_slide_index = $new_slide_index,
                    c.old_slide_indices = $old_slide_indices,
                    c.new_slide_indices = $new_slide_indices,
                    c.similarity_score = $similarity_score,
                    c.slide_title = $slide_title,
                    c.changes_summary = $changes_summary,
                    c.note = $note,
                    c.created_at = datetime()
            """, {
                'change_id': change_id,
                'change_type': change_type,
                'old_slide_index': old_idx,
                'new_slide_index': new_idx,
                'old_slide_indices': old_indices,
                'new_slide_indices': new_indices,
                'similarity_score': change.get('similarity_score'),
                'slide_title': change.get('slide_title'),
                'changes_summary': json.dumps(change.get('changes', [])),
                'note': change.get('note')
            })
            
            # Link Change to old DocVersion
            self.db.run("""
                MATCH (c:Change {change_id: $change_id})
                MATCH (d:DocVersion {docversion_id: $old_docversion_id})
                MERGE (c)-[:FROM_VERSION]->(d)
            """, {
                'change_id': change_id,
                'old_docversion_id': old_docversion_id
            })
            
            # Link Change to new DocVersion
            self.db.run("""
                MATCH (c:Change {change_id: $change_id})
                MATCH (d:DocVersion {docversion_id: $new_docversion_id})
                MERGE (c)-[:TO_VERSION]->(d)
            """, {
                'change_id': change_id,
                'new_docversion_id': new_docversion_id
            })
            
            # Create FROM_SLIDE relationships
            # Handle single old slide
            if old_idx is not None and old_idx in old_slides:
                self._link_change_to_slide(change_id, old_slides[old_idx], 'FROM_SLIDE')
            
            # Handle multiple old slides (merge case)
            for idx in old_indices:
                if idx in old_slides:
                    self._link_change_to_slide(change_id, old_slides[idx], 'FROM_SLIDE')
            
            # Create TO_SLIDE relationships
            # Handle single new slide
            if new_idx is not None and new_idx in new_slides:
                self._link_change_to_slide(change_id, new_slides[new_idx], 'TO_SLIDE')
            
            # Handle multiple new slides (split case)
            for idx in new_indices:
                if idx in new_slides:
                    self._link_change_to_slide(change_id, new_slides[idx], 'TO_SLIDE')
        
        print(f"✅ Created {len(detailed_changes)} Change nodes with relationships")
    
    def _get_slideunit_mapping(self, docversion_id: str) -> Dict[int, str]:
        """Get mapping of slide index -> slideunit_id for a DocVersion"""
        result = self.db.run("""
            MATCH (d:DocVersion {docversion_id: $docversion_id})-[:HAS_SLIDE]->(s:SlideUnit)
            RETURN s.slideunit_id as slideunit_id, s.page_number as page_number
            ORDER BY s.page_number
        """, docversion_id=docversion_id)
        
        # Create index mapping (page_number - 1 = array index)
        mapping = {}
        for record in result:
            page_number = record['page_number']
            if page_number is not None:
                mapping[page_number - 1] = record['slideunit_id']
        
        return mapping
    
    def _link_change_to_slide(self, change_id: str, slideunit_id: str, rel_type: str):
        """Create relationship between Change and SlideUnit"""
        self.db.run(f"""
            MATCH (c:Change {{change_id: $change_id}})
            MATCH (s:SlideUnit {{slideunit_id: $slideunit_id}})
            MERGE (c)-[:{rel_type}]->(s)
        """, {
            'change_id': change_id,
            'slideunit_id': slideunit_id
        })
    
    def upload_docversion(self, slides_json_file: str, 
                         resource_id: Optional[str] = None,
                         resource_name: Optional[str] = None,
                         generate_embeddings: bool = True) -> str:
        """
        Upload a PDF DocVersion from processed JSON file.
        Creates Course, Semester, Resource, DocVersion, and SlideUnit nodes.
        
        Args:
            slides_json_file: Path to JSON file with slide data
            resource_id: Resource ID from pairs.json (pair_id). If None, uses document_file hash.
            resource_name: Clean resource name from pairs.json. If None, uses document_title.
            generate_embeddings: If True, generate embeddings for SlideUnits (default: True).
                                Set to False for old versions when only newest version needs embeddings.
            
        Returns:
            docversion_id
        """
        print(f"\n📄 Loading PDF document: {slides_json_file}")
        
        with open(slides_json_file, 'r', encoding='utf-8') as f:
            slides_data = json.load(f)
        
        if not slides_data:
            raise ValueError(f"No slides found in {slides_json_file}")
        
        # Extract metadata from first slide
        metadata = slides_data[0].get('metadata', {})
        course_id = metadata.get('course_id')
        course_name = metadata.get('course_name')
        course_semester = metadata.get('course_semester')
        document_file = metadata.get('document_file')
        document_title = metadata.get('document_title')
        document_url = metadata.get('document_url')
        moodle_url = metadata.get('moodle_url')
        
        if not all([course_id, course_name, course_semester, document_file]):
            raise ValueError("Missing required metadata in slides JSON")
        
        print(f"   Course: {course_name}")
        print(f"   Semester: {course_semester}")
        print(f"   Document: {document_title}")
        print(f"   Slides: {len(slides_data)}")
        
        # Create Course and Semester
        course_slug = self._create_or_update_course(course_name)
        self._create_or_update_semester(course_id, course_name, course_semester, course_slug)
        
        # Create or link Resource
        # If resource_id/name from pairs.json, use them; otherwise fallback to document metadata
        if resource_id is None:
            # Fallback: generate resource_id from course_slug + document_file
            resource_id = hashlib.md5(f"{course_slug}:{document_file}".encode()).hexdigest()
        if resource_name is None:
            resource_name = document_title
            
        final_resource_id = self._create_or_update_resource(resource_name, resource_id, course_slug)
        
        # Link Semester -> Resource
        self.db.run("""
            MATCH (s:Semester {course_id: $course_id})
            MATCH (r:Resource {resource_id: $resource_id})
            MERGE (s)-[:HAS_RESOURCE]->(r)
        """, {
            'course_id': course_id,
            'resource_id': final_resource_id
        })
        
        # Store document metadata in DocVersion
        docversion_id = self._create_docversion(
            slides_data, final_resource_id, generate_embeddings,
            document_url=document_url, moodle_url=moodle_url,
            is_latest=generate_embeddings  # is_latest same as generate_embeddings
        )
        
        embedding_status = "with embeddings" if generate_embeddings else "without embeddings"
        print(f"✅ Uploaded DocVersion ({embedding_status}): {docversion_id}")
        return docversion_id
    
    def upload_changes(self, changes_json_file: str, 
                      old_docversion_id: str, new_docversion_id: str):
        """
        Upload Change nodes tracking differences between two DocVersions.
        
        Args:
            changes_json_file: Path to changes JSON file
            old_docversion_id: ID of old DocVersion
            new_docversion_id: ID of new DocVersion
        """
        print(f"\n📊 Loading changes: {changes_json_file}")
        
        with open(changes_json_file, 'r', encoding='utf-8') as f:
            changes_data = json.load(f)
        
        metadata = changes_data.get('metadata', {})
        print(f"   Old slides: {metadata.get('old_slide_count')}")
        print(f"   New slides: {metadata.get('new_slide_count')}")
        print(f"   Changes: {metadata.get('aligned_pairs')} aligned, "
              f"{metadata.get('insertions')} added, {metadata.get('deletions')} removed")
        
        self._create_changes(changes_data, old_docversion_id, new_docversion_id)
        
        print("✅ Uploaded changes between versions")
    
    def get_docversion_stats(self) -> Dict[str, int]:
        """Get statistics about uploaded documents"""
        stats = {}
        
        result = self.db.run("MATCH (c:Course) RETURN count(c) as count")
        stats['courses'] = result.single()['count']
        
        result = self.db.run("MATCH (s:Semester) RETURN count(s) as count")
        stats['semesters'] = result.single()['count']
        
        result = self.db.run("MATCH (r:Resource {resource_type: 'pdf'}) RETURN count(r) as count")
        stats['resources'] = result.single()['count']
        
        result = self.db.run("MATCH (d:DocVersion) RETURN count(d) as count")
        stats['docversions'] = result.single()['count']
        
        result = self.db.run("MATCH (s:SlideUnit) RETURN count(s) as count")
        stats['slideunits'] = result.single()['count']
        
        result = self.db.run("MATCH (c:Change) RETURN count(c) as count")
        stats['changes'] = result.single()['count']
        
        return stats
    
    def remove_embeddings_from_docversion(self, docversion_id: str):
        """Remove all embeddings from SlideUnits of a specific DocVersion"""
        result = self.db.run("""
            MATCH (d:DocVersion {docversion_id: $docversion_id})-[:HAS_SLIDE]->(s:SlideUnit)
            WHERE s.embedding IS NOT NULL
            SET s.embedding = NULL
            RETURN count(s) as removed_count
        """, docversion_id=docversion_id)
        
        removed_count = result.single()['removed_count']
        if removed_count > 0:
            print(f"   🗑️  Removed embeddings from {removed_count} SlideUnits")
        return removed_count
    
    def generate_embeddings_slideunits(self, batch_size: int = 100):
        """Generate embeddings for all SlideUnits that don't have them (idempotent)"""
        total_result = self.db.run("""
            MATCH (s:SlideUnit) 
            WHERE s.embedding IS NULL 
            RETURN count(s) as total
        """)
        total_slides = total_result.single()['total']
        
        if total_slides == 0:
            print("✅ All slides already have embeddings!")
            return
        
        print(f"🔢 Generating embeddings for {total_slides} slides...")
        
        processed = 0
        batch_count = 0
        
        while processed < total_slides:
            batch_count += 1
            print(f"   🔄 Batch {batch_count}: Processing slides {processed + 1}-{min(processed + batch_size, total_slides)}...")
            
            # Get batch of slides without embeddings
            result = self.db.run("""
                MATCH (s:SlideUnit)
                WHERE s.embedding IS NULL
                RETURN s.slideunit_id as slideunit_id,
                       s.title as title,
                       s.content as content
                LIMIT $batch_size
            """, batch_size=batch_size)
            
            batch_slides = list(result)
            
            if not batch_slides:
                break
            
            # Prepare texts for embedding (combine title + content)
            texts = []
            slide_ids = []
            
            for slide in batch_slides:
                title = slide['title'] or ''
                content = slide['content'] or ''
                combined_text = f"{title}\n\n{content}".strip()
                texts.append(combined_text if combined_text else "Empty slide")
                slide_ids.append(slide['slideunit_id'])
            
            # Generate embeddings for the batch
            embeddings = self.embedder.embed_list(texts)
            
            # Store embeddings back to Neo4j
            for slide_id, embedding in zip(slide_ids, embeddings, strict=False):
                self.db.run("""
                    MATCH (s:SlideUnit {slideunit_id: $slideunit_id})
                    SET s.embedding = $embedding
                """,
                    slideunit_id=slide_id,
                    embedding=embedding.tolist()
                )
            
            processed += len(batch_slides)
            progress = (processed / total_slides) * 100
            print(f"   ✅ Batch {batch_count} complete! Progress: {processed}/{total_slides} ({progress:.1f}%)")
        
        print(f"🎉 All {total_slides} slides now have embeddings!")
    
    def upload_from_pairs(self, pairs_json_file: str, 
                         json_dir: Optional[Path] = None) -> Dict[str, List[str]]:
        """
        Upload documents from pairs.json file, handling version chains correctly.
        Only the newest version in each chain gets embeddings.
        
        Args:
            pairs_json_file: Path to pairs.json file
            json_dir: Directory containing the processed JSON files. If None, looks in
                     data/comparison/pdf/{pair_id}/ directories
            
        Returns:
            Dict with 'uploaded' list of docversion_ids and 'newest' list of newest version IDs
            
        Example pairs.json:
            [
              {
                "old": "data/course_30422/document/files_pdf/30422_002_01_document.pdf",
                "new": "data/course_40280/document/files_pdf/40280_001_02_document.pdf",
                "pair_id": "introprog_orga_v1_v2",
                "resource_name": "VL01: Organisation"
              }
            ]
            
        The corresponding JSON files should be in:
            data/comparison/pdf/introprog_orga_v1_v2/30422_002_01_document.json
            data/comparison/pdf/introprog_orga_v1_v2/40280_001_02_document.json
        """
        print(f"\n📦 Loading pairs from: {pairs_json_file}")
        
        pairs_path = Path(pairs_json_file)
        with open(pairs_path, 'r', encoding='utf-8') as f:
            pairs = json.load(f)
        
        workspace_root = pairs_path.parent.parent.parent.parent  # Up to ba-tutorai root
        
        print(f"   Workspace root: {workspace_root}")
        print(f"   Found {len(pairs)} pair(s)")
        
        # Build version chains: which PDFs are old/new/newest
        pdf_to_pair = {}  # pdf_path -> pair_info
        old_pdfs = set()  # All PDFs that appear as "old"
        new_pdfs = set()  # All PDFs that appear as "new"
        
        for pair in pairs:
            old_pdf = pair['old']
            new_pdf = pair['new']
            pair_id = pair['pair_id']
            resource_name = pair.get('resource_name', pair_id)
            resource_id = pair.get('resource_id', pair_id)  # Use resource_id if provided, fallback to pair_id
            
            pdf_to_pair[old_pdf] = {
                'pair_id': pair_id, 
                'resource_name': resource_name,
                'resource_id': resource_id
            }
            pdf_to_pair[new_pdf] = {
                'pair_id': pair_id, 
                'resource_name': resource_name,
                'resource_id': resource_id
            }
            
            old_pdfs.add(old_pdf)
            new_pdfs.add(new_pdf)
        
        # Newest versions: appear as "new" but never as "old"
        newest_pdfs = new_pdfs - old_pdfs
        print(f"   Identified {len(newest_pdfs)} newest version(s)")
        
        # Intermediate versions: appear as both "new" AND "old"
        intermediate_pdfs = new_pdfs & old_pdfs
        if intermediate_pdfs:
            print(f"   Identified {len(intermediate_pdfs)} intermediate version(s) (will remove embeddings if present)")
        
        # Upload all documents
        uploaded_ids = []
        newest_ids = []
        intermediate_ids = []
        uploaded_pdfs = {}  # Track which PDFs we've already uploaded: pdf_path -> docversion_id
        
        for pair in pairs:
            old_pdf = pair['old']
            new_pdf = pair['new']
            pair_id = pair['pair_id']
            resource_name = pair.get('resource_name', pair_id)
            resource_id = pair.get('resource_id', pair_id)  # Use resource_id if provided
            
            # Determine JSON directory
            if json_dir:
                comparison_dir = json_dir
            else:
                comparison_dir = workspace_root / "data" / "comparison" / "pdf" / pair_id
            
            # Extract filename from PDF path
            old_filename = Path(old_pdf).stem + '.json'
            new_filename = Path(new_pdf).stem + '.json'
            
            old_json = comparison_dir / old_filename
            new_json = comparison_dir / new_filename
            
            # Upload old version WITHOUT embeddings (skip if already uploaded)
            if old_pdf in uploaded_pdfs:
                old_id = uploaded_pdfs[old_pdf]
            elif old_json.exists():
                print(f"\n   🔵 Old version: {old_filename}")
                old_id = self.upload_docversion(
                    str(old_json),
                    resource_id=resource_id,  # Use resource_id from pairs.json
                    resource_name=resource_name,
                    generate_embeddings=False
                )
                uploaded_ids.append(old_id)
                uploaded_pdfs[old_pdf] = old_id
            else:
                print(f"   ⚠️  Missing JSON for old: {old_json}")
                continue
            
            # Upload new version WITH embeddings only if it's the newest (skip if already uploaded)
            if new_pdf in uploaded_pdfs:
                new_id = uploaded_pdfs[new_pdf]
            elif new_json.exists():
                is_newest = new_pdf in newest_pdfs
                is_intermediate = new_pdf in intermediate_pdfs
                
                if is_newest:
                    embed_status = "🟢 Newest"
                elif is_intermediate:
                    embed_status = "🟡 Intermediate"
                else:
                    embed_status = "🔵 Version"
                
                print(f"\n   {embed_status} version: {new_filename}")
                
                new_id = self.upload_docversion(
                    str(new_json),
                    resource_id=resource_id,  # Use resource_id from pairs.json
                    resource_name=resource_name,
                    generate_embeddings=is_newest
                )
                uploaded_ids.append(new_id)
                uploaded_pdfs[new_pdf] = new_id
                
                if is_newest:
                    newest_ids.append(new_id)
                elif is_intermediate:
                    # Remove embeddings if this version already has them
                    # (e.g., if it was previously uploaded as newest but now we know it's intermediate)
                    intermediate_ids.append(new_id)
                    self.remove_embeddings_from_docversion(new_id)
            else:
                print(f"   ⚠️  Missing JSON for new: {new_json}")
                continue
            
            # Upload changes if they exist
            changes_filename = f"changes_{old_filename.replace('.json', '')}_{new_filename}"
            changes_json = comparison_dir / changes_filename
            
            if changes_json.exists():
                print("   📊 Uploading changes...")
                self.upload_changes(str(changes_json), old_id, new_id)
            else:
                print(f"   ⚠️  Missing changes file: {changes_json}")
        
        print(f"\n✅ Uploaded {len(uploaded_ids)} DocVersions:")
        print(f"   - {len(newest_ids)} newest version(s) with embeddings")
        if intermediate_ids:
            print(f"   - {len(intermediate_ids)} intermediate version(s) with embeddings removed")
        print(f"   - {len(uploaded_ids) - len(newest_ids) - len(intermediate_ids)} older version(s) without embeddings")
        return {'uploaded': uploaded_ids, 'newest': newest_ids, 'intermediate': intermediate_ids}


def demo_upload_from_pairs():
    """Demo function showing how to upload documents from pairs.json"""
    neo = Neo4jConnection()
    embedder = TextEmbedder(neo)
    upserter = PDFLoader(neo, embedder)
    
    try:
        # Upload all documents from pairs.json
        pairs_file = "capture/parsing/comparer_pdf/pairs.json"
        result = upserter.upload_from_pairs(pairs_file)
        
        print(f"\n✅ Uploaded {len(result['uploaded'])} DocVersions")
        print(f"✅ {len(result['newest'])} newest versions have embeddings")
        if result.get('intermediate'):
            print(f"✅ {len(result['intermediate'])} intermediate versions had embeddings removed")
        
        # Print stats
        print("\n" + "=" * 60)
        print("📊 DATABASE STATISTICS")
        print("=" * 60)
        stats = upserter.get_docversion_stats()
        for key, value in stats.items():
            print(f"   {key.capitalize()}: {value}")
        
        # Query: Show Resource with all its versions
        print("\n" + "=" * 60)
        print("🔍 RESOURCE VERSIONS QUERY")
        print("=" * 60)
        result = neo.db.run("""
            MATCH (r:Resource {resource_type: 'pdf'})-[:HAS_VERSION]->(d:DocVersion)
            OPTIONAL MATCH (s:Semester)-[:HAS_RESOURCE]->(r)
            WITH r, d, collect(DISTINCT s.course_semester) as semesters
            RETURN r.resource_name as resource,
                   r.resource_id as resource_id,
                   r.document_url as resource_url,
                   r.moodle_url as resource_moodle_url,
                   d.document_title as version_title,
                   d.course_semester as version_semester,
                   d.page_count as pages,
                   d.moodle_url as version_moodle_url,
                   d.ingest_ts as ingest_ts,
                   semesters
            ORDER BY d.ingest_ts
        """)
        
        for record in result:
            print(f"\n   📦 {record['resource']} ({record['resource_id']})")
            print("      Resource URLs (latest):")
            print(f"         Download: {record['resource_url']}")
            print(f"         Moodle: {record['resource_moodle_url']}")
            print(f"      Linked semesters: {', '.join(record['semesters'])}")
            print(f"      Version: {record['version_title']} ({record['version_semester']})")
            print(f"      Pages: {record['pages']}")
            print(f"      Moodle URL: {record['version_moodle_url']}")
        
    finally:
        neo.close()


if __name__ == "__main__":
    demo_upload_from_pairs()  # New pairs.json approach
