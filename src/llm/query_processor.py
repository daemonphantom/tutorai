from typing import Optional, Dict
from .llm_provider import OllamaProvider
from ..search.search_kg_context import GraphRetrieval
from ..neo4jdata.embedder import TextEmbedder


class QueryProcessor:
    def __init__(self, llm_provider: OllamaProvider, embedder: Optional[TextEmbedder], graph_retrieval: Optional[GraphRetrieval] = None):
        self.llm = llm_provider
        self.embedder = embedder
        self.graph_retrieval = graph_retrieval

    def process_routing_plan(self, original_query: str, routing_plan: Dict) -> Dict:
        """Execute routing plan from LLM call"""
        
        classification = routing_plan['classification']['label']
        step_back = routing_plan['step_back']
        is_temporal = routing_plan.get('is_temporal', False)
        
        # Handle special classifications
        if classification == "SMALL_TALK":
            messages = [
                {
                    "role": "system",
                    "content": "Du bist TutorAI, ein KI-Assistent für die Technische Universität in Berlin. Sei freundlich und hilfsbereit. Erwähne bei Begrüßungen, dass du bei Fragen zu Kursen weiterhelfen kannst."
                },
                {
                    "role": "user",
                    "content": original_query
                }]
            
            return {
                "answer": self.llm.chat(messages).strip(),
                "query_type": "SMALL_TALK",
                "context": []
            }
        
        elif classification == "TIME_DATE":
            return {
                "answer": "Für aktuelle Termine und Zeiten schaue bitte in das offizielle Vorlesungsverzeichnis oder wende dich an das Sekretariat.",
                "query_type": "TIME_DATE",
                "context": []
            }
        
        elif classification == "PERSON":
            person_name = routing_plan.get('person_name')
            if person_name:
                return self._handle_person_query(original_query, person_name)
            else:
                # Fallback
                classification = "NORMAL"
        
                # NORMAL classification - use GraphRAG
        if classification == "NORMAL":
            return self._graphrag_retrieval(original_query, step_back, is_temporal)
        
        # Fallback
        return {
            "answer": "Es tut mir leid, ich konnte diese Anfrage leider nicht verarbeiten.",
            "query_type": "ERROR",
            "context": []
        }

    def _graphrag_retrieval(self, original_query: str, stepback_query: str, is_temporal: bool = False) -> Dict:
        """GraphRAG retrieval using step-back query"""
        
        if not self.graph_retrieval:
            return {
                "answer": "GraphRAG ist nicht verfügbar.",
                "query_type": "ERROR",
                "context": []
            }
        
        print("---- NORMAL - search all: Posts + Slides + Webtext")
        knowledge_results = self.graph_retrieval.search_knowledge_sources(
            stepback_query,
            k_posts=20,
            k_slides=3,
            k_chunks=10
        )
        
        all_results = knowledge_results['combined_results'][:7]
        
        if not all_results:
            return {
                "answer": "Ich konnte keine relevanten Informationen finden.",
                "query_type": "GRAPHRAG",
                "context": []
            }
        
        # Diversify posts: max 5 per thread !! Affects retrieval accuracy, change if calibration is needed
        post_results_raw = knowledge_results['post_results']
        post_results_diverse = []
        thread_count = {}
        
        for item in post_results_raw:
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
        
        print(f"---- Selected {len(post_results_diverse)} diverse posts from {len(thread_count)} threads")
        
        slide_results = knowledge_results['slide_results'][:2]
        chunk_results = knowledge_results['chunk_results'][:5]
        
        print(f"---- Retrieved {len(slide_results)} slides and {len(chunk_results)} webtext chunks")
        
        # Create context
        import json  # For parsing changes_summary JSON
        context_parts = []
        change_context_parts = []  # Separate list for change information
        
        # Add post context
        for i, item in enumerate(post_results_diverse, 1):
            ctx = item['context']
            context_parts.append(f"""
                Quelle {i} [Forum Post]: {ctx['subject']}
                Autor: {ctx.get('author_name', 'Unbekannt')}
                Inhalt: {ctx['content'][:600]}...
                """)
        
        # Add slide context
        for i, item in enumerate(slide_results, len(post_results_diverse) + 1):
            ctx = item['context']
            if not ctx:
                continue
            context_parts.append(f"""
                Quelle {i} [Folie]: {ctx['title']}
                Dokument: {ctx.get('resource_name', 'Unbekannt')} (Seite {ctx.get('page_number', '?')})
                Semester: {ctx.get('document_semester', 'Unbekannt')}
                Inhalt: {ctx['content'][:1200]}
                """)
            
            # Add change information ONLY if temporal query
            if is_temporal and ctx.get('change_id') and ctx.get('changes_summary'):
                change_type = ctx.get('change_type', 'modified')
                old_semester = ctx.get('old_semester', 'Vorherige Version')
                new_semester = ctx.get('new_semester', 'Aktuelle Version')
                is_cross_semester = old_semester != new_semester
                
                temporal_context = f"zwischen Semestern ({old_semester} → {new_semester})" if is_cross_semester else f"innerhalb {new_semester}"
                
                # Parse changes_summary JSON array
                try:
                    changes_list = json.loads(ctx['changes_summary']) if isinstance(ctx['changes_summary'], str) else ctx['changes_summary']
                    if changes_list:
                        changes_text = "; ".join([f"{ch.get('type', 'ÄNDERUNG')}: {ch.get('description', '')}" for ch in changes_list])#changes_list[:3]])  # Max 3 changes
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
        
        # Add chunk context (webtext) with change information - send full content
        for i, item in enumerate(chunk_results, len(post_results_diverse) + len(slide_results) + 1):
            ctx = item['context']
            if not ctx:
                continue
            
            context_parts.append(f"""
                Quelle {i} [Webtext]: {ctx['section_title']}
                Ressource: {ctx.get('resource_name', 'Unbekannt')}
                Semester: {ctx.get('webtext_semester', 'Unbekannt')}
                Inhalt: {ctx['content']}
                """)
            
            # Add change information ONLY if temporal query
            if is_temporal and ctx.get('change_id') and ctx.get('changes_summary'):
                change_type = ctx.get('change_type', 'modified')
                old_semester = ctx.get('old_semester', 'Vorherige Version')
                new_semester = ctx.get('new_semester', 'Aktuelle Version')
                is_cross_semester = old_semester != new_semester
                
                temporal_context = f"zwischen Semestern ({old_semester} → {new_semester})" if is_cross_semester else f"innerhalb {new_semester}"
                
                # Parse changes_summary JSON array
                try:
                    changes_list = json.loads(ctx['changes_summary']) if isinstance(ctx['changes_summary'], str) else ctx['changes_summary']
                    if changes_list:
                        changes_text = "; ".join([f"{ch.get('type', 'ÄNDERUNG')}: {ch.get('description', '')}" for ch in changes_list])#changes_list[:3]])  # Max 3 changes
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
        
        # Add change context if any changes were found
        if change_context_parts:
            change_context = "\n\n" + "="*60 + "\nVERSIONSÄNDERUNGEN (wichtig für temporale Fragen):\n" + "="*60 + "\n" + "\n".join(change_context_parts)
            context += change_context
        
        actual_sources = post_results_diverse + slide_results + chunk_results

        # Generate answer using chat interface with proper system/user separation
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
            - Beispiel: Student fragt: "Warum gibt es jetzt 2 Prüfungen, FRÜHER gab es ja nur eine? Antwort: "In letzten Semester gab es eine Prüfung mit 50 Punkten (WS 23/24), in diesem Semester gibt es zwei mit jeweils 25 Punkte (WS 24/25) mit insgesamt 50 Punkte."

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
        
        return {
            "answer": llm_response,
            "query_type": "GRAPHRAG",
            "context": actual_sources,
            "original_query": original_query,
            "stepback_query": stepback_query
        }

    def _handle_person_query(self, query: str, person_name: str) -> Dict:
        """Handle person-specific queries using graph-based author search"""
        try:
            author_results = self.graph_retrieval.search_by_author(person_name, limit=10)
            
            if author_results:
                context_parts = []
                for i, result in enumerate(author_results[:5], 1):
                    context_parts.append(f"""
                        Post {i}: {result['subject']}
                        Autor: {result.get('author_name', 'Unbekannt')}
                        Autor-Rolle: {result.get('author_role','')}
                        Inhalt: {result['content']}
                        """)
                
                context = "\n".join(context_parts)
                system_prompt = "Du bist TutorAI, ein intelligenter Kurs-Assistent für deutsche Studierende. Beantworte die Frage von dem Studierenden über die Person in dem Kurs. Wenn es keine spezifische Fragen gibt, Antworte mit der User Role von der Person."
                user_message = f"""KONTEXT: 
                    {context}

                    FRAGE: {query}"""
                llm_response = self.llm.chat(messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ])                
                return {
                    "answer": llm_response,
                    "query_type": "PERSON_QUERY",
                    "context": author_results[:5]
                }
            else:
                return {
                    "answer": f"Ich konnte keine Informationen zu '{person_name}' finden.",
                    "query_type": "PERSON_QUERY",
                    "context": []
                }
        except Exception as e:
            return {
                "answer": f"Fehler bei der Personensuche: {str(e)}",
                "query_type": "ERROR",
                "context": []
            }
