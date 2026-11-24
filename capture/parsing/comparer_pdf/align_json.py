"""
JSON Slide Comparer - PDF slides
"""

import json
import argparse
import sys
from pathlib import Path
from typing import List, Dict, Any
from .align_greedy import compare_slide_versions_global, SlideEmbedder




def load_json_slides(json_file: str) -> List[Dict[str, Any]]:
    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if not isinstance(data, list):
            raise ValueError(f"Expected JSON array, got {type(data)}")
        
        return data
    except Exception as e:
        print(f"Error loading {json_file}: {e}")
        sys.exit(1)

def extract_slide_content(slides: List[Dict[str, Any]], content_source: str = "content") -> List[str]:
    slide_texts = []
    
    for slide in slides:
        if content_source == "content":
            text = slide.get("content", "").strip()
        elif content_source == "content_lines":
            lines = slide.get("content_lines", [])
            text = "\n".join(lines).strip()
        elif content_source == "combined":
            title = slide.get("title", "").strip()
            content = slide.get("content", "").strip()
            text = f"{title}\n\n{content}".strip() if title else content
        else:
            raise ValueError(f"Unknown content_source {content_source}")
        
        if not text:
            page_num = slide.get("page_number", "?")
            title = slide.get("title", "Untitled")
            text = f"[Empty slide - Page {page_num}: {title}]"
        
        slide_texts.append(text)
    
    return slide_texts

def create_change_tracking_json(old_slides: List[Dict], new_slides: List[Dict], results: Dict, sim_thresh: float = 0.55) -> Dict[str, Any]:
    change_tracking = {
        "comparison_metadata": {
            "old_slide_count": len(old_slides),
            "new_slide_count": len(new_slides),
            "similarity_threshold": sim_thresh,
            "aligned_pairs": len(results["pairs"]),
            "splits": len(results["splits"]),
            "merges": len(results["merges"]),
            "insertions": len(results["inserts"]),
            "deletions": len(results["deletes"])
        },
        "slides": []
    }
    
    processed_old = set()
    processed_new = set()
    
    # aligned (unchanged or modified) pairs
    for old_idx, new_idx in results["pairs"]:
        similarity = float(results["similarity_matrix"][old_idx, new_idx])
        
        slide_entry = {
            "change_type": "unchanged" if similarity > 0.95 else "modified",
            "old_slide_index": old_idx,
            "new_slide_index": new_idx,
            "similarity_score": round(similarity, 3),
            "old_slide": old_slides[old_idx],
            "new_slide": new_slides[new_idx]
        }
        
        change_tracking["slides"].append(slide_entry)
        processed_old.add(old_idx)
        processed_new.add(new_idx)
    
    # merges
    for old_indices, new_idx in results["merges"]:
        similarities = [float(results["similarity_matrix"][old_idx, new_idx]) for old_idx in old_indices]
        
        slide_entry = {
            "change_type": "merge",
            "old_slide_indices": old_indices,
            "new_slide_index": new_idx,
            "similarity_scores": [round(s, 3) for s in similarities],
            "old_slides": [old_slides[idx] for idx in old_indices],
            "new_slide": new_slides[new_idx]
        }
        
        change_tracking["slides"].append(slide_entry)
        processed_old.update(old_indices)
        processed_new.add(new_idx)
    
    # splits
    for old_idx, new_indices in results["splits"]:
        similarities = [float(results["similarity_matrix"][old_idx, new_idx]) for new_idx in new_indices]
        
        slide_entry = {
            "change_type": "split",
            "old_slide_index": old_idx,
            "new_slide_indices": new_indices,
            "similarity_scores": [round(s, 3) for s in similarities],
            "old_slide": old_slides[old_idx],
            "new_slides": [new_slides[idx] for idx in new_indices]
        }
        
        change_tracking["slides"].append(slide_entry)
        processed_old.add(old_idx)
        processed_new.update(new_indices)
    
    # deletions
    for old_idx in results["deletes"]:
        slide_entry = {
            "change_type": "removed",
            "old_slide_index": old_idx,
            "new_slide_index": None,
            "old_slide": old_slides[old_idx],
            "new_slide": None
        }
        
        change_tracking["slides"].append(slide_entry)
        processed_old.add(old_idx)
    
    # insertions
    for new_idx in results["inserts"]:
        slide_entry = {
            "change_type": "added",
            "old_slide_index": None,
            "new_slide_index": new_idx,
            "old_slide": None,
            "new_slide": new_slides[new_idx]
        }
        
        change_tracking["slides"].append(slide_entry)
        processed_new.add(new_idx)
    
    # Sort slides by the minimum old_slide_index 
    def sort_key(slide):
        if "old_slide_index" in slide and slide["old_slide_index"] is not None:
            return slide["old_slide_index"]
        elif "old_slide_indices" in slide:
            return min(slide["old_slide_indices"])
        else:
            return float('inf')
    
    change_tracking["slides"].sort(key=sort_key)
    
    return change_tracking

def compare_json_slides(old_json: str, new_json: str, 
                       content_source: str = "combined",
                       sim_thresh: float = 0.55,
                       json_output_file: str = None) -> Dict:

    old_slides = load_json_slides(old_json)
    new_slides = load_json_slides(new_json)
    
    old_texts = extract_slide_content(old_slides, content_source)
    new_texts = extract_slide_content(new_slides, content_source)
    
    embedder = SlideEmbedder()
    
    results = compare_slide_versions_global(
        old_texts, new_texts, 
        embedder=embedder,
        sim_thresh=sim_thresh
    )
    
    if json_output_file:
        change_tracking = create_change_tracking_json(old_slides, new_slides, results, sim_thresh)
        
        with open(json_output_file, 'w', encoding='utf-8') as f:
            json.dump(change_tracking, f, indent=2, ensure_ascii=False)
    
    return results

def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument("old_json", help="Path of the old version JSON file")
    parser.add_argument("new_json", help="Path of the new version JSON file") 
    parser.add_argument("--content-source", choices=["content", "content_lines", "combined"],
                       default="combined", help="Which content field to use for comparison (combined is best)")
    parser.add_argument("--threshold", type=float, default=0.55,
                       help="Similarity threshold for split/merge detection (0.0-1.0)")
    parser.add_argument("--json-output", "-j", required=True,
                       help="Output file for structured JSON change tracking (REQUIRED)")
    
    args = parser.parse_args()
    
    for json_file in [args.old_json, args.new_json]:
        if not Path(json_file).exists():
            print(f"file not found: {json_file}", file=sys.stderr)
            sys.exit(1)
    
    try:
        compare_json_slides(
            args.old_json, args.new_json,
            content_source=args.content_source,
            sim_thresh=args.threshold,
            json_output_file=args.json_output
        )
    except Exception as e:
        print(f"failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()