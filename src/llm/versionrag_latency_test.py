"""
VersionRAG Latency Testing Module

Measures and reports latency across the entire RAG pipeline:
1. Step-back + Classification + Temporal Detection (LLM routing)
2. Vector Search (Posts, Slides, Chunks)
3. Knowledge Graph Context Retrieval (enrichment)
4. LLM Response Generation
5. Total End-to-End Response Time

Usage:
    from src.llm.versionrag_latency_test import LatencyTester
    
    tester = LatencyTester()
    results = tester.test_single_query("Was hat sich bei der Klausur geändert?")
    tester.print_results(results)
"""

import time
from typing import Dict, List
import statistics
from dataclasses import dataclass

from .query_graphrag import GraphRAG


@dataclass
class TimingMetrics:
    """Container for timing measurements"""
    # Phase 1: Query routing
    routing_start: float = 0.0
    routing_end: float = 0.0
    
    # Phase 2: Hybrid search
    search_start: float = 0.0
    search_posts_end: float = 0.0
    search_slides_end: float = 0.0
    search_chunks_end: float = 0.0
    search_end: float = 0.0
    
    # Phase 3: Context retrieval (knowledge graph)
    context_start: float = 0.0
    context_posts_end: float = 0.0
    context_slides_end: float = 0.0
    context_chunks_end: float = 0.0
    context_end: float = 0.0
    
    # Phase 4: LLM response generation
    llm_generation_start: float = 0.0
    llm_generation_end: float = 0.0
    
    # Total
    total_start: float = 0.0
    total_end: float = 0.0
    
    # Metadata
    query: str = ""
    classification: str = ""
    is_temporal: bool = False
    stepback_query: str = ""
    num_posts: int = 0
    num_slides: int = 0
    num_chunks: int = 0
    
    def get_routing_time(self) -> float:
        """Time for step-back + classification + temporal detection"""
        return self.routing_end - self.routing_start
    
    def get_search_time(self) -> float:
        """Total time for all vector searches"""
        return self.search_end - self.search_start
    
    def get_search_posts_time(self) -> float:
        """Time for posts hybrid search"""
        return self.search_posts_end - self.search_start
    
    def get_search_slides_time(self) -> float:
        """Time for slides hybrid search"""
        return self.search_slides_end - self.search_posts_end
    
    def get_search_chunks_time(self) -> float:
        """Time for chunks hybrid search"""
        return self.search_chunks_end - self.search_slides_end
    
    def get_context_time(self) -> float:
        """Total time for knowledge graph context enrichment"""
        return self.context_end - self.context_start
    
    def get_context_posts_time(self) -> float:
        """Time for posts context retrieval"""
        return self.context_posts_end - self.context_start
    
    def get_context_slides_time(self) -> float:
        """Time for slides context retrieval"""
        return self.context_slides_end - self.context_posts_end
    
    def get_context_chunks_time(self) -> float:
        """Time for chunks context retrieval"""
        return self.context_chunks_end - self.context_slides_end
    
    def get_llm_generation_time(self) -> float:
        """Time for final LLM response generation"""
        return self.llm_generation_end - self.llm_generation_start
    
    def get_total_time(self) -> float:
        """Total end-to-end time"""
        return self.total_end - self.total_start


