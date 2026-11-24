from typing import Optional, List, Dict, Any
from ..neo4jdata.start import Neo4jConnection
from ..utils import SearchFilters  
from ..neo4jdata.embedder import TextEmbedder
import re

class HybridSearch:
    """Hybrid search combining vector similarity and fulltext search using RRF"""
    
    def __init__(self, neo_connection: Neo4jConnection, embedder: TextEmbedder):
        self.neo = neo_connection
        self.db = neo_connection.db
        self.embedder = embedder
        self.create_fulltext_index()
    
    def _escape_lucene_query(self, query_text: str) -> str:
        r"""
        Escape special Lucene characters in query text.
        Lucene special characters: + - && || ! ( ) { } [ ] ^ " ~ * ? : \ /
        """
        # Characters that need to be escaped
        special_chars = r'[\+\-\!\(\)\{\}\[\]\^"~\*\?:\\\/]'
        # Escape them with backslash
        escaped = re.sub(special_chars, r'\\\g<0>', query_text)
        # Also escape && and ||
        escaped = escaped.replace('&&', r'\&&').replace('||', r'\||')
        return escaped
    
    def create_fulltext_index(self):
        """Create fulltext indexes for hybrid search on Posts, SlideUnits, and ChunkUnits"""
        try:
            self.db.run("""
                CREATE FULLTEXT INDEX post_fulltext IF NOT EXISTS 
                FOR (p:Post) ON EACH [p.subject, p.content]""")
            print("✅ Post fulltext index ready")
        except Exception:
            pass  # Index already exists
        
        try:
            self.db.run("""
                CREATE FULLTEXT INDEX slide_fulltext IF NOT EXISTS 
                FOR (s:SlideUnit) ON EACH [s.title, s.content]""")
            print("✅ Slide fulltext index ready")
        except Exception:
            pass  # Index already exists
        
        try:
            self.db.run("""
                CREATE FULLTEXT INDEX chunk_fulltext IF NOT EXISTS 
                FOR (c:ChunkUnit) ON EACH [c.section_title, c.content]""")
            print("✅ ChunkUnit fulltext index ready")
        except Exception:
            pass  # Index already exists
    
    def search_posts(
        self,
        query_text: str,
        limit: int = 10,
        filters: Optional[SearchFilters] = None
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search combining vector and fulltext search with RRF.
        This handles cases where:
        - Vector search finds semantically similar content
        - Fulltext search finds exact keyword matches
        - RRF combines both rankings for better results
        """
        filters = filters or SearchFilters()
        
        # Escape special Lucene characters in query text
        escaped_query = self._escape_lucene_query(query_text)
        
        # Get query embedding
        query_embedding = self.embedder.embed_text(query_text)
        
        # Build WHERE clause for filters
        where_parts = []
        if filters.course_id:       where_parts.append("s.course_id = $course_id")
        if filters.semester:        where_parts.append("s.semester_label = $semester")
        if filters.author:          where_parts.append("u.name = $author")
        
        where_clause = f"AND {' AND '.join(where_parts)}" if where_parts else ""
        
        # Strategy: Run vector and fulltext searches separately, then combine        
        # 1. Vector search
        vector_query = f'''
        CALL db.index.vector.queryNodes('post_embedding', $limit, $embedding) 
        YIELD node, score
        MATCH (node)-[:BELONGS_TO_THREAD]->(t:Thread)
        MATCH (t)<-[:CONTAINS_THREAD]-(f:Forum)<-[:HAS_FORUM]-(s:Semester)<-[:HAS_SEMESTER]-(c:Course)
        MATCH (u:User)-[:AUTHORED]->(node)
        WHERE 1=1 {where_clause}
        RETURN node.post_id AS post_id, score, 'vector' AS search_type
        ORDER BY score DESC
        LIMIT $limit
        '''
        
        vector_results = self.db.run(
            vector_query,
            limit=limit,
            embedding=query_embedding.tolist(),
            **filters.to_dict()
        )
        vector_posts = {r['post_id']: (r['score'], idx + 1) for idx, r in enumerate(vector_results)}
        
        # 2. Fulltext search
        fulltext_query = f'''
        CALL db.index.fulltext.queryNodes('post_fulltext', $query_text, {{limit: $limit}})
        YIELD node, score
        MATCH (node)-[:BELONGS_TO_THREAD]->(t:Thread)
        MATCH (t)<-[:CONTAINS_THREAD]-(f:Forum)<-[:HAS_FORUM]-(s:Semester)<-[:HAS_SEMESTER]-(c:Course)
        MATCH (u:User)-[:AUTHORED]->(node)
        WHERE 1=1 {where_clause}
        RETURN node.post_id AS post_id, score, 'fulltext' AS search_type
        ORDER BY score DESC
        LIMIT $limit
        '''
        
        fulltext_results = self.db.run(
            fulltext_query,
            limit=limit,
            query_text=escaped_query,  # Use escaped query for fulltext
            **filters.to_dict()
        )
        fulltext_posts = {r['post_id']: (r['score'], idx + 1) for idx, r in enumerate(fulltext_results)}
        
        # 3. Apply: RRF = sum(1 / (k + rank)) for each search type
        k = 60  # Standard RRF value
        rrf_scores = {}
        all_post_ids = set(vector_posts.keys()) | set(fulltext_posts.keys())
        
        for post_id in all_post_ids:
            rrf_score = 0.0
            search_types = []
            
            if post_id in vector_posts:
                _, rank = vector_posts[post_id]
                rrf_score += 1.0 / (k + rank)
                search_types.append('vector')
            
            if post_id in fulltext_posts:
                _, rank = fulltext_posts[post_id]
                rrf_score += 1.0 / (k + rank)
                search_types.append('fulltext')
            
            rrf_scores[post_id] = {
                'rrf_score': rrf_score,
                'search_types': search_types
            }
        
        # 4. Sort by RRF score and get top results
        top_post_ids = sorted(rrf_scores.keys(), key=lambda pid: rrf_scores[pid]['rrf_score'], reverse=True)[:limit]
        

        if not top_post_ids:
            return []
        
        # Just return post IDs with scores - no context fetching here
        results = []
        for post_id in top_post_ids:
            results.append({
                'post_id': post_id,
                'score': rrf_scores[post_id]['rrf_score'],
                'search_types': rrf_scores[post_id]['search_types']
            })
        
        return results
    
    def search_chunks(
        self,
        query_text: str,
        limit: int = 10,
        course_slug: Optional[str] = None,
        semester: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search for webtext chunks combining vector and fulltext search with RRF.
        ChunkUnits are from webtext sections (~600 tokens each).
        """
        # Escape special Lucene characters in query text
        escaped_query = self._escape_lucene_query(query_text)
        
        # Get query embedding
        query_embedding = self.embedder.embed_text(query_text)
        
        # Build WHERE clause for filters
        where_parts = []
        params = {'limit': limit}
        
        if course_slug:
            where_parts.append("c.course_slug = $course_slug")
            params['course_slug'] = course_slug
        
        if semester:
            where_parts.append("s.semester_label = $semester")
            params['semester'] = semester
        
        where_clause = f"AND {' AND '.join(where_parts)}" if where_parts else ""
        
        # 1. Vector search
        vector_query = f'''
        CALL db.index.vector.queryNodes('chunkunit_embedding', $limit, $embedding) 
        YIELD node, score
        MATCH (node)<-[:HAS_CHUNK]-(w:WebtextVersion)<-[:HAS_VERSION]-(r:Resource)
        MATCH (r)<-[:HAS_RESOURCE]-(s:Semester)<-[:HAS_SEMESTER]-(c:Course)
        WHERE w.is_latest = true {where_clause}
        RETURN node.chunkunit_id AS chunkunit_id, score, 'vector' AS search_type
        ORDER BY score DESC
        LIMIT $limit
        '''
        
        params['embedding'] = query_embedding.tolist()
        vector_results = self.db.run(vector_query, **params)
        vector_chunks = {r['chunkunit_id']: (r['score'], idx + 1) for idx, r in enumerate(vector_results)}
        
        # 2. Fulltext search
        fulltext_query = f'''
        CALL db.index.fulltext.queryNodes('chunk_fulltext', $query_text, {{limit: $limit}})
        YIELD node, score
        MATCH (node)<-[:HAS_CHUNK]-(w:WebtextVersion)<-[:HAS_VERSION]-(r:Resource)
        MATCH (r)<-[:HAS_RESOURCE]-(s:Semester)<-[:HAS_SEMESTER]-(c:Course)
        WHERE w.is_latest = true {where_clause}
        RETURN node.chunkunit_id AS chunkunit_id, score, 'fulltext' AS search_type
        ORDER BY score DESC
        LIMIT $limit
        '''
        
        params['query_text'] = escaped_query  # Use escaped query for fulltext
        fulltext_results = self.db.run(fulltext_query, **params)
        fulltext_chunks = {r['chunkunit_id']: (r['score'], idx + 1) for idx, r in enumerate(fulltext_results)}
        
        # 3. Apply RRF in Python
        k = 60
        rrf_scores = {}
        all_chunk_ids = set(vector_chunks.keys()) | set(fulltext_chunks.keys())
        
        for chunk_id in all_chunk_ids:
            rrf_score = 0.0
            search_types = []
            
            if chunk_id in vector_chunks:
                _, rank = vector_chunks[chunk_id]
                rrf_score += 1.0 / (k + rank)
                search_types.append('vector')
            
            if chunk_id in fulltext_chunks:
                _, rank = fulltext_chunks[chunk_id]
                rrf_score += 1.0 / (k + rank)
                search_types.append('fulltext')
            
            rrf_scores[chunk_id] = {
                'rrf_score': rrf_score,
                'search_types': search_types
            }
        
        # 4. Sort by RRF score
        top_chunk_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid]['rrf_score'], reverse=True)[:limit]
        
        if not top_chunk_ids:
            return []
        
        # 5. Return minimal results - let GraphRetrieval handle context enrichment
        results = []
        for chunk_id in top_chunk_ids:
            results.append({
                'chunkunit_id': chunk_id,
                'score': rrf_scores[chunk_id]['rrf_score'],
                'search_types': rrf_scores[chunk_id]['search_types']
            })
        
        return results
    
    def search_slides(
        self,
        query_text: str,
        limit: int = 10,
        course_slug: Optional[str] = None,
        semester: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search for slides combining vector and fulltext search with RRF.
        Compatible with Neo4j Community Edition.
        """
        # Escape special Lucene characters in query text
        escaped_query = self._escape_lucene_query(query_text)
        
        # Get query embedding
        query_embedding = self.embedder.embed_text(query_text)
        
        # Build WHERE clause for filters
        where_parts = []
        params = {'limit': limit}
        
        if course_slug:
            where_parts.append("c.course_slug = $course_slug")
            params['course_slug'] = course_slug
        
        if semester:
            where_parts.append("s.semester_label = $semester")
            params['semester'] = semester
        
        where_clause = f"AND {' AND '.join(where_parts)}" if where_parts else ""
        
        # 1. Vector search
        vector_query = f'''
        CALL db.index.vector.queryNodes('slideunit_embedding', $limit, $embedding) 
        YIELD node, score
        MATCH (node)<-[:HAS_SLIDE]-(d:DocVersion)<-[:HAS_VERSION]-(r:Resource)
        MATCH (r)<-[:HAS_RESOURCE]-(s:Semester)<-[:HAS_SEMESTER]-(c:Course)
        WHERE d.is_latest = true {where_clause}
        RETURN node.slideunit_id AS slideunit_id, score, 'vector' AS search_type
        ORDER BY score DESC
        LIMIT $limit
        '''
        
        params['embedding'] = query_embedding.tolist()
        vector_results = self.db.run(vector_query, **params)
        vector_slides = {r['slideunit_id']: (r['score'], idx + 1) for idx, r in enumerate(vector_results)}
        
        # 2. Fulltext search
        fulltext_query = f'''
        CALL db.index.fulltext.queryNodes('slide_fulltext', $query_text, {{limit: $limit}})
        YIELD node, score
        MATCH (node)<-[:HAS_SLIDE]-(d:DocVersion)<-[:HAS_VERSION]-(r:Resource)
        MATCH (r)<-[:HAS_RESOURCE]-(s:Semester)<-[:HAS_SEMESTER]-(c:Course)
        WHERE d.is_latest = true {where_clause}
        RETURN node.slideunit_id AS slideunit_id, score, 'fulltext' AS search_type
        ORDER BY score DESC
        LIMIT $limit
        '''
        
        params['query_text'] = escaped_query  # Use escaped query for fulltext
        fulltext_results = self.db.run(fulltext_query, **params)
        fulltext_slides = {r['slideunit_id']: (r['score'], idx + 1) for idx, r in enumerate(fulltext_results)}
        
        # 3. Apply RRF in Python
        k = 60
        rrf_scores = {}
        all_slide_ids = set(vector_slides.keys()) | set(fulltext_slides.keys())
        
        for slide_id in all_slide_ids:
            rrf_score = 0.0
            search_types = []
            
            if slide_id in vector_slides:
                _, rank = vector_slides[slide_id]
                rrf_score += 1.0 / (k + rank)
                search_types.append('vector')
            
            if slide_id in fulltext_slides:
                _, rank = fulltext_slides[slide_id]
                rrf_score += 1.0 / (k + rank)
                search_types.append('fulltext')
            
            rrf_scores[slide_id] = {
                'rrf_score': rrf_score,
                'search_types': search_types
            }
        
        # 4. Sort by RRF score
        top_slide_ids = sorted(rrf_scores.keys(), key=lambda sid: rrf_scores[sid]['rrf_score'], reverse=True)[:limit]
        
        if not top_slide_ids:
            return []
        
        # 5. Return minimal results - let GraphRetrieval handle context enrichment
        results = []
        for slide_id in top_slide_ids:
            results.append({
                'slideunit_id': slide_id,
                'score': rrf_scores[slide_id]['rrf_score'],
                'search_types': rrf_scores[slide_id]['search_types']
            })
        
        return results
    