from typing import Dict
from .llm_provider import OllamaProvider
from .query_processor import QueryProcessor

from ..neo4jdata.embedder import TextEmbedder
from ..neo4jdata.start import Neo4jConnection
from ..neo4jdata.upsert_forum import ForumDataLoader
from ..search.search_hybrid import HybridSearch
from ..search.search_kg_context import GraphRetrieval

class GraphRAG:
    """GraphRAG system with ONE unified routing LLM call. Everything starts here (This class is called by flask app.py)"""
    
    def __init__(self):
        self.neo = Neo4jConnection()
        self.embedder = TextEmbedder(self.neo)
        self.loader = ForumDataLoader(self.neo)
        self.hybrid_search = HybridSearch(self.neo, self.embedder)
        self.llm = OllamaProvider()
        self.retrieval = GraphRetrieval(self.neo, self.hybrid_search, self.embedder)
        self.query_route = QueryProcessor(self.llm, self.embedder, self.retrieval)
        print(f"LLM (Ollama/{self.llm.config.model}) ready!")

    def smart_query(self, question: str) -> Dict:
        """Intelligent query with ONE unified routing LLM call"""
        
        print(f"🔍 Smart search for: '{question}'")
        
        # classification step
        routing_plan = self.query_classification(question)
        print(f"Step-back: '{routing_plan['step_back']}'")
        print(f"Classification: {routing_plan['classification']['label']} (confidence: {routing_plan['classification']['confidence']})")
        print(f"Temporal query: {routing_plan.get('is_temporal', False)}")
        
        # Route based on classification
        return self.query_route.process_routing_plan(question, routing_plan)

    def _parse_routing_response(self, response: str) -> Dict:
        """Parse key-value format response - much more robust than JSON parsing"""
        import re
        
        result = {
            "step_back": "",
            "classification": {"label": "NORMAL", "confidence": 0.5},
            "person_name": None,
            "is_temporal": False
        }
        
        match = re.search(r'STEP[_\-\s]*BACK\s*:\s*([^\n]+)', response, re.IGNORECASE)
        if match:
            result['step_back'] = match.group(1).strip()
        
        match = re.search(r'LABEL\s*:\s*(SMALL_TALK|TIME_DATE|PERSON|NORMAL)', response, re.IGNORECASE)
        if match:
            result['classification']['label'] = match.group(1).upper()
        
        match = re.search(r'CONFIDENCE\s*:\s*(0?\.\d+|1\.0|1|0)', response, re.IGNORECASE)
        if match:
            try:
                result['classification']['confidence'] = float(match.group(1))
            except:
                result['classification']['confidence'] = 0.5
        
        match = re.search(r'PERSON[_\s]*NAME\s*:\s*([^\n]+)', response, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            result['person_name'] = None if name.upper() in ['NONE', 'NULL', '-', ''] else name
        
        match = re.search(r'IS[_\s]*TEMPORAL\s*:\s*(TRUE|FALSE|YES|NO|JA|NEIN|1|0)', response, re.IGNORECASE)
        if match:
            result['is_temporal'] = match.group(1).upper() in ['TRUE', 'YES', 'JA', '1']
        
        return result

    def query_classification(self, question: str) -> Dict:        
        routing_system_prompt = """Du bist ein intelligenter Query-Router für ein Hochschul-Q&A-System. Analysiere die Benutzeranfrage und erstelle einen Routing-Plan.

            AUSGABE FORMAT (verwende genau diese Schlüssel):
            STEP_BACK: [Kernbegriffe ohne Füllwörter]
            LABEL: [SMALL_TALK oder TIME_DATE oder PERSON oder NORMAL]
            CONFIDENCE: [Zahl zwischen 0.0 und 1.0]
            PERSON_NAME: [Name oder NONE]
            IS_TEMPORAL: [TRUE oder FALSE]

            CLASSIFICATION REGELN:
            - SMALL_TALK: Grüße, Danke, Small Talk
            - TIME_DATE: "Wie spät ist es?", "Welches Datum heute?" → NUR aktuelle Zeit/Datum!
            - PERSON: "Wer ist Professor X?", "Jan Kindel Kontakt" → PERSON_NAME MUSS ausgefüllt sein
            - NORMAL: Alle Kurs-Fragen (Prüfungen, Konzepte, Deadlines, etc.)

            TEMPORAL DETECTION (IS_TEMPORAL):
            Setze IS_TEMPORAL auf TRUE NUR wenn die Frage EXPLIZIT nach Änderungen/Unterschieden fragt:

            TEMPORAL (IS_TEMPORAL: TRUE):
            - "Was hat sich geändert?"
            - "Warum gibt es keine Klausur mehr?"
            - "Gibt es noch X wie früher?"
            - "Unterschied zu letztem Semester?"
            - Schlüsselwörter: "geändert", "mehr", "früher", "vorher", "nicht mehr", "letztes Semester", "Unterschied"

            NICHT TEMPORAL (IS_TEMPORAL: FALSE):
            - "Wie viel Prozent ist der Test?" (will aktuelle Info)
            - "Wann ist die Prüfung?" (will aktuelle Info)
            - "Gibt es Hausaufgaben?" (will aktuelle Info)
            - "Wo findet Vorlesung statt?" (will aktuelle Info)

            STEP-BACK CLEANING:
            1. Entferne Füllwörter: "ommggg", "hilfeee", "was", "ist", "am"
            2. Wenn IS_TEMPORAL=TRUE: ENTFERNE temporal-Wörter ("früher", "mehr", "geändert", "letztes Semester")
            3. Behalte NUR Kernkonzepte

            BEISPIELE:

            Input: "Warum gibt es keine Hausaufgaben mehr?"
            STEP_BACK: Hausaufgaben
            LABEL: NORMAL
            CONFIDENCE: 0.95
            PERSON_NAME: NONE
            IS_TEMPORAL: TRUE

            Input: "Was hat sich bei der Klausur geändert?"
            STEP_BACK: Klausur Prüfung
            LABEL: NORMAL
            CONFIDENCE: 0.9
            PERSON_NAME: NONE
            IS_TEMPORAL: TRUE

            Input: "Gibt es noch Videos wie früher?"
            STEP_BACK: Videos
            LABEL: NORMAL
            CONFIDENCE: 0.9
            PERSON_NAME: NONE
            IS_TEMPORAL: TRUE

            Input: "Wie viel Prozent ist der Test?"
            STEP_BACK: Test Prozent
            LABEL: NORMAL
            CONFIDENCE: 0.95
            PERSON_NAME: NONE
            IS_TEMPORAL: FALSE

            Input: "Wann ist die Prüfung?"
            STEP_BACK: Prüfung Termin
            LABEL: NORMAL
            CONFIDENCE: 0.9
            PERSON_NAME: NONE
            IS_TEMPORAL: FALSE

            Input: "Wer ist Professor Müller?"
            STEP_BACK: Professor
            LABEL: PERSON
            CONFIDENCE: 0.95
            PERSON_NAME: Müller
            IS_TEMPORAL: FALSE

            WICHTIG: Antworte NUR mit den 5 Zeilen im Format oben. Keine zusätzlichen Erklärungen."""

        try:
            response = self.llm.chat(
                messages=[
                    {"role": "system", "content": routing_system_prompt},
                    {"role": "user", "content": question},
                ],
                options={
                    "temperature": 0.2,
                    "num_predict": 512,
                    "num_ctx": 4096,
                    "stop": []
                }
            )
            
            if not response or len(response.strip()) == 0:
                print("⚠️ LLM returned empty response")
                return {
                    "step_back": question,
                    "classification": {"label": "NORMAL", "confidence": 0.5},
                    "person_name": None,
                    "is_temporal": False
                }
            
            print(f"Raw LLM response length: {len(response)} chars")
            print(f"Raw response preview: '{response}'")
            
            plan = self._parse_routing_response(response)
            

            if not plan['step_back']:
                print("Could not extract step_back, using original question")
                plan['step_back'] = question
            
            if plan['classification']['confidence'] < 0.5:
                print(f"Very low confidence ({plan['classification']['confidence']}), retrying...")
                response = self.llm.chat(
                    messages=[
                        {"role": "system", "content": routing_system_prompt},
                        {"role": "user", "content": question},
                    ],
                    options={
                        "temperature": 0.1,
                        "num_predict": 512,
                        "num_ctx": 4096,
                        "stop": []
                    }
                )
                
                if not response or len(response.strip()) == 0:
                    print("Empty response on retry")
                    return {
                        "step_back": question,
                        "classification": {"label": "NORMAL", "confidence": 0.5},
                        "person_name": None,
                        "is_temporal": False
                    }
                
                plan = self._parse_routing_response(response)
                
                if not plan['step_back']:
                    plan['step_back'] = question
            
            return plan
            
        except Exception as e:
            print(f"Error in routing: {e}")
            return {
                "step_back": question,
                "classification": {"label": "NORMAL", "confidence": 0.5},
                "person_name": None,
                "is_temporal": False
            }