class InstrumentedGraphRAG(GraphRAG):
    """
    Instrumented version of GraphRAG that tracks timing at each step.
    Extends the original GraphRAG without modifying its behavior.
    """
    
    def __init__(self):
        super().__init__()
        self.timing = TimingMetrics()
    
    def smart_query_timed(self, question: str) -> tuple[Dict, TimingMetrics]:
        """
        Instrumented version of smart_query that tracks all timing information.
        Returns both the result and timing metrics.
        """
        self.timing = TimingMetrics()
        self.timing.total_start = time.perf_counter()
        self.timing.query = question
        
        print(f"\n{'='*80}")
        print(f"🔍 LATENCY TEST: '{question}'")
        print(f"{'='*80}\n")
        
        # Phase 1: Query Routing (step-back + classification + temporal)
        print("📍 Phase 1: Query Routing (Step-back + Classification + Temporal)")
        self.timing.routing_start = time.perf_counter()
        routing_plan = self.query_classification(question)
        self.timing.routing_end = time.perf_counter()
        
        self.timing.classification = routing_plan['classification']['label']
        self.timing.is_temporal = routing_plan.get('is_temporal', False)
        self.timing.stepback_query = routing_plan['step_back']
        
        print(f"   ⏱️  Routing Time: {self.timing.get_routing_time()*1000:.2f}ms")
        print(f"   🎯 Classification: {self.timing.classification}")
        print(f"   ⏰ Temporal: {self.timing.is_temporal}")
        print(f"   🔄 Step-back: '{self.timing.stepback_query}'")
        
        # Handle special classifications (no search needed)
        if self.timing.classification in ["SMALL_TALK", "TIME_DATE"]:
            result = self.query_route.process_routing_plan(question, routing_plan)
            self.timing.total_end = time.perf_counter()
            print(f"\n✅ Total Time: {self.timing.get_total_time()*1000:.2f}ms (no search needed)")
            return result, self.timing
        
        # Phase 2: Hybrid Search (Vector + Fulltext)
        print("\n📍 Phase 2: Hybrid Search (Vector + Fulltext)")
        if self.timing.classification == "NORMAL":
            result = self._graphrag_retrieval_timed(
                question, 
                routing_plan['step_back'], 
                routing_plan.get('is_temporal', False)
            )
        elif self.timing.classification == "PERSON":
            # PERSON queries don't use hybrid search, just graph-based search
            result = self.query_route.process_routing_plan(question, routing_plan)
        else:
            result = self.query_route.process_routing_plan(question, routing_plan)
        
        self.timing.total_end = time.perf_counter()
        
        print(f"\n{'='*80}")
        print(f"✅ TOTAL END-TO-END TIME: {self.timing.get_total_time()*1000:.2f}ms")
        print(f"{'='*80}\n")
        
        return result, self.timing
    
    def _graphrag_retrieval_timed(self, original_query: str, stepback_query: str, is_temporal: bool = False) -> Dict:
        """
        Instrumented version of _graphrag_retrieval with timing breakdowns.
        """
        print("   🔍 Searching knowledge sources...")
        
        self.timing.search_start = time.perf_counter()
        
        # Use the retrieval module but track each search separately
        # We need to break down search_knowledge_sources into its components
        
        # Posts search
        print("      - Searching posts (hybrid)...", end=" ")
        search_posts_start = time.perf_counter()
        post_results = self.hybrid_search.search_posts(
            stepback_query,
            limit=20,
            filters=None
        )
        self.timing.search_posts_end = time.perf_counter()
        print(f"✓ {len(post_results)} results ({(self.timing.search_posts_end - search_posts_start)*1000:.2f}ms)")
        
        # Slides search
        print("      - Searching slides (hybrid)...", end=" ")
        slide_results = self.hybrid_search.search_slides(
            stepback_query,
            limit=3,
            course_slug=None,
            semester=None
        )
        self.timing.search_slides_end = time.perf_counter()
        print(f"✓ {len(slide_results)} results ({(self.timing.search_slides_end - self.timing.search_posts_end)*1000:.2f}ms)")
        
        # Chunks search
        print("      - Searching chunks (hybrid)...", end=" ")
        chunk_results = self.hybrid_search.search_chunks(
            stepback_query,
            limit=10,
            course_slug=None,
            semester=None
        )
        self.timing.search_chunks_end = time.perf_counter()
        self.timing.search_end = self.timing.search_chunks_end
        print(f"✓ {len(chunk_results)} results ({(self.timing.search_chunks_end - self.timing.search_slides_end)*1000:.2f}ms)")
        
        print(f"   ⏱️  Total Search Time: {self.timing.get_search_time()*1000:.2f}ms")
        
        # Phase 3: Context Enrichment (Knowledge Graph)
        print("\n📍 Phase 3: Knowledge Graph Context Enrichment")
        self.timing.context_start = time.perf_counter()
        
        # Get IDs
        post_ids = [r['post_id'] for r in post_results]
        slide_ids = [r['slideunit_id'] for r in slide_results]
        chunk_ids = [r['chunkunit_id'] for r in chunk_results]
        
        self.timing.num_posts = len(post_ids)
        self.timing.num_slides = len(slide_ids)
        self.timing.num_chunks = len(chunk_ids)
        
        # Posts context
        print(f"      - Enriching {len(post_ids)} posts...", end=" ")
        post_contexts = self.retrieval.get_posts_context_batch(post_ids) if post_ids else {}
        self.timing.context_posts_end = time.perf_counter()
        print(f"✓ ({(self.timing.context_posts_end - self.timing.context_start)*1000:.2f}ms)")
        
        # Slides context
        print(f"      - Enriching {len(slide_ids)} slides...", end=" ")
        slide_contexts = self.retrieval.get_slides_context_batch(slide_ids) if slide_ids else {}
        self.timing.context_slides_end = time.perf_counter()
        print(f"✓ ({(self.timing.context_slides_end - self.timing.context_posts_end)*1000:.2f}ms)")
        
        # Chunks context
        print(f"      - Enriching {len(chunk_ids)} chunks...", end=" ")
        chunk_contexts = self.retrieval.get_chunks_context_batch(chunk_ids) if chunk_ids else {}
        self.timing.context_chunks_end = time.perf_counter()
        self.timing.context_end = self.timing.context_chunks_end
        print(f"✓ ({(self.timing.context_chunks_end - self.timing.context_slides_end)*1000:.2f}ms)")
        
        print(f"   ⏱️  Total Context Enrichment Time: {self.timing.get_context_time()*1000:.2f}ms")
        
        # Now use the original logic for processing results
        # (We skip duplicating all the context building logic from query_processor.py)
        # Instead, we'll call the original method but track LLM generation time
        
        # Build enhanced results (same as search_knowledge_sources)
        enhanced_posts = []
        for result in post_results:
            context = post_contexts.get(result['post_id'])
            enhanced_posts.append({
                'type': 'post',
                'vector_result': result,
                'context': context
            })
        
        enhanced_slides = []
        for result in slide_results:
            context = slide_contexts.get(result['slideunit_id'])
            enhanced_slides.append({
                'type': 'slide',
                'vector_result': result,
                'context': context
            })
        
        enhanced_chunks = []
        for result in chunk_results:
            context = chunk_contexts.get(result['chunkunit_id'])
            enhanced_chunks.append({
                'type': 'chunk',
                'vector_result': result,
                'context': context
            })
        
        # Combine and normalize scores
        all_results = enhanced_posts + enhanced_slides + enhanced_chunks
        
        if all_results:
            max_score = max(item['vector_result']['score'] for item in all_results)
            if max_score > 0:
                for item in all_results:
                    item['vector_result']['score'] = item['vector_result']['score'] / max_score
        
        all_results.sort(key=lambda x: x['vector_result']['score'], reverse=True)
        
        # Select top results (same as in query_processor.py)
        all_results_top = all_results[:7]
        
        if not all_results_top:
            return {
                "answer": "Ich konnte keine relevanten Informationen finden.",
                "query_type": "GRAPHRAG",
                "context": []
            }
        
        # Diversify posts (same logic as query_processor.py)
        post_results_diverse = []
        thread_count = {}
        
        for item in enhanced_posts:
            context = item['context']
            if not context:
                continue
            thread_id = context.get('thread_id', 'unknown')
            current_count = thread_count.get(thread_id, 0)
            
            if current_count < 2:
                post_results_diverse.append(item)
                thread_count[thread_id] = current_count + 1
                
                if len(post_results_diverse) >= 5:
                    break
        
        slide_results_top = enhanced_slides[:2]
        chunk_results_top = enhanced_chunks[:5]
        
        # Build context string (simplified - using original logic from query_processor.py)
        import json
        context_parts = []
        change_context_parts = []
        
        # Add post context
        for i, item in enumerate(post_results_diverse, 1):
            ctx = item['context']
            context_parts.append(f"""
                Quelle {i} [Forum Post]: {ctx['subject']}
                Autor: {ctx.get('author_name', 'Unbekannt')}
                Inhalt: {ctx['content'][:600]}...
                """)
        
        # Add slide context
        for i, item in enumerate(slide_results_top, len(post_results_diverse) + 1):
            ctx = item['context']
            if not ctx:
                continue
            context_parts.append(f"""
                Quelle {i} [Folie]: {ctx['title']}
                Dokument: {ctx.get('resource_name', 'Unbekannt')} (Seite {ctx.get('page_number', '?')})
                Semester: {ctx.get('document_semester', 'Unbekannt')}
                Inhalt: {ctx['content'][:1200]}
                """)
            
            # Add change information if temporal
            if is_temporal and ctx.get('change_id') and ctx.get('changes_summary'):
                change_type = ctx.get('change_type', 'modified')
                old_semester = ctx.get('old_semester', 'Vorherige Version')
                new_semester = ctx.get('new_semester', 'Aktuelle Version')
                is_cross_semester = old_semester != new_semester
                
                temporal_context = f"zwischen Semestern ({old_semester} → {new_semester})" if is_cross_semester else f"innerhalb {new_semester}"
                
                try:
                    changes_list = json.loads(ctx['changes_summary']) if isinstance(ctx['changes_summary'], str) else ctx['changes_summary']
                    if changes_list:
                        changes_text = "; ".join([f"{ch.get('type', 'ÄNDERUNG')}: {ch.get('description', '')}" for ch in changes_list])
                    else:
                        changes_text = "Keine spezifischen Änderungen aufgezeichnet"
                except (json.JSONDecodeError, TypeError):
                    changes_text = str(ctx.get('changes_summary', 'Keine Zusammenfassung verfügbar'))
                
                change_info = f"""
                🔄 ÄNDERUNG {temporal_context}:
                Typ: {change_type}
                Was hat sich geändert: {changes_text}
                """
                
                if ctx.get('old_slide_content'):
                    change_info += f"\nVorher ({old_semester}): {ctx['old_slide_content'][:400]}..."
                
                change_context_parts.append(change_info)
        
        # Add chunk context
        for i, item in enumerate(chunk_results_top, len(post_results_diverse) + len(slide_results_top) + 1):
            ctx = item['context']
            if not ctx:
                continue
            
            context_parts.append(f"""
                Quelle {i} [Webtext]: {ctx['section_title']}
                Ressource: {ctx.get('resource_name', 'Unbekannt')}
                Semester: {ctx.get('webtext_semester', 'Unbekannt')}
                Inhalt: {ctx['content']}
                """)
            
            # Add change information if temporal
            if is_temporal and ctx.get('change_id') and ctx.get('changes_summary'):
                change_type = ctx.get('change_type', 'modified')
                old_semester = ctx.get('old_semester', 'Vorherige Version')
                new_semester = ctx.get('new_semester', 'Aktuelle Version')
                is_cross_semester = old_semester != new_semester
                
                temporal_context = f"zwischen Semestern ({old_semester} → {new_semester})" if is_cross_semester else f"innerhalb {new_semester}"
                
                try:
                    changes_list = json.loads(ctx['changes_summary']) if isinstance(ctx['changes_summary'], str) else ctx['changes_summary']
                    if changes_list:
                        changes_text = "; ".join([f"{ch.get('type', 'ÄNDERUNG')}: {ch.get('description', '')}" for ch in changes_list])
                    else:
                        changes_text = "Keine spezifischen Änderungen aufgezeichnet"
                except (json.JSONDecodeError, TypeError):
                    changes_text = str(ctx.get('changes_summary', 'Keine Zusammenfassung verfügbar'))
                
                change_info = f"""
                🔄 ÄNDERUNG {temporal_context}:
                Typ: {change_type}
                Was hat sich geändert: {changes_text}
                """
                
                if ctx.get('old_chunk_content'):
                    change_info += f"\nVorher ({old_semester}): {ctx['old_chunk_content'][:400]}..."
                
                change_context_parts.append(change_info)
        
        context = "\n".join(context_parts)
        
        if change_context_parts:
            change_context = "\n\n" + "="*60 + "\nVERSIONSÄNDERUNGEN (wichtig für temporale Fragen):\n" + "="*60 + "\n" + "\n".join(change_context_parts)
            context += change_context
        
        actual_sources = post_results_diverse + slide_results_top + chunk_results_top
        
        # Phase 4: LLM Response Generation
        print("\n📍 Phase 4: LLM Response Generation")
        self.timing.llm_generation_start = time.perf_counter()
        
        system_prompt = """Du bist TutorAI, ein intelligenter Kurs-Assistent für deutsche Studierende. Du hilfst bei Fragen zu Kursen, Aufgaben, Terminen und technischen Problemen basierend auf Forumsdiskussionen, Vorlesungsfolien und Webtext-Inhalten.

            REGELN:
            - Antworte NUR basierend auf dem bereitgestellten Kontext
            - Wenn der Kontext nicht ausreicht: sage das klar und frage nach Details, gleichzeitig Kontakt mit Lehrpersonal empfehlen
            - Zitiere Quellen mit [Quelle X]
            - Sei präzise und hilfreich
            - Sprache: Deutsch
            - Halte Antworten kurz und fokussiert. Gehe in spezifische Details nur dann, wenn das gefragt wird
            - KURZ & KLAR: Maximal 2-3 Sätze, außer bei komplexen Erklärungen
            - NIEMALS RATEN: Wenn du unsicher bist, sage es ehrlich
            - NUR BEREITGESTELLTE DATEN: Verwende ausschließlich die bereitgestellten Informationen aus Forumsbeiträgen, Vorlesungsfolien und Webtext-Inhalten
            - FOKUSSIERT: Beantworte nur die gestellte Frage, keine unnötigen Details
            - EMPATHISCH: Sei geduldig und verständnisvoll mit Studierenden
            
            VERSIONSÄNDERUNGEN:
            - Schreibe über die VERSIONSÄNDERUNGEN NUR wenn der Student danach fragt! 
            - Wenn VERSIONSÄNDERUNGEN bereitgestellt werden, beachte diese besonders!
            - Erkläre Unterschiede zwischen Semestern/Versionen klar: "Früher war es so..., jetzt ist es so..."
            - Bei cross-semester Änderungen: Erwähne explizit "im letzten Semester" vs "in diesem Semester"
            - Bei intra-semester Änderungen: Erwähne "in einer früheren Version" vs "aktuell"

            ANTWORTFORMAT:
            <b>Antwort:</b> [Direkte, präzise Antwort basierend auf den bereitgestellten Quellen]<br>
            <b>Quelle:</b> [Titel des relevanten Forumbeitrags, der Folie oder des Webtext-Abschnitts]<br>"""

        user_message = f"""KONTEXT:
            {context}

            FRAGE: {original_query}"""
        
        llm_response = self.llm.chat(messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ])
        
        self.timing.llm_generation_end = time.perf_counter()
        
        print(f"   ⏱️  LLM Generation Time: {self.timing.get_llm_generation_time()*1000:.2f}ms")
        
        return {
            "answer": llm_response,
            "query_type": "GRAPHRAG",
            "context": actual_sources,
            "original_query": original_query,
            "stepback_query": stepback_query
        }


