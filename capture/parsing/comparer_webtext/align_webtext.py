import json
import argparse
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime

import sys
sys.path.append(str(Path(__file__).parent.parent / 'comparer_pdf'))
from ..comparer_pdf.align_greedy import (
    SlideEmbedder
)


class WebtextAligner:    
    def __init__(self, sim_threshold: float = 0.55):
        self.sim_threshold = sim_threshold
        self.embedder = SlideEmbedder()
    
    def load_webtext(self, file_path: Path, course_id: str, course_name: str, 
                     course_semester: str) -> List[Dict[str, Any]]:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        sections = []
        for section_title, section_data in data.items():
            sections.append({
                "section_title": section_title,
                "text": section_data.get("text", ""),
                "tables": section_data.get("table", []),
                "links": section_data.get("links", []),
                "colors": section_data.get("colors", []),
                "metadata": {
                    "source": section_data.get("metadata", {}).get("source", ""),
                    "page_type": section_data.get("metadata", {}).get("page_type", ""),
                    "timestamp": section_data.get("metadata", {}).get("timestamp", ""),
                    "course_id": course_id,
                    "course_name": course_name,
                    "course_semester": course_semester
                }
            })
        
        print(f"Loaded {len(sections)} sections")
        return sections
    
    def prepare_text_list(self, sections: List[Dict[str, Any]]) -> List[str]:
        texts = []
        for section in sections:
            section_title = section.get('section_title', '')
            content = section.get('text', '')
            tables = section.get('tables', [])
            
            combined_parts = [section_title]
            
            if content.strip():
                combined_parts.append(content)
            
            if len(content.strip()) < 100 and tables:
                table_text = self._extract_table_text(tables)
                combined_parts.append(table_text)
            
            total_content = '\n'.join(combined_parts[1:]) if len(combined_parts) > 1 else ''
            if len(total_content.strip()) < 100:
                combined = f"{section_title}\n" * 3 + total_content
            else:
                combined = '\n'.join(combined_parts)
            
            texts.append(combined)
        return texts
    
    def _extract_table_text(self, tables: List[Dict]) -> str:
        table_texts = []
        for table in tables[:3]:  
            parts = []
            for key, value in table.items():
                if value:  
                    parts.append(f"{key}: {value}")
            if parts:
                table_texts.append(' | '.join(parts))
        return '\n'.join(table_texts)
    
    def boost_title_similarity(self, similarity_matrix, old_sections: List[Dict], 
                                new_sections: List[Dict], boost: float = 0.35):
        boosted = similarity_matrix.copy()
        
        for i, old_sec in enumerate(old_sections):
            old_title = old_sec.get('section_title', '').strip().lower()
            if not old_title:
                continue
            
            for j, new_sec in enumerate(new_sections):
                new_title = new_sec.get('section_title', '').strip().lower()
                if not new_title:
                    continue
                
                # Exact title match: apply boost
                if old_title == new_title:
                    boosted[i, j] = min(1.0, boosted[i, j] + boost)
                    print(f"Title match boosted: '{old_sec.get('section_title')}' "
                          f"({boosted[i, j] - boost:.3f} -> {boosted[i, j]:.3f})")
        
        return boosted
    
    def align_versions(self, old_sections: List[Dict], new_sections: List[Dict]) -> Dict[str, Any]:
        print(f"\n Aligning {len(old_sections)} old sections with {len(new_sections)} new sections...")
        
        # Prepare text
        old_texts = self.prepare_text_list(old_sections)
        new_texts = self.prepare_text_list(new_sections)
        
        # Get greedy matching results
        from ..comparer_pdf.align_greedy import similarity_matrix, global_best_match, detect_splits_merges
        
        # Compute base similarity matrix
        sim_matrix = similarity_matrix(self.embedder, old_texts, new_texts, w_cos=0.7, w_jac=0.3)
        
        # Boost scores for exact title matches (crucial for empty content cases)
        sim_matrix = self.boost_title_similarity(sim_matrix, old_sections, new_sections, boost=0.35)
        
        # Find best matches
        matches = global_best_match(sim_matrix, threshold=self.sim_threshold)
        
        results = detect_splits_merges(old_texts, new_texts, sim_matrix, matches, self.sim_threshold)
        results["similarity_matrix"] = sim_matrix
        results["match_method"] = "global_greedy_with_title_boost"
        
        # Build alignment output
        alignment = {
            "comparison_metadata": {
                "old_sections_count": len(old_sections),
                "new_sections_count": len(new_sections),
                "old_course_id": old_sections[0]['metadata']['course_id'] if old_sections else None,
                "new_course_id": new_sections[0]['metadata']['course_id'] if new_sections else None,
                "old_semester": old_sections[0]['metadata']['course_semester'] if old_sections else None,
                "new_semester": new_sections[0]['metadata']['course_semester'] if new_sections else None,
                "alignment_method": "global_greedy",
                "similarity_threshold": self.sim_threshold,
                "timestamp": datetime.now().isoformat()
            },
            "aligned_sections": []
        }
        
        # Process matched pairs (modified or unchanged)
        for old_idx, new_idx in results['pairs']:
            old_section = old_sections[old_idx]
            new_section = new_sections[new_idx]
            sim_score = float(results['similarity_matrix'][old_idx, new_idx])
            
            is_identical = (old_section['text'] == new_section['text'] and 
                          old_section['section_title'] == new_section['section_title'])
            
            change_type = "unchanged" if is_identical else "modified"
            
            alignment['aligned_sections'].append({
                "change_type": change_type,
                "old_section_index": old_idx,
                "new_section_index": new_idx,
                "similarity_score": sim_score,
                "old_section": old_section,
                "new_section": new_section
            })
        
        # Process splits 
        for old_idx, new_indices in results['splits']:
            old_section = old_sections[old_idx]
            new_sections_list = [new_sections[idx] for idx in new_indices]
            sim_scores = [float(results['similarity_matrix'][old_idx, idx]) for idx in new_indices]
            
            alignment['aligned_sections'].append({
                "change_type": "split",
                "old_section_index": old_idx,
                "new_section_indices": new_indices,
                "similarity_scores": sim_scores,
                "old_section": old_section,
                "new_sections": new_sections_list
            })
        
        # Process merges 
        for old_indices, new_idx in results['merges']:
            old_sections_list = [old_sections[idx] for idx in old_indices]
            new_section = new_sections[new_idx]
            sim_scores = [float(results['similarity_matrix'][idx, new_idx]) for idx in old_indices]
            
            alignment['aligned_sections'].append({
                "change_type": "merge",
                "old_section_indices": old_indices,
                "new_section_index": new_idx,
                "similarity_scores": sim_scores,
                "old_sections": old_sections_list,
                "new_section": new_section
            })
        
        # Process removed sections
        for old_idx in results['deletes']:
            old_section = old_sections[old_idx]
            alignment['aligned_sections'].append({
                "change_type": "removed",
                "old_section_index": old_idx,
                "old_section": old_section
            })
        
        # Process added sections
        for new_idx in results['inserts']:
            new_section = new_sections[new_idx]
            alignment['aligned_sections'].append({
                "change_type": "added",
                "new_section_index": new_idx,
                "new_section": new_section
            })
        
        stats = {
            'modified': sum(1 for s in alignment['aligned_sections'] if s['change_type'] == 'modified'),
            'unchanged': sum(1 for s in alignment['aligned_sections'] if s['change_type'] == 'unchanged'),
            'added': sum(1 for s in alignment['aligned_sections'] if s['change_type'] == 'added'),
            'removed': sum(1 for s in alignment['aligned_sections'] if s['change_type'] == 'removed'),
            'split': sum(1 for s in alignment['aligned_sections'] if s['change_type'] == 'split'),
            'merge': sum(1 for s in alignment['aligned_sections'] if s['change_type'] == 'merge')
        }
        
        alignment['comparison_metadata']['statistics'] = stats
        
        print("Alignment complete:")
        for change_type, count in stats.items():
            if count > 0:
                print(f"   {change_type}: {count}")
        
        return alignment
    
    def save_alignment(self, alignment: Dict, output_file: Path):
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(alignment, f, indent=2, ensure_ascii=False)
        print(f"Saved alignment to {output_file}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('old_webtext', help='Path to old version webpagetext.json')
    parser.add_argument('new_webtext', help='Path to new version webpagetext.json')
    parser.add_argument('--old-course-id', required=True, help='Old course ID')
    parser.add_argument('--new-course-id', required=True, help='New course ID')
    parser.add_argument('--old-course-name', required=True, help='Old course name')
    parser.add_argument('--new-course-name', required=True, help='New course name')
    parser.add_argument('--old-semester', required=True, help='Old semester')
    parser.add_argument('--new-semester', required=True, help='New semester')
    parser.add_argument('-o', '--output', help='Output alignment JSON file')
    parser.add_argument('-t', '--threshold', type=float, default=0.55)
    
    args = parser.parse_args()
    
    old_file = Path(args.old_webtext)
    new_file = Path(args.new_webtext)
    
    if not old_file.exists():
        print(f"Old webtext file not found: {old_file}")
        return
    if not new_file.exists():
        print(f"New webtext file not found: {new_file}")
        return
    
    if not args.output:
        output_file = old_file.parent / f"alignment_{args.old_course_id}_{args.new_course_id}.json"
    else:
        output_file = Path(args.output)
    
    aligner = WebtextAligner(sim_threshold=args.threshold)
    old_sections = aligner.load_webtext(old_file, args.old_course_id, 
                                        args.old_course_name, args.old_semester)
    new_sections = aligner.load_webtext(new_file, args.new_course_id,
                                        args.new_course_name, args.new_semester)
    
    alignment = aligner.align_versions(old_sections, new_sections)
    
    aligner.save_alignment(alignment, output_file)


if __name__ == "__main__":
    main()
