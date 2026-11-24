# VersionRAG Latency Testing

`versionrag_latency_test.py` times each step of the VersionRAG pipeline for one query or a batch of queries.

## What it measures

1. **Query routing**: step-back query, classification, temporal detection
2. **Hybrid search**: vector + fulltext search over forum posts, PDF slides and web text chunks
3. **Context enrichment**: one batch lookup of graph metadata per source type
4. **LLM generation**: the final answer
5. **Total**: end-to-end time for one query

## Usage

### Command Line Interface

#### One query
```bash
python -m src.llm.versionrag_latency_test --query "Was hat sich bei der Klausur geändert?"
```

#### One query with a detailed breakdown
```bash
python -m src.llm.versionrag_latency_test --query "Wie viel Prozent ist der Test?" --detailed
```

#### The first 5 example queries (default)
```bash
python -m src.llm.versionrag_latency_test
```

#### All example queries
```bash
python -m src.llm.versionrag_latency_test --all
```

#### A chosen number of example queries
```bash
python -m src.llm.versionrag_latency_test --count 10
```

### Python API

```python
from src.llm.versionrag_latency_test import LatencyTester

# Initialize tester
tester = LatencyTester()

# Test single query
timing = tester.test_single_query("Was hat sich bei der Klausur geändert?")
tester.print_results(timing, detailed=True)

# Test multiple queries
queries = [
    "Was hat sich bei der Klausur geändert?",
    "Wie viel Prozent ist der Test?",
    "Wann ist die Prüfung?"
]
timings = tester.test_multiple_queries(queries)
tester.print_summary(timings)

# Access timing data programmatically
print(f"Total time: {timing.get_total_time() * 1000:.2f}ms")
print(f"Search time: {timing.get_search_time() * 1000:.2f}ms")
print(f"LLM time: {timing.get_llm_generation_time() * 1000:.2f}ms")
```

## Sample output

### One query

```
================================================================================
📊 LATENCY REPORT
================================================================================
Query: Was hat sich bei der Klausur geändert?
Classification: NORMAL
Temporal: True
Step-back: Klausur Prüfung
--------------------------------------------------------------------------------

🎯 MAIN PHASES:
   1. Query Routing (Step-back + Classification + Temporal)
      ⏱️    245.32ms

   2. Hybrid Search (Vector + Fulltext)
      ⏱️    892.15ms
         - Posts (20 results):   421.45ms
         - Slides (3 results):    285.32ms
         - Chunks (10 results):   185.38ms

   3. Knowledge Graph Context Enrichment
      ⏱️    156.78ms
         - Posts:   45.23ms
         - Slides:  62.11ms
         - Chunks:  49.44ms

   4. LLM Response Generation
      ⏱️   2345.67ms

================================================================================
⏱️  TOTAL END-TO-END TIME: 3640.92ms (3.641s)
================================================================================
```

### Summary over several queries

```
================================================================================
📊 SUMMARY STATISTICS (10 queries)
================================================================================

🎯 TOTAL END-TO-END TIME:
   Mean:    3245.67ms
   Median:  3198.45ms
   Min:      245.32ms
   Max:     4892.34ms
   StdDev:   892.12ms

📍 DETAILED BREAKDOWN (NORMAL queries only, n=8):

   1️⃣  Query Routing:
       Mean:     234.56ms
       Median:   228.34ms

   2️⃣  Hybrid Search:
       Mean:     845.23ms
       Median:   832.11ms

   3️⃣  Context Enrichment:
       Mean:     145.67ms
       Median:   142.89ms

   4️⃣  LLM Generation:
       Mean:    2123.45ms
       Median:  2098.76ms

📊 PERCENTAGE BREAKDOWN (NORMAL queries only):
   Routing:      7.2%
   Search:      26.0%
   Context:      4.5%
   LLM Gen:     65.3%

🏷️  CLASSIFICATION BREAKDOWN:
   NORMAL: 8
   SMALL_TALK: 2

⏰ TEMPORAL QUERIES: 4/10

================================================================================
```

## Timing Metrics API

`TimingMetrics` exposes every measured time and some query metadata:

```python
# Phase times
timing.get_routing_time()           # Step-back + classification + temporal
timing.get_search_time()            # Total search time
timing.get_search_posts_time()      # Posts search only
timing.get_search_slides_time()     # Slides search only
timing.get_search_chunks_time()     # Chunks search only
timing.get_context_time()           # Total context enrichment
timing.get_context_posts_time()     # Posts context only
timing.get_context_slides_time()    # Slides context only
timing.get_context_chunks_time()    # Chunks context only
timing.get_llm_generation_time()    # Final answer generation
timing.get_total_time()             # End-to-end total

# Metadata
timing.query                        # Original query
timing.classification               # NORMAL, SMALL_TALK, etc.
timing.is_temporal                  # True/False
timing.stepback_query               # Generated step-back query
timing.num_posts                    # Number of posts retrieved
timing.num_slides                   # Number of slides retrieved
timing.num_chunks                   # Number of chunks retrieved
```

## Example queries

The module ships these test queries:

**Temporal (asks about change over time):**
- "Was hat sich bei der Klausur geändert?"
- "Warum gibt es keine Hausaufgaben mehr?"
- "Gibt es noch Videos wie früher?"
- "Unterschied zu letztem Semester bei der Prüfung?"

**Normal:**
- "Wie viel Prozent ist der Test?"
- "Wann ist die Prüfung?"
- "Gibt es Hausaufgaben?"
- "Wo findet die Vorlesung statt?"
- "Was ist eine Variable in Java?"

**About a person:**
- "Wer ist Professor Müller?"

**Small talk:**
- "Hallo!"
- "Danke für deine Hilfe"

## Where the time goes

In the sample summary above, LLM generation takes 65% of the total, search 26%, routing 7% and context enrichment under 5%. Ideas for speeding it up, by phase:

1. **LLM generation**: use a smaller or faster model, or stream the answer so you see the first words sooner.
2. **Hybrid search**: cache results for frequent questions. The search already batches its queries.
3. **Routing**: cache step-back queries for similar questions, or classify with a smaller model.
4. **Context enrichment**: little to gain; it already runs as batch queries.

## Notes

The tester measures with `time.perf_counter()`. Your numbers depend on Neo4j performance and cache state, network latency, the Ollama model and the load on your machine.

## Requirements

- A running Neo4j instance with the ingested graph
- Ollama with the model set in `OLLAMA_MODEL`
- The packages in the repository's `requirements.txt`
