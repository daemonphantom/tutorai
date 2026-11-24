import sys
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from src.neo4jdata.start import Neo4jConnection
from src.neo4jdata.embedder import TextEmbedder
from src.neo4jdata.upsert_pdf import PDFLoader


def upload_single_pdf(json_file: str, neo_password: str = None):
    json_path = Path(json_file)
    
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file not found: {json_file}")

    # Connect to Neo4j
    neo = Neo4jConnection(password=neo_password)
    embedder = TextEmbedder(neo)
    uploader = PDFLoader(neo, embedder)
    
    try:
        # Upload the document (with embeddings)
        docversion_id = uploader.upload_docversion(
            slides_json_file=str(json_path),
            resource_id=None,  # Auto-generate from document metadata
            resource_name=None,  # Use document_title from metadata
            generate_embeddings=True  # Yes, we want embeddings for search
        )
        
        print(f"\n{'=' * 60}")
        print("✅ SUCCESS!")
        print(f"DocVersion ID: {docversion_id}")
        
        # Show what was created
        print("\n📊 Database Statistics:")
        stats = uploader.get_docversion_stats()
        for key, value in stats.items():
            print(f"   {key.capitalize()}: {value}")
        
        # Show the uploaded document details
        print("\n🔍 Document Details:")
        result = neo.db.run("""
            MATCH (d:DocVersion {docversion_id: $docversion_id})
            OPTIONAL MATCH (r:Resource)-[:HAS_VERSION]->(d)
            OPTIONAL MATCH (s:Semester)-[:HAS_RESOURCE]->(r)
            OPTIONAL MATCH (c:Course)-[:HAS_SEMESTER]->(s)
            OPTIONAL MATCH (d)-[:HAS_SLIDE]->(slide:SlideUnit)
            RETURN d.document_title as title,
                   d.course_name as course,
                   d.course_semester as semester,
                   d.page_count as pages,
                   d.document_url as doc_url,
                   d.moodle_url as moodle_url,
                   r.resource_name as resource_name,
                   r.resource_id as resource_id,
                   c.course_slug as course_slug,
                   count(slide) as slide_count
        """, docversion_id=docversion_id)
        
        record = result.single()
        if record:
            print(f"   Title: {record['title']}")
            print(f"   Course: {record['course']} ({record['course_slug']})")
            print(f"   Semester: {record['semester']}")
            print(f"   Resource: {record['resource_name']} (ID: {record['resource_id']})")
            print(f"   Pages: {record['pages']}")
            print(f"   SlideUnits: {record['slide_count']}")
            print(f"   Document URL: {record['doc_url']}")
            print(f"   Moodle URL: {record['moodle_url']}")
        return docversion_id
        
    finally:
        neo.close()


def main():
    """CLI entry point"""
    if len(sys.argv) < 2:
        print("Usage: python -m src.neo4jdata.upsert_pdf_single <json_file>")
        print("\nExample:")
        print("  python -m src.neo4jdata.upsert_pdf_single data/course_42290/document/json/42290_001_01_document.json")
        sys.exit(1)
    
    json_file = sys.argv[1]
    
    upload_single_pdf(json_file)


if __name__ == "__main__":
    main()
