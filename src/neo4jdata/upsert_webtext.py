"""
Webtext Version Upserter for Neo4j Knowledge Graph

Manages the upload and tracking of webtext documents as WebtextVersions with their ChunkUnits
and Change nodes between versions.

Graph Structure:
    Course -> Semester -> Resource (type: webtext) -> WebtextVersion -> ChunkUnit
    
ChunkUnits are the retrievable units (like SlideUnit for PDFs):
    - Each webtext section is split into chunks (~600 tokens each)
    - ChunkUnits have embeddings for semantic search (RRF)
    - ChunkUnits preserve section context (section_title, section_index, chunk_index)
    
Change Tracking:
    Change nodes connect ChunkUnits across versions with relationships:
    - [:FROM_CHUNK] -> ChunkUnit (0, 1, or many - for splits)
    - [:TO_CHUNK] -> ChunkUnit (0, 1, or many - for merges)
    
Change Types:
    - unchanged: High similarity (>0.95)
    - modified: Changed content (1→1)
    - added: New chunk (0→1)
    - removed: Deleted chunk (1→0)
    - split: One chunk becomes multiple (1→N)
    - merge: Multiple chunks become one (N→1)
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


class WebtextLoader:
    """Handles uploading Webtext WebtextVersions with change tracking to Neo4j"""
    
    def __init__(self, neo_connection: Neo4jConnection, embedder: TextEmbedder):
        self.neo = neo_connection
        self.db = neo_connection.db
        self.embedder = embedder
        self._create_constraints()
        self._create_indexes()
        self._create_vector_index()
    
    def _create_constraints(self):
        """Create unique constraints for Webtext document nodes"""
        constraints = [
            "CREATE CONSTRAINT resource_id IF NOT EXISTS FOR (r:Resource) REQUIRE r.resource_id IS UNIQUE",
            "CREATE CONSTRAINT webtextversion_id IF NOT EXISTS FOR (w:WebtextVersion) REQUIRE w.webtextversion_id IS UNIQUE",
            "CREATE CONSTRAINT chunkunit_id IF NOT EXISTS FOR (c:ChunkUnit) REQUIRE c.chunkunit_id IS UNIQUE",
            "CREATE CONSTRAINT change_id IF NOT EXISTS FOR (c:Change) REQUIRE c.change_id IS UNIQUE",
            "CREATE CONSTRAINT course_slug IF NOT EXISTS FOR (c:Course) REQUIRE c.course_slug IS UNIQUE"
        ]
        
        for constraint in constraints:
            try:
                self.db.run(constraint)
            except Exception:
                pass  # Constraint already exists
        
        print("✅ Webtext document constraints created")
    
    def _create_indexes(self):
        """Create indexes for better search performance"""
        indexes = [
            "CREATE INDEX resource_type IF NOT EXISTS FOR (r:Resource) ON (r.resource_type)",
            "CREATE INDEX webtextversion_source IF NOT EXISTS FOR (w:WebtextVersion) ON (w.source)",
            "CREATE INDEX chunkunit_section_title IF NOT EXISTS FOR (c:ChunkUnit) ON (c.section_title)",
            "CREATE INDEX chunkunit_section_index IF NOT EXISTS FOR (c:ChunkUnit) ON (c.section_index)",
            "CREATE INDEX change_type IF NOT EXISTS FOR (c:Change) ON (c.change_type)"
        ]
        
        for index in indexes:
            try:
                self.db.run(index)
            except Exception:
                pass  # Index already exists
        
        print("✅ Webtext document indexes created")
    
    def _create_vector_index(self):
        """Create vector index for ChunkUnit embeddings"""
        try:
            self.db.run("""
                CREATE VECTOR INDEX chunkunit_embedding IF NOT EXISTS
                FOR (c:ChunkUnit) ON (c.embedding)
                OPTIONS {
                    indexConfig: {
                        `vector.dimensions`: $dims,
                        `vector.similarity_function`: 'cosine'
                    }
                }
            """, dims=self.embedder.dims)
            print(f"✅ ChunkUnit vector index (dims={self.embedder.dims}) ready")
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
        Create or update Resource node (webtext), returns resource_id.
        
        IMPORTANT: Resource is now identified by resource_id from pairs/config.
        Each pair represents the same resource across different versions.
        The resource_id is deterministic and shared across all versions of the same resource.
        
        Args:
            resource_name: Clean name for the resource
            resource_id: Unique identifier for this resource
            course_slug: Course slug for linking
        """
        self.db.run("""
            MERGE (r:Resource {resource_id: $resource_id})
            SET r.resource_type = 'webtext',
                r.resource_name = $resource_name,
                r.course_slug = $course_slug
        """, {
            'resource_id': resource_id,
            'resource_name': resource_name,
            'course_slug': course_slug
        })
        
        return resource_id
    
    def _create_webtextversion(self, chunks_data: List[Dict[str, Any]], 
                              resource_id: str, generate_embeddings: bool = True,
                              moodle_url: Optional[str] = None,
                              is_latest: bool = False) -> str:
        """
        Create WebtextVersion node and all its ChunkUnits.
        
        Args:
            chunks_data: List of chunk dictionaries (from chunked alignment file)
            resource_id: ID of the parent Resource
            generate_embeddings: If True, generate embeddings for ChunkUnits (default: True)
            moodle_url: Moodle URL (overrides metadata if provided)
            is_latest: If True, marks this version as latest (searchable via fulltext/vector)
        
        Returns:
            webtextversion_id
        """
        if not chunks_data:
            raise ValueError("No chunks data provided")
        
        # Extract metadata from first chunk
        first_chunk = chunks_data[0]
        metadata = first_chunk.get('metadata', {})
        
        # Generate webtextversion_id from source + course_id + ingest timestamp
        source = metadata.get('source', 'unknown')
        course_id = metadata.get('course_id', 'unknown')
        ingest_ts = metadata.get('timestamp', datetime.now(timezone.utc).isoformat())
        webtextversion_id = hashlib.md5(f"{source}:{course_id}:{ingest_ts}".encode()).hexdigest()
        
        # Use provided URL or fall back to metadata
        final_moodle_url = moodle_url if moodle_url is not None else metadata.get('source')
        
        # Count unique sections
        unique_sections = len(set(chunk.get('section_index', -1) for chunk in chunks_data))
        
        # Create WebtextVersion node
        self.db.run("""
            MERGE (w:WebtextVersion {webtextversion_id: $webtextversion_id})
            SET w.source = $source,
                w.page_type = $page_type,
                w.moodle_url = $moodle_url,
                w.ingest_ts = $ingest_ts,
                w.section_count = $section_count,
                w.chunk_count = $chunk_count,
                w.course_id = $course_id,
                w.course_name = $course_name,
                w.course_semester = $course_semester,
                w.is_latest = $is_latest,
                w.created_at = datetime()
        """, {
            'webtextversion_id': webtextversion_id,
            'source': source,
            'page_type': metadata.get('page_type', 'unknown'),
            'moodle_url': final_moodle_url,
            'ingest_ts': ingest_ts,
            'section_count': unique_sections,
            'chunk_count': len(chunks_data),
            'course_id': course_id,
            'course_name': metadata.get('course_name'),
            'course_semester': metadata.get('course_semester'),
            'is_latest': is_latest
        })
        
        # Link Resource -> WebtextVersion and update Resource URL
        # Keep the most recent URL on Resource for chatbot source references
        self.db.run("""
            MATCH (r:Resource {resource_id: $resource_id})
            MATCH (w:WebtextVersion {webtextversion_id: $webtextversion_id})
            MERGE (r)-[:HAS_VERSION]->(w)
            SET r.moodle_url = $moodle_url
        """, {
            'resource_id': resource_id,
            'webtextversion_id': webtextversion_id,
            'moodle_url': final_moodle_url
        })
        
        # Create ChunkUnits (with or without embeddings)
        if generate_embeddings:
            print(f"📄 Creating {len(chunks_data)} ChunkUnits with embeddings...")
        else:
            print(f"📄 Creating {len(chunks_data)} ChunkUnits (no embeddings)...")
        self._create_chunkunits(chunks_data, webtextversion_id, generate_embeddings)
        
        return webtextversion_id
    
    def _create_chunkunits(self, chunks_data: List[Dict[str, Any]], webtextversion_id: str, 
                          generate_embeddings: bool = True):
        """
        Create ChunkUnit nodes and link them to WebtextVersion.
        
        IMPORTANT: ChunkUnits are the retrievable units (like SlideUnit for PDFs).
        Each chunk contains ~600 tokens of content from a webtext section.
        ChunkUnits preserve section context and are embedded for semantic search.
        
        Args:
            chunks_data: List of chunk dictionaries (from chunked alignment file)
            webtextversion_id: ID of the parent WebtextVersion
            generate_embeddings: If True, generate and store embeddings (default: True)
        """
        chunk_texts = []
        chunk_ids = []
        
        for chunk in chunks_data:
            section_title = chunk.get('section_title', '')
            section_index = chunk.get('section_index', 0)
            chunk_index = chunk.get('chunk_index', 0)
            content = chunk.get('content', '')
            
            # Generate unique chunkunit_id
            chunk_id = hashlib.md5(
                f"{section_title}:{section_index}:{chunk_index}:{content[:100]}".encode()
            ).hexdigest()
            chunkunit_id = f"{webtextversion_id}:{chunk_id}"
            chunk_ids.append(chunkunit_id)
            
            # Prepare text for embedding (title + content)
            combined_text = f"{section_title}\n\n{content}".strip()
            chunk_texts.append(combined_text if combined_text else "Empty chunk")
            
            # Create ChunkUnit node (without embedding first)
            self.db.run("""
                MERGE (c:ChunkUnit {chunkunit_id: $chunkunit_id})
                SET c.section_title = $section_title,
                    c.section_index = $section_index,
                    c.chunk_index = $chunk_index,
                    c.total_chunks = $total_chunks,
                    c.content = $content,
                    c.content_chars = $content_chars,
                    c.content_tokens = $content_tokens,
                    c.tables = $tables,
                    c.links = $links,
                    c.created_at = datetime()
            """, {
                'chunkunit_id': chunkunit_id,
                'section_title': section_title,
                'section_index': section_index,
                'chunk_index': chunk_index,
                'total_chunks': chunk.get('total_chunks', 1),
                'content': content,
                'content_chars': chunk.get('content_chars', len(content)),
                'content_tokens': chunk.get('content_tokens', len(content) // 4),
                'tables': json.dumps(chunk.get('tables', [])),
                'links': json.dumps(chunk.get('links', []))
            })
            
            # Link WebtextVersion -> ChunkUnit
            self.db.run("""
                MATCH (w:WebtextVersion {webtextversion_id: $webtextversion_id})
                MATCH (c:ChunkUnit {chunkunit_id: $chunkunit_id})
                MERGE (w)-[:HAS_CHUNK]->(c)
            """, {
                'webtextversion_id': webtextversion_id,
                'chunkunit_id': chunkunit_id
            })
        
        # Create navigation relationships between ChunkUnits (NEXT_CHUNK/PREV_CHUNK)
        print("🔗 Creating navigation relationships between ChunkUnits...")
        for i in range(len(chunks_data)):
            current_chunkunit_id = chunk_ids[i]
            
            # Link to previous chunk
            if i > 0:
                prev_chunkunit_id = chunk_ids[i - 1]
                self.db.run("""
                    MATCH (current:ChunkUnit {chunkunit_id: $current_id})
                    MATCH (prev:ChunkUnit {chunkunit_id: $prev_id})
                    MERGE (current)-[:PREV_CHUNK]->(prev)
                """, {
                    'current_id': current_chunkunit_id,
                    'prev_id': prev_chunkunit_id
                })
            
            # Link to next chunk
            if i < len(chunks_data) - 1:
                next_chunkunit_id = chunk_ids[i + 1]
                self.db.run("""
                    MATCH (current:ChunkUnit {chunkunit_id: $current_id})
                    MATCH (next:ChunkUnit {chunkunit_id: $next_id})
                    MERGE (current)-[:NEXT_CHUNK]->(next)
                """, {
                    'current_id': current_chunkunit_id,
                    'next_id': next_chunkunit_id
                })
        
        # Generate embeddings in batch only if requested
        if generate_embeddings:
            print(f"🔢 Generating embeddings for {len(chunk_texts)} chunks...")
            embeddings = self.embedder.embed_list(chunk_texts)
            
            # Store embeddings
            for chunkunit_id, embedding in zip(chunk_ids, embeddings, strict=False):
                self.db.run("""
                    MATCH (c:ChunkUnit {chunkunit_id: $chunkunit_id})
                    SET c.embedding = $embedding
                """, {
                    'chunkunit_id': chunkunit_id,
                    'embedding': embedding.tolist()
                })
            
            print(f"✅ Created {len(chunks_data)} ChunkUnits with embeddings")
        else:
            print(f"✅ Created {len(chunks_data)} ChunkUnits (embeddings skipped)")
    
    def _create_changes(self, changes_data: Dict[str, Any], 
                       old_webtextversion_id: str, new_webtextversion_id: str):
        """
        Create Change nodes tracking differences between two WebtextVersions.
        Changes track section-level modifications, but link to ChunkUnits for retrieval.
        """
        detailed_changes = changes_data.get('detailed_changes', [])
        
        if not detailed_changes:
            print("⚠️ No detailed changes found in changes data")
            return
        
        print(f"📊 Creating {len(detailed_changes)} Change nodes...")
        
        # Get ChunkUnit mappings for both versions (by section index)
        old_chunks_by_section = self._get_chunkunit_mapping(old_webtextversion_id)
        new_chunks_by_section = self._get_chunkunit_mapping(new_webtextversion_id)
        
        for change in detailed_changes:
            change_type = change.get('change_type')
            old_idx = change.get('old_section_index')
            new_idx = change.get('new_section_index')
            old_indices = change.get('old_section_indices', [])
            new_indices = change.get('new_section_indices', [])
            
            # Generate unique change_id
            change_id_base = f"{old_webtextversion_id}:{new_webtextversion_id}:{change_type}"
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
                    c.old_section_index = $old_section_index,
                    c.new_section_index = $new_section_index,
                    c.old_section_indices = $old_section_indices,
                    c.new_section_indices = $new_section_indices,
                    c.similarity_score = $similarity_score,
                    c.section_title = $section_title,
                    c.changes_summary = $changes_summary,
                    c.note = $note,
                    c.chunks_count = $chunks_count,
                    c.created_at = datetime()
            """, {
                'change_id': change_id,
                'change_type': change_type,
                'old_section_index': old_idx,
                'new_section_index': new_idx,
                'old_section_indices': old_indices,
                'new_section_indices': new_indices,
                'similarity_score': change.get('similarity_score'),
                'section_title': change.get('section_title'),
                'changes_summary': json.dumps(change.get('changes', [])),
                'note': change.get('note'),
                'chunks_count': json.dumps(change.get('chunks_count', {}))
            })
            
            # Link Change to old WebtextVersion
            self.db.run("""
                MATCH (c:Change {change_id: $change_id})
                MATCH (w:WebtextVersion {webtextversion_id: $old_webtextversion_id})
                MERGE (c)-[:FROM_VERSION]->(w)
            """, {
                'change_id': change_id,
                'old_webtextversion_id': old_webtextversion_id
            })
            
            # Link Change to new WebtextVersion
            self.db.run("""
                MATCH (c:Change {change_id: $change_id})
                MATCH (w:WebtextVersion {webtextversion_id: $new_webtextversion_id})
                MERGE (c)-[:TO_VERSION]->(w)
            """, {
                'change_id': change_id,
                'new_webtextversion_id': new_webtextversion_id
            })
            
            # Create FROM_CHUNK relationships (link all chunks from affected sections)
            # Handle single old section
            if old_idx is not None and old_idx in old_chunks_by_section:
                for chunk_id in old_chunks_by_section[old_idx]:
                    self._link_change_to_chunk(change_id, chunk_id, 'FROM_CHUNK')
            
            # Handle multiple old sections (merge case)
            for idx in old_indices:
                if idx in old_chunks_by_section:
                    for chunk_id in old_chunks_by_section[idx]:
                        self._link_change_to_chunk(change_id, chunk_id, 'FROM_CHUNK')
            
            # Create TO_CHUNK relationships (link all chunks from affected sections)
            # Handle single new section
            if new_idx is not None and new_idx in new_chunks_by_section:
                for chunk_id in new_chunks_by_section[new_idx]:
                    self._link_change_to_chunk(change_id, chunk_id, 'TO_CHUNK')
            
            # Handle multiple new sections (split case)
            for idx in new_indices:
                if idx in new_chunks_by_section:
                    for chunk_id in new_chunks_by_section[idx]:
                        self._link_change_to_chunk(change_id, chunk_id, 'TO_CHUNK')
        
        print(f"✅ Created {len(detailed_changes)} Change nodes with relationships")
    
    def _get_chunkunit_mapping(self, webtextversion_id: str) -> Dict[int, List[str]]:
        """Get mapping of section_index -> list of chunkunit_ids for a WebtextVersion"""
        result = self.db.run("""
            MATCH (w:WebtextVersion {webtextversion_id: $webtextversion_id})-[:HAS_CHUNK]->(c:ChunkUnit)
            RETURN c.chunkunit_id as chunkunit_id, c.section_index as section_index
            ORDER BY c.section_index, c.chunk_index
        """, webtextversion_id=webtextversion_id)
        
        # Create mapping: section_index -> [chunk_ids]
        mapping = {}
        for record in result:
            section_index = record['section_index']
            if section_index is not None:
                if section_index not in mapping:
                    mapping[section_index] = []
                mapping[section_index].append(record['chunkunit_id'])
        
        return mapping
    
    def _link_change_to_chunk(self, change_id: str, chunkunit_id: str, rel_type: str):
        """Create relationship between Change and ChunkUnit"""
        self.db.run(f"""
            MATCH (c:Change {{change_id: $change_id}})
            MATCH (ch:ChunkUnit {{chunkunit_id: $chunkunit_id}})
            MERGE (c)-[:{rel_type}]->(ch)
        """, {
            'change_id': change_id,
            'chunkunit_id': chunkunit_id
        })
    
    def upload_webtextversion(self, chunked_json_file: str, 
                             resource_id: Optional[str] = None,
                             resource_name: Optional[str] = None,
                             generate_embeddings: bool = True) -> str:
        """
        Upload a Webtext WebtextVersion from processed chunked JSON file.
        Creates Course, Semester, Resource, WebtextVersion, and ChunkUnit nodes.
        
        Args:
            chunked_json_file: Path to chunked JSON file with chunk data
            resource_id: Resource ID from config. If None, uses source URL hash.
            resource_name: Clean resource name. If None, uses course name + "Webtext".
            generate_embeddings: If True, generate embeddings for ChunkUnits (default: True).
                                Set to False for old versions when only newest version needs embeddings.
            
        Returns:
            webtextversion_id
        """
        print(f"\n📄 Loading Webtext document: {chunked_json_file}")
        
        with open(chunked_json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Extract chunks from chunked alignment format
        if 'chunked_sections' in data:
            # This is a chunked alignment file - extract chunks from one side
            chunks_data = []
            for chunked_section in data['chunked_sections']:
                
                # Extract old chunks
                if 'old_chunks' in chunked_section:
                    chunks_data.extend(chunked_section['old_chunks'])
                elif 'old_chunks_list' in chunked_section:  # merge case
                    for chunk_list in chunked_section['old_chunks_list']:
                        chunks_data.extend(chunk_list)
                
                # Extract new chunks (if no old chunks)
                if not any(key in chunked_section for key in ['old_chunks', 'old_chunks_list']):
                    if 'new_chunks' in chunked_section:
                        chunks_data.extend(chunked_section['new_chunks'])
                    elif 'new_chunks_list' in chunked_section:  # split case
                        for chunk_list in chunked_section['new_chunks_list']:
                            chunks_data.extend(chunk_list)
        else:
            # This is raw chunks data
            chunks_data = data if isinstance(data, list) else []
        
        if not chunks_data:
            raise ValueError(f"No chunks found in {chunked_json_file}")
        
        # Extract metadata from first chunk
        metadata = chunks_data[0].get('metadata', {})
        course_id = metadata.get('course_id')
        course_name = metadata.get('course_name')
        course_semester = metadata.get('course_semester')
        source = metadata.get('source')
        
        if not all([course_id, course_name, course_semester]):
            raise ValueError("Missing required metadata in chunks JSON")
        
        print(f"   Course: {course_name}")
        print(f"   Semester: {course_semester}")
        print(f"   Source: {source}")
        print(f"   Chunks: {len(chunks_data)}")
        
        # Create Course and Semester
        course_slug = self._create_or_update_course(course_name)
        self._create_or_update_semester(course_id, course_name, course_semester, course_slug)
        
        # Create or link Resource
        # If resource_id/name not provided, generate from course_slug
        if resource_id is None:
            resource_id = hashlib.md5(f"{course_slug}:webtext".encode()).hexdigest()
        if resource_name is None:
            resource_name = f"{course_name} - Webtext"
            
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
        
        # Store metadata in WebtextVersion
        webtextversion_id = self._create_webtextversion(
            chunks_data, final_resource_id, generate_embeddings,
            moodle_url=source, is_latest=generate_embeddings  # is_latest same as generate_embeddings
        )
        
        embedding_status = "with embeddings" if generate_embeddings else "without embeddings"
        print(f"✅ Uploaded WebtextVersion ({embedding_status}): {webtextversion_id}")
        return webtextversion_id
    
    def upload_changes(self, changes_json_file: str, 
                      old_webtextversion_id: str, new_webtextversion_id: str):
        """
        Upload Change nodes tracking differences between two WebtextVersions.
        
        Args:
            changes_json_file: Path to changes JSON file
            old_webtextversion_id: ID of old WebtextVersion
            new_webtextversion_id: ID of new WebtextVersion
        """
        print(f"\n📊 Loading changes: {changes_json_file}")
        
        with open(changes_json_file, 'r', encoding='utf-8') as f:
            changes_data = json.load(f)
        
        metadata = changes_data.get('metadata', {})
        print(f"   Old sections: {metadata.get('old_sections_count')}")
        print(f"   New sections: {metadata.get('new_sections_count')}")
        stats = metadata.get('statistics', {})
        print(f"   Changes: {stats.get('modified', 0)} modified, "
              f"{stats.get('added', 0)} added, {stats.get('removed', 0)} removed")
        
        self._create_changes(changes_data, old_webtextversion_id, new_webtextversion_id)
        
        print("✅ Uploaded changes between versions")
    
    def get_webtextversion_stats(self) -> Dict[str, int]:
        """Get statistics about uploaded webtext documents"""
        stats = {}
        
        result = self.db.run("MATCH (c:Course) RETURN count(c) as count")
        stats['courses'] = result.single()['count']
        
        result = self.db.run("MATCH (s:Semester) RETURN count(s) as count")
        stats['semesters'] = result.single()['count']
        
        result = self.db.run("MATCH (r:Resource {resource_type: 'webtext'}) RETURN count(r) as count")
        stats['resources'] = result.single()['count']
        
        result = self.db.run("MATCH (w:WebtextVersion) RETURN count(w) as count")
        stats['webtextversions'] = result.single()['count']
        
        result = self.db.run("MATCH (c:ChunkUnit) RETURN count(c) as count")
        stats['chunkunits'] = result.single()['count']
        
        result = self.db.run("MATCH (c:Change) RETURN count(c) as count")
        stats['changes'] = result.single()['count']
        
        return stats
    
    def clear_all_webtext_data(self):
        """Delete all webtext-related data (WebtextVersions, ChunkUnits, Changes)"""
        print("🗑️  Clearing all webtext data from database...")
        
        # Delete all changes related to webtext
        result = self.db.run("""
            MATCH (c:Change)
            WHERE EXISTS((c)-[:TO_CHUNK]->()) OR EXISTS((c)-[:FROM_CHUNK]->())
            DETACH DELETE c
            RETURN count(c) as deleted_changes
        """)
        deleted_changes = result.single()['deleted_changes']
        print(f"   ✅ Deleted {deleted_changes} Change nodes")
        
        # Delete all ChunkUnits
        result = self.db.run("""
            MATCH (c:ChunkUnit)
            DETACH DELETE c
            RETURN count(c) as deleted_chunks
        """)
        deleted_chunks = result.single()['deleted_chunks']
        print(f"   ✅ Deleted {deleted_chunks} ChunkUnit nodes")
        
        # Delete all WebtextVersions
        result = self.db.run("""
            MATCH (w:WebtextVersion)
            DETACH DELETE w
            RETURN count(w) as deleted_versions
        """)
        deleted_versions = result.single()['deleted_versions']
        print(f"   ✅ Deleted {deleted_versions} WebtextVersion nodes")
        
        # Note: We keep Resources, Semesters, and Courses as they may be shared with other content
        print("   ℹ️  Kept Resources, Semesters, and Courses (may be shared)")
        
        return {
            'deleted_changes': deleted_changes,
            'deleted_chunks': deleted_chunks,
            'deleted_versions': deleted_versions
        }
    
    def remove_embeddings_from_webtextversion(self, webtextversion_id: str):
        """Remove all embeddings from ChunkUnits of a specific WebtextVersion"""
        result = self.db.run("""
            MATCH (w:WebtextVersion {webtextversion_id: $webtextversion_id})-[:HAS_CHUNK]->(c:ChunkUnit)
            WHERE c.embedding IS NOT NULL
            SET c.embedding = NULL
            RETURN count(c) as removed_count
        """, webtextversion_id=webtextversion_id)
        
        removed_count = result.single()['removed_count']
        if removed_count > 0:
            print(f"   🗑️  Removed embeddings from {removed_count} ChunkUnits")
        return removed_count
    
    def generate_embeddings_chunkunits(self, batch_size: int = 100):
        """Generate embeddings for all ChunkUnits that don't have them (idempotent)"""
        total_result = self.db.run("""
            MATCH (c:ChunkUnit) 
            WHERE c.embedding IS NULL 
            RETURN count(c) as total
        """)
        total_chunks = total_result.single()['total']
        
        if total_chunks == 0:
            print("✅ All chunks already have embeddings!")
            return
        
        print(f"🔢 Generating embeddings for {total_chunks} chunks...")
        
        processed = 0
        batch_count = 0
        
        while processed < total_chunks:
            batch_count += 1
            print(f"   🔄 Batch {batch_count}: Processing chunks {processed + 1}-{min(processed + batch_size, total_chunks)}...")
            
            # Get batch of chunks without embeddings
            result = self.db.run("""
                MATCH (c:ChunkUnit)
                WHERE c.embedding IS NULL
                RETURN c.chunkunit_id as chunkunit_id,
                       c.section_title as section_title,
                       c.content as content
                LIMIT $batch_size
            """, batch_size=batch_size)
            
            batch_chunks = list(result)
            
            if not batch_chunks:
                break
            
            # Prepare texts for embedding (combine title + content)
            texts = []
            chunk_ids = []
            
            for chunk in batch_chunks:
                title = chunk['section_title'] or ''
                content = chunk['content'] or ''
                combined_text = f"{title}\n\n{content}".strip()
                texts.append(combined_text if combined_text else "Empty chunk")
                chunk_ids.append(chunk['chunkunit_id'])
            
            # Generate embeddings for the batch
            embeddings = self.embedder.embed_list(texts)
            
            # Store embeddings back to Neo4j
            for chunk_id, embedding in zip(chunk_ids, embeddings, strict=False):
                self.db.run("""
                    MATCH (c:ChunkUnit {chunkunit_id: $chunkunit_id})
                    SET c.embedding = $embedding
                """,
                    chunkunit_id=chunk_id,
                    embedding=embedding.tolist()
                )
            
            processed += len(batch_chunks)
            progress = (processed / total_chunks) * 100
            print(f"   ✅ Batch {batch_count} complete! Progress: {processed}/{total_chunks} ({progress:.1f}%)")
        
        print(f"🎉 All {total_chunks} chunks now have embeddings!")
    
    def upload_from_comparison_dir(self, comparison_dir: Path, 
                                   resource_id: str,
                                   resource_name: str) -> Dict[str, List[str]]:
        """
        Upload webtext versions from a comparison directory.
        Only the newest version gets embeddings.
        
        Args:
            comparison_dir: Directory containing chunked alignment and changes files
                           (e.g., data/comparison/webtext/introprog_webtext_v2_v3/)
            resource_id: Resource ID for this webtext resource
            resource_name: Clean resource name
            
        Returns:
            Dict with 'uploaded' list of webtextversion_ids and 'newest' list
            
        Expected structure:
            comparison_dir/
                chunked_40280_43321.json  (contains old and new chunks)
                changes_40280_43321.json
        """
        print(f"\n📦 Loading from comparison directory: {comparison_dir}")
        
        # Find chunked alignment file
        chunked_files = list(comparison_dir.glob("chunked_*.json"))
        if not chunked_files:
            raise ValueError(f"No chunked file found in {comparison_dir}")
        
        chunked_file = chunked_files[0]
        print(f"   Found chunked file: {chunked_file.name}")
        
        # Load chunked data to extract course IDs
        with open(chunked_file, 'r', encoding='utf-8') as f:
            chunked_data = json.load(f)
        
        metadata = chunked_data.get('comparison_metadata', {})
        old_course_id = metadata.get('old_course_id')
        new_course_id = metadata.get('new_course_id')
        
        # Extract old and new chunks from chunked alignment
        old_chunks = []
        new_chunks = []
        
        for chunked_section in chunked_data['chunked_sections']:
            # Extract old chunks
            if 'old_chunks' in chunked_section:
                old_chunks.extend(chunked_section['old_chunks'])
            elif 'old_chunks_list' in chunked_section:  # merge case
                for chunk_list in chunked_section['old_chunks_list']:
                    old_chunks.extend(chunk_list)
            
            # Extract new chunks
            if 'new_chunks' in chunked_section:
                new_chunks.extend(chunked_section['new_chunks'])
            elif 'new_chunks_list' in chunked_section:  # split case
                for chunk_list in chunked_section['new_chunks_list']:
                    new_chunks.extend(chunk_list)
        
        uploaded_ids = []
        
        # Upload old version WITHOUT embeddings
        if old_chunks:
            print(f"\n   🔵 Old version (course {old_course_id})...")
            print(f"      Chunks: {len(old_chunks)}")
            # Create temp file with old chunks
            old_temp_file = comparison_dir / f"_temp_old_chunks_{old_course_id}.json"
            with open(old_temp_file, 'w', encoding='utf-8') as f:
                json.dump(old_chunks, f, ensure_ascii=False, indent=2)
            
            try:
                old_id = self.upload_webtextversion(
                    str(old_temp_file),
                    resource_id=resource_id,
                    resource_name=resource_name,
                    generate_embeddings=False
                )
                uploaded_ids.append(old_id)
            finally:
                old_temp_file.unlink()  # Clean up temp file
        
        # Upload new version WITH embeddings
        if new_chunks:
            print(f"\n   🟢 Newest version (course {new_course_id})...")
            print(f"      Chunks: {len(new_chunks)}")
            # Create temp file with new chunks
            new_temp_file = comparison_dir / f"_temp_new_chunks_{new_course_id}.json"
            with open(new_temp_file, 'w', encoding='utf-8') as f:
                json.dump(new_chunks, f, ensure_ascii=False, indent=2)
            
            try:
                new_id = self.upload_webtextversion(
                    str(new_temp_file),
                    resource_id=resource_id,
                    resource_name=resource_name,
                    generate_embeddings=True
                )
                uploaded_ids.append(new_id)
                newest_ids = [new_id]
            finally:
                new_temp_file.unlink()  # Clean up temp file
        
        # Upload changes if they exist
        changes_files = list(comparison_dir.glob("changes_*.json"))
        if changes_files:
            changes_file = changes_files[0]
            print(f"\n   📊 Uploading changes from {changes_file.name}...")
            self.upload_changes(str(changes_file), old_id, new_id)
        
        print(f"\n✅ Uploaded {len(uploaded_ids)} WebtextVersions:")
        print(f"   - {len(newest_ids)} newest version(s) with embeddings")
        print(f"   - {len(uploaded_ids) - len(newest_ids)} older version(s) without embeddings")
        
        return {'uploaded': uploaded_ids, 'newest': newest_ids}

    def upload_all_versions_from_webtext_dir(self, 
                                             webtext_base_dir: Path,
                                             resource_id: str,
                                             resource_name: str) -> Dict[str, List[str]]:
        """
        Upload ALL webtext versions from the webtext comparison base directory.
        Finds all version comparison directories (v1_v2, v2_v3, etc.) and uploads them in order.
        Only the final newest version gets embeddings.
        
        Args:
            webtext_base_dir: Base directory containing all version comparisons
                             (e.g., data/comparison/webtext/)
            resource_id: Resource ID for this webtext resource
            resource_name: Clean resource name
            
        Returns:
            Dict with 'uploaded' list of all webtextversion_ids and 'newest' list
            
        Expected structure:
            webtext_base_dir/
                introprog_webtext_v1_v2/
                    chunked_40280_42290.json
                    changes_40280_42290.json
                introprog_webtext_v2_v3/
                    chunked_42290_43321.json
                    changes_42290_43321.json
                [... more version directories ...]
        """
        print(f"\n🌐 Uploading ALL webtext versions from: {webtext_base_dir}")
        
        # Find all comparison directories (pattern: *_v*_v*)
        import re
        comparison_dirs = []
        for item in webtext_base_dir.iterdir():
            if item.is_dir() and re.search(r'_v\d+_v\d+', item.name):
                comparison_dirs.append(item)
        
        if not comparison_dirs:
            raise ValueError(f"No version comparison directories found in {webtext_base_dir}")
        
        # Sort directories by version numbers to ensure chronological order
        # Extract version numbers from directory names like "introprog_webtext_v1_v2"
        def extract_version_nums(dir_path):
            match = re.search(r'_v(\d+)_v(\d+)', dir_path.name)
            if match:
                return (int(match.group(1)), int(match.group(2)))
            return (0, 0)
        
        comparison_dirs.sort(key=extract_version_nums)
        
        print(f"   Found {len(comparison_dirs)} version comparison directories:")
        for i, dir_path in enumerate(comparison_dirs, 1):
            v1, v2 = extract_version_nums(dir_path)
            print(f"      {i}. {dir_path.name} (v{v1} → v{v2})")
        
        all_uploaded = []
        all_course_ids_seen = set()
        final_newest = []
        
        # Process each comparison directory
        for idx, comp_dir in enumerate(comparison_dirs, 1):
            print(f"\n{'='*70}")
            print(f"📂 Processing directory {idx}/{len(comparison_dirs)}: {comp_dir.name}")
            print(f"{'='*70}")
            
            # Determine if this is the last directory (final version gets embeddings)
            is_final = (idx == len(comparison_dirs))
            
            # Load chunked file to extract course IDs and chunks
            chunked_files = list(comp_dir.glob("chunked_*.json"))
            if not chunked_files:
                print("   ⚠️ No chunked file found, skipping...")
                continue
            
            chunked_file = chunked_files[0]
            print(f"   Found: {chunked_file.name}")
            
            with open(chunked_file, 'r', encoding='utf-8') as f:
                chunked_data = json.load(f)
            
            metadata = chunked_data.get('comparison_metadata', {})
            old_course_id = metadata.get('old_course_id')
            new_course_id = metadata.get('new_course_id')
            
            # Extract old and new chunks
            old_chunks = []
            new_chunks = []
            
            for chunked_section in chunked_data['chunked_sections']:
                # Extract old chunks
                if 'old_chunks' in chunked_section:
                    old_chunks.extend(chunked_section['old_chunks'])
                elif 'old_chunks_list' in chunked_section:
                    for chunk_list in chunked_section['old_chunks_list']:
                        old_chunks.extend(chunk_list)
                
                # Extract new chunks
                if 'new_chunks' in chunked_section:
                    new_chunks.extend(chunked_section['new_chunks'])
                elif 'new_chunks_list' in chunked_section:
                    for chunk_list in chunked_section['new_chunks_list']:
                        new_chunks.extend(chunk_list)
            
            # Upload old version if not already uploaded
            old_id = None
            if old_course_id and old_course_id not in all_course_ids_seen and old_chunks:
                print(f"\n   🔵 Uploading OLD version (course {old_course_id})...")
                print(f"      Chunks: {len(old_chunks)}")
                
                old_temp_file = comp_dir / f"_temp_old_{old_course_id}.json"
                with open(old_temp_file, 'w', encoding='utf-8') as f:
                    json.dump(old_chunks, f, ensure_ascii=False, indent=2)
                
                try:
                    old_id = self.upload_webtextversion(
                        str(old_temp_file),
                        resource_id=resource_id,
                        resource_name=resource_name,
                        generate_embeddings=False  # Old versions never get embeddings
                    )
                    all_uploaded.append(old_id)
                    all_course_ids_seen.add(old_course_id)
                finally:
                    old_temp_file.unlink()
            elif old_course_id in all_course_ids_seen:
                # Old version already uploaded - look up its webtextversion_id
                print(f"\n   🔵 Old version (course {old_course_id}) already uploaded, fetching ID...")
                result = self.db.run("""
                    MATCH (w:WebtextVersion {course_id: $course_id})
                    RETURN w.webtextversion_id AS webtextversion_id
                    LIMIT 1
                """, course_id=str(old_course_id))
                record = result.single()
                if record:
                    old_id = record['webtextversion_id']
                    print(f"      Found: {old_id}")
            
            # Upload new version
            new_id = None
            if new_course_id and new_chunks:
                # Only embed if this is the final version
                should_embed = is_final
                version_label = "FINAL (with embeddings)" if should_embed else "INTERMEDIATE (no embeddings)"
                
                print(f"\n   🟢 Uploading NEW version (course {new_course_id}) - {version_label}...")
                print(f"      Chunks: {len(new_chunks)}")
                
                new_temp_file = comp_dir / f"_temp_new_{new_course_id}.json"
                with open(new_temp_file, 'w', encoding='utf-8') as f:
                    json.dump(new_chunks, f, ensure_ascii=False, indent=2)
                
                try:
                    new_id = self.upload_webtextversion(
                        str(new_temp_file),
                        resource_id=resource_id,
                        resource_name=resource_name,
                        generate_embeddings=should_embed
                    )
                    all_uploaded.append(new_id)
                    all_course_ids_seen.add(new_course_id)
                    
                    if should_embed:
                        final_newest.append(new_id)
                finally:
                    new_temp_file.unlink()
            
            # Upload changes if both versions exist
            if old_id and new_id:
                changes_files = list(comp_dir.glob("changes_*.json"))
                if changes_files:
                    print(f"\n   📊 Uploading changes from {changes_files[0].name}...")
                    self.upload_changes(str(changes_files[0]), old_id, new_id)
        
        print(f"\n{'='*70}")
        print("✅ UPLOAD COMPLETE")
        print(f"{'='*70}")
        print(f"   Total versions uploaded: {len(all_uploaded)}")
        print(f"   Versions with embeddings: {len(final_newest)}")
        print(f"   Versions without embeddings: {len(all_uploaded) - len(final_newest)}")
        
        return {'uploaded': all_uploaded, 'newest': final_newest}



def demo_upload_from_comparison():
    """Demo function showing how to upload ALL webtext versions from comparison directory"""
    neo = Neo4jConnection()
    embedder = TextEmbedder(neo)
    upserter = WebtextLoader(neo, embedder)
    
    try:
        # Clear existing webtext data first
        print("\n" + "=" * 70)
        upserter.clear_all_webtext_data()
        print("=" * 70)
                
        #comparison_dir = Path("data/comparison/webtext/introprog_webtext_v3_01__v3_02")
        #result = upserter.upload_from_comparison_dir(
        #    comparison_dir,
        #    resource_id="introprog_webtext",
        #    resource_name="Einführung in die Programmierung - Webtext"
        #)
        # Upload ALL versions from webtext base directory
        webtext_base_dir = Path("data/comparison/webtext")
        result = upserter.upload_all_versions_from_webtext_dir(
            webtext_base_dir,
            resource_id="introprog_webtext",
            resource_name="Einführung in die Programmierung - Webtext"
        )
        
        print(f"\n✅ Uploaded {len(result['uploaded'])} WebtextVersions")
        print(f"✅ {len(result['newest'])} newest version(s) have embeddings")
        
        # Print stats
        print("\n" + "=" * 60)
        print("📊 DATABASE STATISTICS")
        print("=" * 60)
        stats = upserter.get_webtextversion_stats()
        for key, value in stats.items():
            print(f"   {key.capitalize()}: {value}")
        
        # Query: Show Resource with all its versions
        print("\n" + "=" * 60)
        print("🔍 RESOURCE VERSIONS QUERY")
        print("=" * 60)
        result = neo.db.run("""
            MATCH (r:Resource {resource_type: 'webtext'})-[:HAS_VERSION]->(w:WebtextVersion)
            OPTIONAL MATCH (s:Semester)-[:HAS_RESOURCE]->(r)
            WITH r, w, collect(DISTINCT s.semester_label) as semesters
            RETURN r.resource_name as resource,
                   r.resource_id as resource_id,
                   r.moodle_url as resource_url,
                   w.course_semester as version_semester,
                   w.section_count as sections,
                   w.chunk_count as chunks,
                   w.moodle_url as version_url,
                   w.ingest_ts as ingest_ts,
                   semesters
            ORDER BY w.course_semester
        """)
        
        for record in result:
            print(f"\n   Resource: {record['resource']}")
            print(f"   Version: {record['version_semester']}")
            print(f"   Sections: {record['sections']}, Chunks: {record['chunks']}")
            print(f"   URL: {record['version_url']}")
            print(f"   Semesters: {', '.join(record['semesters']) if record['semesters'] else 'None'}")
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        neo.close()


if __name__ == "__main__":
    demo_upload_from_comparison()
