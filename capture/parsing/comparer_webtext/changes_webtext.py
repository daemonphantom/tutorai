import json
from pathlib import Path
from typing import Dict, List, Any

import sys
sys.path.append(str(Path(__file__).parent.parent / 'comparer_pdf'))
from ..comparer_pdf.changes_llm import LLMVersionComparator


class WebtextChangeDetector:    
    def __init__(self, llm_provider=None):
        self.comparator = LLMVersionComparator(llm_provider=llm_provider)
    
    def process_chunked_alignment(self, chunked_alignment: Dict[str, Any]) -> Dict[str, Any]:
        metadata = chunked_alignment.get("comparison_metadata", {})
        sections = chunked_alignment.get("chunked_sections", [])
        detailed_changes = []
        
        for section in sections:
            change_type = section.get("change_type")
            
            if change_type == "removed":
                old_chunks = section.get("old_chunks", [])
                first_chunk = old_chunks[0] if old_chunks else {}
                detailed_changes.append({
                    "change_type": "removed",
                    "old_section_index": section.get("old_section_index"),
                    "section_title": first_chunk.get("section_title", "Untitled"),
                    "changes": [],
                    "note": "Entire section was removed"
                })
                
            elif change_type == "added":
                new_chunks = section.get("new_chunks", [])
                first_chunk = new_chunks[0] if new_chunks else {}
                detailed_changes.append({
                    "change_type": "added",
                    "new_section_index": section.get("new_section_index"),
                    "section_title": first_chunk.get("section_title", "Untitled"),
                    "changes": [],
                    "note": "Entire section was added"
                })
                
            elif change_type == "unchanged":
                new_section = section.get("new_section", {})
                detailed_changes.append({
                    "change_type": "unchanged",
                    "old_section_index": section.get("old_section_index"),
                    "new_section_index": section.get("new_section_index"),
                    "section_title": new_section.get("section_title", "Untitled"),
                    "similarity_score": section.get("similarity_score"),
                    "changes": [],
                    "note": "Sections are identical, no changes detected"
                })
                
            elif change_type == "modified":
                old_chunks = section.get("old_chunks", [])
                new_chunks = section.get("new_chunks", [])
                
                section_title = new_chunks[0].get('section_title', 'Untitled') if new_chunks else 'Untitled'
                
                print(f"Analyzing modified section: {section_title}")
                
                changes = []
                max_chunks = max(len(old_chunks), len(new_chunks))
                
                for i in range(max_chunks):
                    old_chunk = old_chunks[i] if i < len(old_chunks) else None
                    new_chunk = new_chunks[i] if i < len(new_chunks) else None
                    
                    if old_chunk and new_chunk:
                        # Both chunks. compare them
                        chunk_changes = self.analyze_chunk_changes(old_chunk, new_chunk)
                        changes.extend(chunk_changes)
                    elif old_chunk and not new_chunk:
                        changes.append({
                            'type': 'LOESCHUNG',
                            'description': f'Chunk {i+1} was removed: {old_chunk.get("content", "")[:100]}...'
                        })
                    elif new_chunk and not old_chunk:
                        changes.append({
                            'type': 'EINFUEGUNG',
                            'description': f'Chunk {i+1} was added: {new_chunk.get("content", "")[:100]}...'
                        })
                    
                detailed_changes.append({
                    "change_type": "modified",
                    "old_section_index": section.get("old_section_index"),
                    "new_section_index": section.get("new_section_index"),
                    "section_title": section_title,
                    "similarity_score": section.get("similarity_score"),
                    "chunks_count": {"old": len(old_chunks), "new": len(new_chunks)},
                    "changes": changes
                })
                
            elif change_type == "merge":
                new_chunks = section.get("new_chunks", [])
                section_title = new_chunks[0].get('section_title', 'Untitled') if new_chunks else 'Untitled'
                
                print(f"Analyzing merged section: {section_title}")
                
                old_chunks_list = section.get("old_chunks_list", [])
                changes = self.analyze_merge(old_chunks_list, new_chunks)
                
                detailed_changes.append({
                    "change_type": "merge",
                    "old_section_indices": section.get("old_section_indices", []),
                    "new_section_index": section.get("new_section_index"),
                    "section_title": section_title,
                    "similarity_scores": section.get("similarity_scores", []),
                    "changes": changes
                })
                
            elif change_type == "split":
                old_chunks = section.get("old_chunks", [])
                section_title = old_chunks[0].get('section_title', 'Untitled') if old_chunks else 'Untitled'
                
                print(f"Analyzing split section: {section_title}")
                
                new_chunks_list = section.get("new_chunks_list", [])
                changes = self.analyze_split(old_chunks, new_chunks_list)
                
                detailed_changes.append({
                    "change_type": "split",
                    "old_section_index": section.get("old_section_index"),
                    "new_section_indices": section.get("new_section_indices", []),
                    "section_title": section_title,
                    "similarity_scores": section.get("similarity_scores", []),
                    "changes": changes
                })
        
        report = {
            "metadata": metadata,
            "total_sections_analyzed": len(sections),
            "sections_with_content_changes": len([s for s in detailed_changes if s.get("changes")]),
            "detailed_changes": detailed_changes
        }
        
        return report
    
    def analyze_chunk_changes(self, old_chunk: Dict, new_chunk: Dict) -> List[Dict[str, Any]]:
        all_changes = []
        
        content_changes = self.comparator.detect_content_changes_llm(
            old_chunk.get("content", ""),
            new_chunk.get("content", "")
        )
        all_changes.extend(content_changes)
        
        if old_chunk.get("chunk_index", 0) == 0 and new_chunk.get("chunk_index", 0) == 0:
            old_tables = old_chunk.get("tables", [])
            new_tables = new_chunk.get("tables", [])
            
            if old_tables or new_tables:
                table_changes = self.comparator.compare_tables_llm(old_tables, new_tables)
                all_changes.extend(table_changes)
        
        return all_changes
    
    def analyze_merge(self, old_chunks_list: List[List[Dict]], new_chunks: List[Dict]) -> List[Dict[str, Any]]:
        if not old_chunks_list or not new_chunks:
            return []
        
        all_old_chunks = []
        for chunks in old_chunks_list:
            all_old_chunks.extend(chunks)
        
        all_old_chunks_sorted = sorted(all_old_chunks, key=lambda c: (c.get("section_index", 0), c.get("chunk_index", 0)))
        
        merged_content = "\n\n".join(chunk.get("content", "") for chunk in all_old_chunks_sorted if chunk.get("content"))
        
        merged_tables = []
        for chunk in all_old_chunks_sorted:
            if chunk.get("chunk_index", 0) == 0:
                tables = chunk.get("tables")
                if tables and isinstance(tables, list):
                    merged_tables.extend(tables)
        
        virtual_old_chunk = {
            "content": merged_content,
            "tables": merged_tables,
            "section_title": " + ".join(chunk.get("section_title", "Untitled") for chunk in all_old_chunks_sorted if chunk.get("chunk_index", 0) == 0),
            "chunk_index": 0
        }
        
        new_chunks_sorted = sorted(new_chunks, key=lambda c: c.get("chunk_index", 0))
        new_first_chunk = new_chunks_sorted[0] if new_chunks_sorted else {}
        
        return self.analyze_chunk_changes(virtual_old_chunk, new_first_chunk)
    
    def analyze_split(self, old_chunks: List[Dict], new_chunks_list: List[List[Dict]]) -> List[Dict[str, Any]]:
        if not old_chunks or not new_chunks_list:
            return []
        
        all_new_chunks = []
        for chunks in new_chunks_list:
            all_new_chunks.extend(chunks)
        
        all_new_chunks_sorted = sorted(all_new_chunks, key=lambda c: (c.get("section_index", 0), c.get("chunk_index", 0)))
        
        merged_content = "\n\n".join(chunk.get("content", "") for chunk in all_new_chunks_sorted if chunk.get("content"))
        
        merged_tables = []
        for chunk in all_new_chunks_sorted:
            if chunk.get("chunk_index", 0) == 0:
                tables = chunk.get("tables")
                if tables and isinstance(tables, list):
                    merged_tables.extend(tables)
        
        virtual_new_chunk = {
            "content": merged_content,
            "tables": merged_tables,
            "section_title": " + ".join(chunk.get("section_title", "Untitled") for chunk in all_new_chunks_sorted if chunk.get("chunk_index", 0) == 0),
            "chunk_index": 0
        }
        
        old_chunks_sorted = sorted(old_chunks, key=lambda c: c.get("chunk_index", 0))
        old_first_chunk = old_chunks_sorted[0] if old_chunks_sorted else {}
        
        return self.analyze_chunk_changes(old_first_chunk, virtual_new_chunk)
    
def main():
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('alignment_file', help='Alignment JSON file from align_webtext.py')
    parser.add_argument('-o', '--output', help='Output file analysis (default: changes_<input>.json)')
    
    args = parser.parse_args()
    
    alignment_file = Path(args.alignment_file)
    if not alignment_file.exists():
        print(f"Alignment file not found: {alignment_file}")
        return
    
    if not args.output:
        output_file = alignment_file.parent / f"changes_{alignment_file.name}"
    else:
        output_file = Path(args.output)
    
    try:
        detector = WebtextChangeDetector()
        
        report = detector.process_alignment(alignment_file)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"change analysis saved to: {output_file}")
        print(f"   Total sections analyzed: {report['total_sections_analyzed']}")
        print(f"   Sections with content changes: {report['sections_with_content_changes']}")
        
        change_types = {}
        for section in report['detailed_changes']:
            change_type = section['change_type']
            change_types[change_type] = change_types.get(change_type, 0) + 1
        
        print("Change type breakdown:")
        for change_type, count in sorted(change_types.items()):
            print(f"     {change_type}: {count}")
    
    except Exception as e:
        print(f"Error during analysis: {e}")
        print("Ollama running?")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