class LatencyTester:
    """
    Main testing interface for VersionRAG latency measurements.
    """
    
    def __init__(self):
        """Initialize the instrumented GraphRAG system"""
        print("🚀 Initializing LatencyTester...")
        self.graphrag = InstrumentedGraphRAG()
        print("✅ LatencyTester ready!\n")
    
    def test_single_query(self, query: str) -> TimingMetrics:
        """
        Test a single query and return timing metrics.
        
        Args:
            query: The user query to test
            
        Returns:
            TimingMetrics object with all timing information
        """
        result, timing = self.graphrag.smart_query_timed(query)
        return timing
    
    def test_multiple_queries(self, queries: List[str]) -> List[TimingMetrics]:
        """
        Test multiple queries and return timing metrics for each.
        
        Args:
            queries: List of user queries to test
            
        Returns:
            List of TimingMetrics objects
        """
        results = []
        for i, query in enumerate(queries, 1):
            print(f"\n{'#'*80}")
            print(f"# Test {i}/{len(queries)}")
            print(f"{'#'*80}")
            timing = self.test_single_query(query)
            results.append(timing)
            time.sleep(0.5)  # Brief pause between queries
        
        return results
    
    def print_results(self, timing: TimingMetrics, detailed: bool = True):
        """
        Print formatted timing results.
        
        Args:
            timing: TimingMetrics object
            detailed: If True, show detailed breakdown
        """
        print(f"\n{'='*80}")
        print("📊 LATENCY REPORT")
        print(f"{'='*80}")
        print(f"Query: {timing.query}")
        print(f"Classification: {timing.classification}")
        print(f"Temporal: {timing.is_temporal}")
        print(f"Step-back: {timing.stepback_query}")
        print("-"*80)
        
        # Main phases
        print("\n🎯 MAIN PHASES:")
        print("   1. Query Routing (Step-back + Classification + Temporal)")
        print(f"      ⏱️  {timing.get_routing_time()*1000:>8.2f}ms")
        
        if timing.classification == "NORMAL":
            print("\n   2. Hybrid Search (Vector + Fulltext)")
            print(f"      ⏱️  {timing.get_search_time()*1000:>8.2f}ms")
            
            if detailed:
                print(f"         - Posts ({timing.num_posts} results): {timing.get_search_posts_time()*1000:>8.2f}ms")
                print(f"         - Slides ({timing.num_slides} results): {timing.get_search_slides_time()*1000:>8.2f}ms")
                print(f"         - Chunks ({timing.num_chunks} results): {timing.get_search_chunks_time()*1000:>8.2f}ms")
            
            print("\n   3. Knowledge Graph Context Enrichment")
            print(f"      ⏱️  {timing.get_context_time()*1000:>8.2f}ms")
            
            if detailed:
                print(f"         - Posts: {timing.get_context_posts_time()*1000:>8.2f}ms")
                print(f"         - Slides: {timing.get_context_slides_time()*1000:>8.2f}ms")
                print(f"         - Chunks: {timing.get_context_chunks_time()*1000:>8.2f}ms")
            
            print("\n   4. LLM Response Generation")
            print(f"      ⏱️  {timing.get_llm_generation_time()*1000:>8.2f}ms")
        
        print(f"\n{'='*80}")
        print(f"⏱️  TOTAL END-TO-END TIME: {timing.get_total_time()*1000:.2f}ms ({timing.get_total_time():.3f}s)")
        print(f"{'='*80}\n")
    
    def print_summary(self, timings: List[TimingMetrics]):
        """
        Print summary statistics for multiple queries.
        
        Args:
            timings: List of TimingMetrics objects
        """
        if not timings:
            print("No timing data to summarize.")
            return
        
        # Filter out non-NORMAL queries for detailed stats
        normal_timings = [t for t in timings if t.classification == "NORMAL"]
        
        print(f"\n{'='*80}")
        print(f"📊 SUMMARY STATISTICS ({len(timings)} queries)")
        print(f"{'='*80}\n")
        
        # Total time stats
        total_times = [t.get_total_time() * 1000 for t in timings]
        print("🎯 TOTAL END-TO-END TIME:")
        print(f"   Mean:   {statistics.mean(total_times):>8.2f}ms")
        print(f"   Median: {statistics.median(total_times):>8.2f}ms")
        print(f"   Min:    {min(total_times):>8.2f}ms")
        print(f"   Max:    {max(total_times):>8.2f}ms")
        if len(total_times) > 1:
            print(f"   StdDev: {statistics.stdev(total_times):>8.2f}ms")
        
        if normal_timings:
            print(f"\n📍 DETAILED BREAKDOWN (NORMAL queries only, n={len(normal_timings)}):")
            
            # Routing time
            routing_times = [t.get_routing_time() * 1000 for t in normal_timings]
            print("\n   1️⃣  Query Routing:")
            print(f"       Mean:   {statistics.mean(routing_times):>8.2f}ms")
            print(f"       Median: {statistics.median(routing_times):>8.2f}ms")
            
            # Search time
            search_times = [t.get_search_time() * 1000 for t in normal_timings]
            print("\n   2️⃣  Hybrid Search:")
            print(f"       Mean:   {statistics.mean(search_times):>8.2f}ms")
            print(f"       Median: {statistics.median(search_times):>8.2f}ms")
            
            # Context time
            context_times = [t.get_context_time() * 1000 for t in normal_timings]
            print("\n   3️⃣  Context Enrichment:")
            print(f"       Mean:   {statistics.mean(context_times):>8.2f}ms")
            print(f"       Median: {statistics.median(context_times):>8.2f}ms")
            
            # LLM time
            llm_times = [t.get_llm_generation_time() * 1000 for t in normal_timings]
            print("\n   4️⃣  LLM Generation:")
            print(f"       Mean:   {statistics.mean(llm_times):>8.2f}ms")
            print(f"       Median: {statistics.median(llm_times):>8.2f}ms")
            
            # Percentage breakdown - use NORMAL queries only for accurate percentages
            normal_total_times = [t.get_total_time() * 1000 for t in normal_timings]
            total_avg_normal = statistics.mean(normal_total_times)
            routing_avg = statistics.mean(routing_times)
            search_avg = statistics.mean(search_times)
            context_avg = statistics.mean(context_times)
            llm_avg = statistics.mean(llm_times)
            
            print("\n📊 PERCENTAGE BREAKDOWN (NORMAL queries only):")
            print(f"   Routing:    {(routing_avg/total_avg_normal)*100:>6.1f}%")
            print(f"   Search:     {(search_avg/total_avg_normal)*100:>6.1f}%")
            print(f"   Context:    {(context_avg/total_avg_normal)*100:>6.1f}%")
            print(f"   LLM Gen:    {(llm_avg/total_avg_normal)*100:>6.1f}%")
        
        # Classification breakdown
        print("\n🏷️  CLASSIFICATION BREAKDOWN:")
        classification_counts = {}
        for t in timings:
            classification_counts[t.classification] = classification_counts.get(t.classification, 0) + 1
        
        for cls, count in sorted(classification_counts.items()):
            print(f"   {cls}: {count}")
        
        # Temporal breakdown
        temporal_count = sum(1 for t in timings if t.is_temporal)
        print(f"\n⏰ TEMPORAL QUERIES: {temporal_count}/{len(timings)}")
        
        print(f"\n{'='*80}\n")


