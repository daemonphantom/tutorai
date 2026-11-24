# Version comparison

TutorAI tracks how a course changes from one semester to the next. You give it two versions of the same material, an old one and a new one. The comparer matches the parts that belong together, an LLM describes what changed, and the ingest scripts store both versions and the changes in Neo4j. You can then ask questions like "Was hat sich bei der Klausur geändert?"

Three kinds of material can be compared:

| Material         | Comparer            | You choose the pair with                   | Output folder                      |
|------------------|---------------------|--------------------------------------------|------------------------------------|
| Lecture slides (PDF) | `comparer_pdf/`     | `comparer_pdf/pairs.json`                  | `data/comparison/pdf/<pair_id>/`     |
| Course web pages | `comparer_webtext/` | `comparer_webtext/pairs.json`              | `data/comparison/webtext/<pair_id>/` |
| Quizzes (Moodle XML) | `comparer_quiz/`    | two XML files in `data/comparison/quiz/`   | `data/comparison/quiz/`              |

The comparers never find pairs on their own yet. Crawled files have no stable names across semesters (`30422_002_01_document.pdf` in one semester is `40280_001_02_document.pdf` in the next), so you decide which old file belongs to which new file.

Run every command from the repository root. The comparers need Ollama running (see `OLLAMA_MODEL` in `.env`). The ingest scripts also need Neo4j.

## Version chains

A pair compares two versions. To track a resource over three semesters, add two pairs that share the middle file:

```
WiSe 22/23            WiSe 24/25            WiSe 25/26
30422_002_01.pdf  ->  40280_001_02.pdf  ->  43321_001_03.pdf
      pair introprog_orga_v1_v2
                            pair introprog_orga_v2_v3
```

The ingest scripts follow these chains. They store every version, but only the newest one gets embeddings, so search returns current material while the graph keeps the history. A file that appears as `new` in one pair and never as `old` counts as the newest version.

## Slides (PDF)

### 1. Write the pairs

`capture/parsing/comparer_pdf/pairs.json`:

```json
[
  {
    "old": "data/course_30422/document/files_pdf/30422_002_01_document.pdf",
    "new": "data/course_40280/document/files_pdf/40280_001_02_document.pdf",
    "pair_id": "introprog_orga_v1_v2",
    "resource_name": "VL01: Organisation",
    "resource_id": "introprog_orga"
  }
]
```

| Field           | Meaning |
|-----------------|---------|
| `old`, `new`    | Paths to the two PDFs, relative to the repository root |
| `pair_id`       | Name of the output folder; must be unique |
| `resource_name` | Readable name of the slide deck, stored in the graph |
| `resource_id`   | Groups all versions of one slide deck; use the same value in every pair of a chain |

### 2. Run the comparison

```bash
python -m capture.parsing.comparer_pdf.main --pairs capture/parsing/comparer_pdf/pairs.json
```

On a Mac, first change the import in `DiffRunner.parse_pdf` (`comparer_pdf/main.py`) from `nologo_cuda` to `nologo_mac`. The CUDA version needs an NVIDIA GPU.

For each pair, the script runs five steps:

1. **PDF → Markdown** with Docling (`docling/nologo_*.py`), one Markdown file per PDF
2. **Markdown → JSON**: `docling/md_processor.py` splits the Markdown into one entry per slide
3. **Align slides** (`align_json.py`, `align_greedy.py`): the score for each old/new slide pair is 70% embedding similarity (`distiluse-base-multilingual-cased-v2`) and 30% word overlap (Jaccard). The script matches the highest-scoring pair first, removes both slides and repeats until no score reaches 0.55. A matched pair scoring above 0.95 counts as unchanged, anything lower as modified. One old slide matching two or more new slides is a **split**; two or more old slides matching one new slide is a **merge**. Slides without a match are **added** or **removed**.
4. **Describe the changes** (`changes_llm.py`): for each modified, split or merged slide, the LLM lists the changes that matter (numbers, dates, URLs, rules, negations) as substitutions, insertions and deletions. It ignores formatting.
5. **HTML report**: `diff_<pair_id>.html` shows the changed slides side by side.

