# COMBINE VECTOR SEARCH (D_VECTOR_SEARCH.PY) WITH GRAPH TRAVERSAL
from ..neo4jdata.start import Neo4jConnection
from .search_hybrid import HybridSearch
from ..neo4jdata.embedder import TextEmbedder
from ..utils import SearchFilters
from typing import Optional

class GraphRetrieval:    
    def __init__(self, neo_connection: Neo4jConnection, hybrid_search: HybridSearch, embedder: TextEmbedder):
        self.neo = neo_connection
        self.db = neo_connection.db
        self.embedder = embedder
        self.hybrid_search = hybrid_search

    def search_by_author(self, author_name: str, limit: int = 10, include_links: bool = True):
        """Find all posts by a specific author with complete context and links"""
        result = self.db.run("""
            MATCH (u:User {name: $author_name})-[:AUTHORED]->(posts:Post)
            MATCH (posts)-[:BELONGS_TO_THREAD]->(t:Thread)<-[:CONTAINS_THREAD]-(f:Forum)<-[:HAS_FORUM]-(s:Semester)<-[:HAS_SEMESTER]-(c:Course)
            OPTIONAL MATCH (posts)-[:HAS_LINK]->(links:Link)
            
            RETURN posts.post_id AS post_id,
                   posts.subject AS subject,
                   posts.content AS content,
                   posts.datetime AS datetime,
                   posts.permalink AS permalink,
                   posts.is_thread_root AS is_thread_root,
                   posts.is_reply AS is_reply,
                   posts.has_attachments AS has_attachments,
                   c.course_name AS course_name,
                   s.course_id AS course_id,
                   f.name AS forum_name,
                   f.forum_id AS forum_id,
                   t.title AS thread_title,
                   t.thread_id AS thread_id,
                   u.role AS author_role,
                   u.user_id AS author_user_id,
                   collect(DISTINCT {
                       url: links.url,
                       text: links.text
                   }) AS links
            ORDER BY posts.datetime DESC
            LIMIT $limit
        """, author_name=author_name, limit=limit)
        
        return [dict(r) for r in result]
    
    def get_posts_context_batch(self, post_ids: list):
        """Get full context for multiple posts in a single query - EFFICIENT"""
        if not post_ids:
            return {}
        
        result = self.db.run("""
            MATCH (p:Post)-[:BELONGS_TO_THREAD]->(t:Thread)<-[:CONTAINS_THREAD]-(f:Forum)<-[:HAS_FORUM]-(s:Semester)<-[:HAS_SEMESTER]-(c:Course)
            MATCH (u:User)-[:AUTHORED]->(p)
            WHERE p.post_id IN $post_ids
            OPTIONAL MATCH (p)-[:REPLIES_TO]->(original:Post)
            OPTIONAL MATCH (original_author:User)-[:AUTHORED]->(original)
            OPTIONAL MATCH (p)-[:HAS_LINK]->(links:Link)
            RETURN p.post_id AS post_id,
                   p.subject AS subject,
                   p.content AS content,
                   p.datetime AS post_datetime,
                   p.permalink AS permalink,
                   p.is_thread_root AS is_thread_root,
                   p.is_reply AS is_reply,
                   c.course_name AS course_name,
                   s.course_id AS course_id,
                   f.name AS forum_name,
                   f.forum_id AS forum_id,
                   t.title AS thread_title,
                   t.thread_id AS thread_id,
                   u.name AS author_name,
                   u.role AS author_role,
                   original.post_id AS reply_to_post_id,
                   original_author.name AS reply_to_author,
                   collect(DISTINCT {
                       url: links.url,
                       text: links.text,
                       link_id: links.link_id
                   }) AS links
        """, post_ids=post_ids)
        
        # Return as dict mapping post_id -> context
        return {r['post_id']: dict(r) for r in result}

    def get_post_context_single(self, post_id: str):
        """Get full context for a post (course, forum, thread, author)"""
        result = self.db.run("""
            MATCH (p:Post {post_id: $post_id})-[:BELONGS_TO_THREAD]->(t:Thread)<-[:CONTAINS_THREAD]-(f:Forum)<-[:HAS_FORUM]-(s:Semester)<-[:HAS_SEMESTER]-(c:Course)
            MATCH (u:User)-[:AUTHORED]->(p)
            OPTIONAL MATCH (p)-[:REPLIES_TO]->(original:Post)
            OPTIONAL MATCH (original_author:User)-[:AUTHORED]->(original)
            RETURN p.post_id AS post_id,
                    p.subject AS subject,
                    p.content AS content,
                    p.datetime AS post_datetime,
                    p.is_thread_root AS is_thread_root,
                    p.is_reply AS is_reply,
                    c.course_name AS course_name,
                    s.course_id AS course_id,
                    f.name AS forum_name,
                    f.forum_id AS forum_id,
                    t.title AS thread_title,
                    t.thread_id AS thread_id,
                    u.name AS author_name,
                    u.role AS author_role,
                    original.post_id AS reply_to_post_id,
                    original_author.name AS reply_to_author
        """, post_id=post_id)
        
        record = result.single()
        return dict(record) if record else None
    
    def get_slides_context_batch(self, slideunit_ids: list):
        """Get full context for multiple slides in a single query - EFFICIENT"""
        if not slideunit_ids:
            return {}
        
        result = self.db.run("""
            MATCH (s:SlideUnit)<-[:HAS_SLIDE]-(d:DocVersion)<-[:HAS_VERSION]-(r:Resource)
            MATCH (r)<-[:HAS_RESOURCE]-(sem:Semester)<-[:HAS_SEMESTER]-(c:Course)
            WHERE s.slideunit_id IN $slideunit_ids
            
            // Get change information with temporal context
            OPTIONAL MATCH (change:Change)-[:TO_SLIDE]->(s)
            OPTIONAL MATCH (change)-[:FROM_SLIDE]->(old_slide:SlideUnit)
            OPTIONAL MATCH (change)-[:FROM_VERSION]->(old_doc:DocVersion)
            OPTIONAL MATCH (change)-[:TO_VERSION]->(new_doc:DocVersion)
            OPTIONAL MATCH (old_doc)-[:HAS_VERSION]-(r_old:Resource)<-[:HAS_RESOURCE]-(sem_old:Semester)
            
            RETURN s.slideunit_id AS slideunit_id,
                   s.page_number AS page_number,
                   s.title AS title,
                   s.content AS content,
                   s.has_tables AS has_tables,
                   s.urls AS urls,
                   d.document_title AS document_title,
                   d.document_url AS document_url,
                   d.moodle_url AS moodle_url,
                   d.course_semester AS document_semester,
                   d.page_count AS total_pages,
                   r.resource_name AS resource_name,
                   r.resource_id AS resource_id,
                   c.course_name AS course_name,
                   c.course_slug AS course_slug,
                   sem.semester_label AS semester_label,
                   change.change_id AS change_id,
                   change.change_type AS change_type,
                   change.changes_summary AS changes_summary,
                   change.similarity_score AS similarity_score,
                   old_slide.title AS old_slide_title,
                   old_slide.content AS old_slide_content,
                   old_doc.course_semester AS old_semester,
                   new_doc.course_semester AS new_semester,
                   sem_old.semester_label AS old_semester_label
        """, slideunit_ids=slideunit_ids)
        
        # Return as dict mapping slideunit_id -> context
        return {r['slideunit_id']: dict(r) for r in result}

    def get_slide_context_single(self, slideunit_id: str):
        """Get full context for a slide (navigation, changes, resource info)"""
        result = self.db.run("""
            MATCH (s:SlideUnit {slideunit_id: $slideunit_id})<-[:HAS_SLIDE]-(d:DocVersion)<-[:HAS_VERSION]-(r:Resource)
            MATCH (r)<-[:HAS_RESOURCE]-(sem:Semester)<-[:HAS_SEMESTER]-(c:Course)
            
            // Get navigation context
            OPTIONAL MATCH (s)-[:PREV_SLIDE]->(prev:SlideUnit)
            OPTIONAL MATCH (s)-[:NEXT_SLIDE]->(next:SlideUnit)
            
            // Get changes (if this slide was modified from previous version)
            OPTIONAL MATCH (change:Change)-[:TO_SLIDE]->(s)
            OPTIONAL MATCH (change)-[:FROM_SLIDE]->(old_slide:SlideUnit)
            OPTIONAL MATCH (change)-[:FROM_VERSION]->(old_doc:DocVersion)
            
            RETURN s.slideunit_id AS slideunit_id,
                   s.page_number AS page_number,
                   s.title AS title,
                   s.content AS content,
                   s.has_tables AS has_tables,
                   s.urls AS urls,
                   d.document_title AS document_title,
                   d.document_url AS document_url,
                   d.moodle_url AS moodle_url,
                   d.course_semester AS document_semester,
                   d.page_count AS total_pages,
                   r.resource_name AS resource_name,
                   r.resource_id AS resource_id,
                   c.course_name AS course_name,
                   c.course_slug AS course_slug,
                   sem.semester_label AS semester_label,
                   prev.slideunit_id AS prev_slide_id,
                   prev.title AS prev_slide_title,
                   next.slideunit_id AS next_slide_id,
                   next.title AS next_slide_title,
                   change.change_type AS change_type,
                   change.similarity_score AS change_similarity,
                   old_slide.title AS old_slide_title,
                   old_doc.course_semester AS old_semester
        """, slideunit_id=slideunit_id)
        
        record = result.single()
        return dict(record) if record else None

    def get_chunks_context_batch(self, chunkunit_ids: list):
        """Get full context for multiple webtext chunks in a single query - EFFICIENT"""
        if not chunkunit_ids:
            return {}
        
        result = self.db.run("""
            MATCH (c:ChunkUnit)<-[:HAS_CHUNK]-(w:WebtextVersion)<-[:HAS_VERSION]-(r:Resource)
            MATCH (r)<-[:HAS_RESOURCE]-(sem:Semester)<-[:HAS_SEMESTER]-(course:Course)
            WHERE c.chunkunit_id IN $chunkunit_ids
            
            // Get change information with temporal context
            OPTIONAL MATCH (change:Change)-[:TO_CHUNK]->(c)
            OPTIONAL MATCH (change)-[:FROM_CHUNK]->(old_chunk:ChunkUnit)
            OPTIONAL MATCH (change)-[:FROM_VERSION]->(old_web:WebtextVersion)
            OPTIONAL MATCH (change)-[:TO_VERSION]->(new_web:WebtextVersion)
            OPTIONAL MATCH (old_web)-[:HAS_VERSION]-(r_old:Resource)<-[:HAS_RESOURCE]-(sem_old:Semester)
            
            RETURN c.chunkunit_id AS chunkunit_id,
                   c.section_title AS section_title,
                   c.content AS content,
                   c.chunk_index AS chunk_index,
                   w.webtextversion_id AS webtextversion_id,
                   w.course_semester AS webtext_semester,
                   w.moodle_url AS moodle_url,
                   r.resource_name AS resource_name,
                   r.resource_id AS resource_id,
                   course.course_name AS course_name,
                   course.course_slug AS course_slug,
                   sem.semester_label AS semester_label,
                   change.change_id AS change_id,
                   change.change_type AS change_type,
                   change.changes_summary AS changes_summary,
                   change.similarity_score AS similarity_score,
                   old_chunk.section_title AS old_chunk_title,
                   old_chunk.content AS old_chunk_content,
                   old_web.course_semester AS old_semester,
                   new_web.course_semester AS new_semester,
                   sem_old.semester_label AS old_semester_label
        """, chunkunit_ids=chunkunit_ids)
        
        # Return as dict mapping chunkunit_id -> context
        return {r['chunkunit_id']: dict(r) for r in result}

    def get_chunk_context_single(self, chunkunit_id: str):
        """Get full context for a chunk (navigation, changes, resource info)"""
        result = self.db.run("""
            MATCH (c:ChunkUnit {chunkunit_id: $chunkunit_id})<-[:HAS_CHUNK]-(w:WebtextVersion)<-[:HAS_VERSION]-(r:Resource)
            MATCH (r)<-[:HAS_RESOURCE]-(sem:Semester)<-[:HAS_SEMESTER]-(course:Course)
            
            // Get navigation context
            OPTIONAL MATCH (c)-[:PREV_CHUNK]->(prev:ChunkUnit)
            OPTIONAL MATCH (c)-[:NEXT_CHUNK]->(next:ChunkUnit)
            
            // Get changes (if this chunk was modified from previous version)
            OPTIONAL MATCH (change:Change)-[:TO_CHUNK]->(c)
            OPTIONAL MATCH (change)-[:FROM_CHUNK]->(old_chunk:ChunkUnit)
            OPTIONAL MATCH (change)-[:FROM_VERSION]->(old_web:WebtextVersion)
            
            RETURN c.chunkunit_id AS chunkunit_id,
                   c.section_title AS section_title,
                   c.content AS content,
                   c.chunk_index AS chunk_index,
                   w.webtextversion_id AS webtextversion_id,
                   w.course_semester AS webtext_semester,
                   w.moodle_url AS moodle_url,
                   r.resource_name AS resource_name,
                   r.resource_id AS resource_id,
                   course.course_name AS course_name,
                   course.course_slug AS course_slug,
                   sem.semester_label AS semester_label,
                   prev.chunkunit_id AS prev_chunk_id,
                   prev.section_title AS prev_chunk_section_title,
                   next.chunkunit_id AS next_chunk_id,
                   next.section_title AS next_chunk_section_title,
                   change.change_type AS change_type,
                   change.similarity_score AS change_similarity,
                   old_chunk.section_title AS old_chunk_section_title,
                   old_web.course_semester AS old_semester
        """, chunkunit_id=chunkunit_id)
        
        record = result.single()
        return dict(record) if record else None


    def search_knowledge_sources(
        self,
        query: str,
        k_posts: int = 3,
        k_slides: int = 3,
        k_chunks: int = 3,
        post_filters: Optional['SearchFilters'] = None,
        course_slug: Optional[str] = None,
        semester: Optional[str] = None
    ):
        """
        Search forum posts, PDF slides, AND webtext chunks simultaneously.
        Returns combined results from all three knowledge sources.
        Uses hybrid search (vector + fulltext) for all sources.
        """
        from ..utils import SearchFilters
        
        post_filters = post_filters or SearchFilters()
        
        # Search posts using HYBRID SEARCH (vector + fulltext)
        post_results = self.hybrid_search.search_posts(
            query,
            limit=k_posts,
            filters=post_filters
        )
        
        # Search slides using HYBRID SEARCH (vector + fulltext)
        slide_results = self.hybrid_search.search_slides(
            query,
            limit=k_slides,
            course_slug=course_slug,
            semester=semester
        )
        
        # Search chunks using HYBRID SEARCH (vector + fulltext)
        chunk_results = self.hybrid_search.search_chunks(
            query,
            limit=k_chunks,
            course_slug=course_slug,
            semester=semester
        )
        
        # Efficient batch fetch context
        post_ids = [r['post_id'] for r in post_results]
        slide_ids = [r['slideunit_id'] for r in slide_results]
        chunk_ids = [r['chunkunit_id'] for r in chunk_results]
        
        post_contexts = self.get_posts_context_batch(post_ids) if post_ids else {}
        slide_contexts = self.get_slides_context_batch(slide_ids) if slide_ids else {}
        chunk_contexts = self.get_chunks_context_batch(chunk_ids) if chunk_ids else {}
        
        # Enhance posts with context
        enhanced_posts = []
        for result in post_results:
            context = post_contexts.get(result['post_id'])
            enhanced_posts.append({
                'type': 'post',
                'vector_result': result,
                'context': context
            })
        
        # Enhance slides with context
        enhanced_slides = []
        for result in slide_results:
            context = slide_contexts.get(result['slideunit_id'])
            enhanced_slides.append({
                'type': 'slide',
                'vector_result': result,
                'context': context
            })
        
        # Enhance chunks with context
        enhanced_chunks = []
        for result in chunk_results:
            context = chunk_contexts.get(result['chunkunit_id'])
            enhanced_chunks.append({
                'type': 'chunk',
                'vector_result': result,
                'context': context
            })
        
        # Combine all results
        all_results = enhanced_posts + enhanced_slides + enhanced_chunks
        
        # Normalize scores across ALL results (posts + slides + chunks) for fair comparison
        if all_results:
            max_score = max(item['vector_result']['score'] for item in all_results)
            if max_score > 0:
                for item in all_results:
                    item['vector_result']['score'] = item['vector_result']['score'] / max_score
        
        # Sort by normalized score
        all_results.sort(key=lambda x: x['vector_result']['score'], reverse=True)
        
        return {
            'query': query,
            'post_results': enhanced_posts,
            'slide_results': enhanced_slides,
            'chunk_results': enhanced_chunks,
            'combined_results': all_results
        }

    
    def get_diff_html_path(self, change_id: str) -> Optional[str]:
        """
        Get the path to the diff HTML file for a Change node.
        Works for both PDF (SlideUnit) and Webtext (ChunkUnit) changes.
        
        Args:
            change_id: The change_id from the Change node
            
        Returns:
            Relative path to the diff HTML file, or None if not found
        """
        import glob
        
        # Query to get resource info and determine change type
        result = self.db.run("""
            MATCH (change:Change {change_id: $change_id})
            
            // Try to match as slide change (PDF)
            OPTIONAL MATCH (change)-[:TO_SLIDE]->(slide:SlideUnit)<-[:HAS_SLIDE]-(doc:DocVersion)<-[:HAS_VERSION]-(r_pdf:Resource)
            
            // Try to match as chunk change (Webtext)  
            OPTIONAL MATCH (change)-[:TO_CHUNK]->(chunk:ChunkUnit)<-[:HAS_CHUNK]-(web:WebtextVersion)<-[:HAS_VERSION]-(r_web:Resource)
            
            RETURN r_pdf.resource_id AS pdf_resource_id,
                   r_web.resource_id AS webtext_resource_id
        """, change_id=change_id)
        
        record = result.single()
        if not record:
            print(f"[DEBUG] No record found for change_id: {change_id}")
            return None
        
        pdf_resource_id = record.get('pdf_resource_id')
        webtext_resource_id = record.get('webtext_resource_id')
        
        print(f"[DEBUG] change_id={change_id}, pdf_resource_id={pdf_resource_id}, webtext_resource_id={webtext_resource_id}")
        
        if pdf_resource_id:
            # This is a PDF change
            # Search for any diff HTML file matching this resource_id pattern
            # Pattern: data/comparison/pdf/{resource_id}_*/diff_{resource_id}_*.html
            new_pattern = f"data/comparison/pdf/{pdf_resource_id}_*/diff_{pdf_resource_id}_*.html"
            matches = glob.glob(new_pattern)
            
            if matches:
                print(f"[DEBUG] Found PDF diff at: {matches[0]}")
                return matches[0]
            
        elif webtext_resource_id:
            # This is a webtext change
            # Search for any diff HTML file matching this resource_id pattern
            # Pattern: data/comparison/webtext/{resource_id}_*/diff_{resource_id}_*.html
            new_pattern = f"data/comparison/webtext/{webtext_resource_id}_*/diff_{webtext_resource_id}_*.html"
            matches = glob.glob(new_pattern)
            
            if matches:
                print(f"[DEBUG] Found webtext diff at: {matches[0]}")
                return matches[0]
            
        else:
            print(f"[DEBUG] Neither pdf_resource_id nor webtext_resource_id found for change_id: {change_id}")
            return None