# Example test queries
EXAMPLE_QUERIES = [
    # Temporal queries
    "Was hat sich bei der Klausur geändert?",
    "Warum gibt es keine Hausaufgaben mehr?",
    "Gibt es noch Videos wie früher?",
    "Unterschied zu letztem Semester bei der Prüfung?",
    
    # Normal queries
    "Wie viel Prozent ist der Test?",
    "Wann ist die Prüfung?",
    "Gibt es Hausaufgaben?",
    "Wo findet die Vorlesung statt?",
    "Was ist eine Variable in Java?",
    
    # Person queries
    "Wer ist Professor Müller?",
    
    # Small talk
    "Hallo!",
    "Danke für deine Hilfe",
]


def main():
    """
    Main entry point for running latency tests.
    """
    import argparse
    
    parser = argparse.ArgumentParser(description="VersionRAG Latency Testing")
    parser.add_argument('--query', '-q', type=str, help='Single query to test')
    parser.add_argument('--all', '-a', action='store_true', help='Test all example queries')
    parser.add_argument('--count', '-c', type=int, default=5, help='Number of example queries to test')
    parser.add_argument('--detailed', '-d', action='store_true', help='Show detailed breakdown')
    
    args = parser.parse_args()
    
    tester = LatencyTester()
    
    if args.query:
        # Single query test
        timing = tester.test_single_query(args.query)
        tester.print_results(timing, detailed=args.detailed)
    
    elif args.all:
        # Test all example queries
        timings = tester.test_multiple_queries(EXAMPLE_QUERIES)
        for timing in timings:
            tester.print_results(timing, detailed=args.detailed)
        tester.print_summary(timings)
    
    else:
        # Test subset of example queries
        queries = EXAMPLE_QUERIES[:args.count]
        timings = tester.test_multiple_queries(queries)
        for timing in timings:
            tester.print_results(timing, detailed=args.detailed)
        tester.print_summary(timings)


if __name__ == "__main__":
    main()