Output in `data/comparison/pdf/<pair_id>/`:

```
<old>.md, <new>.md                  Docling output
<old>.json, <new>.json              One entry per slide
<old>_<new>_alignment.json          Which slide matches which, with change type
changes_<old>_<new>.json            LLM change descriptions per slide
diff_<pair_id>.html                 Report for people to read
```

### 3. Load into Neo4j

```bash
python -m src.neo4jdata.upsert_pdf
```

This reads the same `pairs.json` and the JSON files from `data/comparison/pdf/<pair_id>/`. It creates a `DocVersion` per PDF with its `SlideUnit`s, and a `Change` node per changed slide, linked with `FROM_SLIDE` and `TO_SLIDE`.

## Web pages

### 1. Register the course and write the pairs

The comparer looks up course name and semester in `capture/crawling/course_ids/courses_versioning.json`. Add every course ID you compare:

```json
{ "title": "Einführung in die Programmierung",
  "semesters": [
    { "course_id": "40280", "semester": "WiSe 2024/25" },
    { "course_id": "43321", "semester": "WiSe 2025/26" } ] }
```

Then `capture/parsing/comparer_webtext/pairs.json`:

```json
[
  {
    "old": "data/course_40280/webpagetext/webpagetext.json",
    "new": "data/course_43321/webpagetext/webpagetext.json",
    "pair_id": "introprog_webtext_v2_v3",
    "course_name": "Einführung in die Programmierung"
  }
]
```

The files must sit at `data/course_<id>/webpagetext/`, because the script reads the course ID from that path. Both can come from the same course, for example two crawls a week apart.

Name each `pair_id` `<name>_v<old>_v<new>`. The ingest script sorts the pairs by these numbers to rebuild the chain and gives embeddings to the last one.

### 2. Run the comparison

```bash
python -m capture.parsing.comparer_webtext.main --pairs capture/parsing/comparer_webtext/pairs.json
```

1. **Align sections** (`align_webtext.py`): each page section (heading + text + tables) gets matched with the same scoring as slides, plus a bonus when the headings match.
2. **Chunk** (`webtext_chunker.py`): splits long sections into chunks of up to 2000 characters, so they fit into the LLM prompt (`--char-limit` changes this).
3. **Describe the changes** (`changes_webtext.py`) with the LLM.
4. **HTML report**: `diff_<pair_id>.html`.

Output in `data/comparison/webtext/<pair_id>/`: `alignment_<old>_<new>.json`, `chunked_<old>_<new>.json`, `changes_<old>_<new>.json` and the HTML report.

### 3. Load into Neo4j

```bash
python -m src.neo4jdata.upsert_webtext
```

This deletes all existing web text data in the graph, then loads every `*_v*_v*` folder in `data/comparison/webtext/`. The resource ID and name are hardcoded in `demo_upload_from_comparison()` (`introprog_webtext`, "Einführung in die Programmierung - Webtext"). Change them before you load another course.

## Quizzes

Quizzes have no pairs file. The crawler can't get the XML, so a teacher exports the quiz questions from Moodle in **Moodle XML** format, once per version.

1. Put the two XML files into `data/comparison/quiz/`. The script takes the first two files in alphabetical order, the first as old and the second as new, so name them that way.
2. Compare:
   ```bash
   python -m capture.parsing.comparer_quiz.compare_quiz
   ```
   The script matches questions by name. It compares the question text with `difflib` and needs no LLM. Every question comes out as unchanged, modified, added or removed. The result goes to `data/comparison/quiz/quiz_changes.json`.
3. Load into Neo4j:
   ```bash
   python -m src.neo4jdata.upsert_quiz
   ```
   `demo_upload_quiz()` in `src/neo4jdata/upsert_quiz.py` hardcodes the two file names, the semester labels, the resource name and the ID of a forum post. The forum post links the change to its cause: a student reported the error in post `1196190`, which gives the graph a `(Post)-[:TRIGGERED_CHANGE]->(QuizQuestionVersion)` edge. Edit these values for your quiz. Load the course's forum first (`python -m src.neo4jdata.upsert_forum data/course_<id>/forums`). Without the post in the graph, the script prints a warning and skips the link.
