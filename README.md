# TutorAI

TutorAI answers questions about TU Berlin courses. I built it for my Bachelor's thesis at **Technische Universität Berlin**, using courses from Faculty IV.

It crawls a course from ISIS, the TU's Moodle platform, and stores slides, forum posts, web pages and quizzes in a Neo4j knowledge graph. When you ask a question, it searches that graph and has a local LLM write the answer from what it finds. The graph also keeps every semester's version of a course, so you can ask what changed since last year.

## What it does

**For students**, TutorAI answers course questions ("When is the exam?", "What counts towards the grade?") and explains course content, with the course's own material as the source.

**For teachers**, it tracks how slides, web pages and quizzes change between semesters and summarises the differences.

## Thesis objective

The thesis asks how far you get by combining an LLM, vector search and a knowledge graph into an assistant for one domain. A general-purpose chatbot has never seen your course's slides or forum. TutorAI retrieves them at query time and passes them to the model with the question.

## Tech stack

| Tool / Library               | Purpose                                               |
|------------------------------|-------------------------------------------------------|
| `Python 3.11`                | All components                                        |
| `Selenium` / `BeautifulSoup` | Crawling ISIS                                         |
| `Docling`                    | Converting lecture slides from PDF to Markdown        |
| `whisper-timestamped`        | Transcribing lecture videos                           |
| `Neo4j`                      | Versioned knowledge graph with vector and fulltext indexes |
| `sentence-transformers`      | Multilingual text embeddings                          |
| `Ollama` (`gpt-oss:20b`)     | Local LLM for query routing and answers               |
| `Flask` + `MongoDB`          | Web chat interface and user accounts                  |

## Setup

Install these first:
- [Neo4j Community 5.x](https://neo4j.com/deployment-center/), which needs Java 21
- [Ollama](https://ollama.com), then run `ollama pull gpt-oss:20b` (set `OLLAMA_MODEL` to use a different model)
- Docker, for MongoDB
- `ffmpeg` for transcription and Google Chrome for crawling

Then create the Python environment. Run every command from the repository root.
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your values
```

`.env` holds your ISIS login and the settings for Neo4j, Ollama, MongoDB and Flask.

## Project structure

```
capture/                 Data acquisition; each step runs on its own
  crawling/              Selenium crawler for ISIS courses -> data/course_<id>/
    course_ids/          Course lists; course_ID_saved.json = courses to crawl
  parsing/               Version comparison, see capture/parsing/README.md
    docling/             PDF -> Markdown (nologo_mac.py / nologo_cuda.py) -> JSON (md_processor.py)
    comparer_pdf/        Aligns two versions of a slide deck, LLM summarises the changes
    comparer_webtext/    Same for course web page text
    comparer_quiz/       Same for Moodle quiz XML exports
  transcription/         Lecture video -> timestamped transcript (Whisper)
data/                    Crawled data, NOT in git (layout: data/README.md)
src/                     GraphRAG system
  neo4jdata/             Neo4j connection, embeddings, upsert_* ingest scripts
  search/                Hybrid search (vector + fulltext) and graph context lookup
  llm/                   Query routing, answer generation, latency evaluation
  interface/             Flask web app, MongoDB via docker-compose
```

## Running the pipeline

1. **Crawl** the courses listed in `capture/crawling/course_ids/course_ID_saved.json`:
   `python -m capture.crawling.main`
   By default the crawler only fetches `users`, `forums` and `webpagetext`. To crawl documents, quizzes, videos and the rest, edit `enabled_modules` in `crawl_course()` in `capture/crawling/main.py`.
2. **Compare versions** of slides, web pages and quizzes. You pick which old file belongs to which new one, in a `pairs.json` file. [capture/parsing/README.md](capture/parsing/README.md) explains the whole process. Read it before you run anything here.
   `python -m capture.parsing.comparer_pdf.main --pairs capture/parsing/comparer_pdf/pairs.json`
   `python -m capture.parsing.comparer_webtext.main --pairs capture/parsing/comparer_webtext/pairs.json`
3. **Ingest** into Neo4j (start Neo4j first):
   `python -m src.neo4jdata.upsert_forum data/course_<id>/forums` loads one course's forums. Run it once per course.
   `python -m src.neo4jdata.upsert_pdf`, `upsert_webtext` and `upsert_quiz` load the comparison results ([capture/parsing/README.md](capture/parsing/README.md)).
   `upsert_webtext` replaces all web text in the graph on each run. `upsert_forum --reset` wipes the whole graph first.
   `python -m src.neo4jdata.start` prints graph statistics. `python -m src.neo4jdata.reset` wipes the graph.
4. **Run the app** (details in [src/interface/README-MongoDB.md](src/interface/README-MongoDB.md)):
   `docker compose --env-file .env -f src/interface/docker-compose.yml up -d`
   `python -m src.interface.app`, then open http://localhost:5001
5. **Measure latency** with the tools in [src/llm/LATENCY_TESTING.md](src/llm/LATENCY_TESTING.md).

## Course data

This repository contains no course data. The crawled files include student names, forum posts and copyrighted teaching material. You need your own ISIS access to crawl a course. Keep everything in `data/` out of git.

## Features

- Crawls ISIS course pages: announcements, forums, documents, quizzes, videos, links and images
- Converts slides to Markdown and transcribes lecture videos
- Stores each semester of a course as a version in the knowledge graph and records what changed
- Hybrid search (vector + fulltext) over forum posts, slides and web page text
- Handles several courses and semesters in one graph

## Research goals

- Build a tutor for one domain: TU Berlin courses
- Test how retrieval-augmented generation (RAG) works together with a knowledge graph
- Model how teaching material changes from one semester to the next

## Ethical reflections

AI in education can help or harm, depending on how people use it. TutorAI could support students who learn on their own and take repetitive questions off teaching staff. It could also invite passive copying, or do work that students should think through themselves.

I designed TutorAI to support understanding. It answers in a dialogue, gives context, and points to the source material, so you can check what it says. Version tracking and the content graph make it visible where an answer comes from and how the material has changed.

Students need guidance on how to use generative AI in their studies, and teachers should decide how a system like this uses their material. Any use of TutorAI at TU Berlin should follow clear rules on academic integrity.

## Acknowledgements

Thanks to my supervisor Damien for pushing me to think bigger and to build tools that work at scale.

## Contact

To collaborate, use TutorAI, or ask about the project:

**David Durglishvili**
`durglishvili@campus.tu-berlin.de`
[LinkedIn](https://linkedin.com/in/david-durglishvili)
