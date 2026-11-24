import json
import argparse
from pathlib import Path
from .align_webtext import WebtextAligner
from .webtext_chunker import WebtextChunker
from .changes_webtext import WebtextChangeDetector


class WebtextComparisonRunner:    
    def __init__(self, pairs_file: str, output_dir: str = None, char_limit: int = 2000,
                 sim_threshold: float = 0.55):
        self.pairs_file = Path(pairs_file)
        with open(self.pairs_file, 'r', encoding='utf-8') as f:
            self.pairs = json.load(f)
        
        self.base_output_dir = Path(output_dir) if output_dir else Path("data/comparison/webtext")
        self.base_output_dir.mkdir(parents=True, exist_ok=True)
        self.char_limit = char_limit
        self.sim_threshold = sim_threshold
        
        self.versioning_file = Path("capture/crawling/course_ids/courses_versioning.json")
        with open(self.versioning_file, 'r', encoding='utf-8') as f:
            self.versioning_data = json.load(f)
    
    def get_course_metadata(self, course_id: str) -> dict:
        for course in self.versioning_data['courses']:
            course_name = course.get('title', 'Unknown')
            for semester_info in course.get('semesters', []):
                if semester_info.get('course_id') == course_id:
                    return {
                        'course_id': course_id,
                        'course_name': course_name,
                        'course_semester': semester_info.get('semester', 'Unknown')
                    }
        return {
            'course_id': course_id,
            'course_name': 'Unknown',
            'course_semester': 'Unknown'
        }
    
    def process_pair(self, pair: dict) -> bool:
        pid = pair["pair_id"]
        old_file = Path(pair["old"])
        new_file = Path(pair["new"])
        
        if not old_file.exists():
            return False
        if not new_file.exists():
            return False
        
        output_dir = self.base_output_dir / pid
        output_dir.mkdir(parents=True, exist_ok=True)
        
        old_course_id = old_file.parts[-3].replace('course_', '')
        new_course_id = new_file.parts[-3].replace('course_', '')
        
        old_meta = self.get_course_metadata(old_course_id)
        new_meta = self.get_course_metadata(new_course_id)
        
        alignment_file = output_dir / f"alignment_{old_course_id}_{new_course_id}.json"
        chunked_file = output_dir / f"chunked_{old_course_id}_{new_course_id}.json"
        changes_file = output_dir / f"changes_{old_course_id}_{new_course_id}.json"
        html_file = output_dir / f"diff_{pid}.html"
        
        try:
            aligner = WebtextAligner(sim_threshold=self.sim_threshold)
            
            old_sections = aligner.load_webtext(
                old_file, old_meta['course_id'], old_meta['course_name'], old_meta['course_semester']
            )
            new_sections = aligner.load_webtext(
                new_file, new_meta['course_id'], new_meta['course_name'], new_meta['course_semester']
            )
            print("Step 1/4: Aligning Versions:")
            alignment = aligner.align_versions(old_sections, new_sections)
            aligner.save_alignment(alignment, alignment_file)
            
            print("Step 2/4: Chunking large sections for LLM:")
            chunker = WebtextChunker(char_limit=self.char_limit)
            chunked_alignment = chunker.chunk_aligned_sections(alignment)
            
            with open(chunked_file, 'w', encoding='utf-8') as f:
                json.dump(chunked_alignment, f, indent=2, ensure_ascii=False)
            print(f"Saved chunked alignment to {chunked_file}")
            
            print("Step 3/4: Analyzing changes with LLM:")
            detector = WebtextChangeDetector()
            changes = detector.process_chunked_alignment(chunked_alignment)
            
            with open(changes_file, 'w', encoding='utf-8') as f:
                json.dump(changes, f, indent=2, ensure_ascii=False)
            print(f"Saved changes analysis to {changes_file}")
            
            print("Step 4/4: Generating HTML visualization")
            if not Path(changes_file).exists():
                print(f"Changes file not created: {changes_file}")
                return False
            self.gen_html(changes, str(html_file), pid)
            
            print(f"Pair {pid} completed")
            print(f"Results saved to: {output_dir}")
            return True
            
        except Exception as e:
            print(f"Error processing pair {pid}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def gen_html(self, changes: dict, html_output: str, pair_id: str):
        meta = changes.get('metadata', {})
        comp_meta = changes.get('comparison_metadata', {})
        
        stats = comp_meta.get('statistics', {})
        chunk_stats = comp_meta.get('chunking_stats', {})
        
        alignment_file = Path(html_output).parent / f"alignment_{meta.get('old_course_id')}_{meta.get('new_course_id')}.json"
        old_sections = {}
        new_sections = {}
        try:
            with open(alignment_file, 'r') as f:
                alignment = json.load(f)
                for section_info in alignment.get("sections", []):
                    if "old_section" in section_info:
                        idx = section_info.get("old_section_index")
                        old_sections[idx] = section_info["old_section"]
                    if "new_section" in section_info:
                        idx = section_info.get("new_section_index")
                        new_sections[idx] = section_info["new_section"]
        except:
            pass
        
        html = f'''<!doctype html><html lang="de"><head><meta charset="utf-8"><title>{pair_id}</title><style>
            body{{font-family:system-ui,sans-serif;margin:24px;background:#fafafa;line-height:1.4}}
            .container{{max-width:1400px;margin:0 auto;background:white;padding:32px;border-radius:12px}}
            .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}}
            .stat-box{{background:#f9f9f9;padding:12px;border-radius:6px;text-align:center}}
            .section{{border:1px solid #e3e3e3;border-radius:8px;padding:16px;margin:16px 0}}
            .change-badge{{display:inline-block;padding:4px 12px;border-radius:12px;font-size:12px}}
            .badge-modified{{background:#fff3cd;color:#856404}}
            .badge-added{{background:#d4edda;color:#155724}}
            .badge-removed{{background:#f8d7da;color:#721c24}}
            .badge-unchanged{{background:#e7e7e7;color:#555}}
            h1{{margin:0 0 16px 0}}
            h2{{margin:16px 0 8px 0;font-size:18px}}
            h3{{margin:12px 0 8px 0;font-size:14px;color:#333}}
            del{{text-decoration:line-through;background:#ffecec;padding:2px 4px}}
            ins{{background:#e8ffea;padding:2px 4px}}
            .changes-list{{margin:12px 0}}
            .change-item{{margin:4px 0;padding:4px 0}}
            .cols{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:12px}}
            .panel{{border:1px solid #e3e3e3;border-radius:8px;padding:12px;background:#fafafa}}
            .panel h3{{margin:0 0 8px 0}}
            .panel-content{{white-space:pre-wrap;font-size:13px;line-height:1.6}}
            .overview{{background:#f0f8ff;padding:16px;border-radius:8px;margin:20px 0}}
            .overview p{{margin:4px 0}}
            </style></head><body><div class="container"><h1>{pair_id}</h1>
            <div class="overview">
                <p><strong>Old Course:</strong> {meta.get('old_course_id')} ({meta.get('old_semester')})</p>
                <p><strong>New Course:</strong> {meta.get('new_course_id')} ({meta.get('new_semester')})</p>
                <p><strong>Total Sections Analyzed:</strong> {changes.get('total_sections_analyzed', 0)}</p>
                <p><strong>Sections with Changes:</strong> {changes.get('sections_with_changes', 0)}</p>
            </div>
            <div class="stats">'''
        
        if stats:
            for change_type, count in sorted(stats.items()):
                if count > 0:
                    html += f'<div class="stat-box"><div style="font-size:24px;font-weight:bold">{count}</div><div>{change_type}</div></div>'
        
        if chunk_stats:
            html += f'<div class="stat-box"><div style="font-size:24px;font-weight:bold">{chunk_stats.get("sections_chunked", 0)}</div><div>sections chunked</div></div>'
            html += f'<div class="stat-box"><div style="font-size:24px;font-weight:bold">{chunk_stats.get("total_chunks", 0)}</div><div>total chunks</div></div>'
        
        html += '</div>'
        
        for section in changes.get('detailed_changes', []):
            change_type = section.get('change_type')
            if change_type == 'unchanged':
                continue 
            
            section_title = section.get('section_title', 'Untitled')
            html += f'<div class="section"><div><strong>{section_title}</strong> <span class="change-badge badge-{change_type}">{change_type}</span></div>'
            
            if section.get('changes'):
                html += '<h2>Änderungen</h2><ul class="changes-list">'
                for change in section.get('changes', []):
                    change_type_detail = change.get('type', change.get('description', 'Unknown change'))
                    html += f'<li><strong>{change_type_detail}</strong>: '
                    
                    old_text = change.get('old_text')
                    new_text = change.get('new_text')
                    description = change.get('description')
                    
                    if old_text and new_text:
                        html += f'<del>{self._esc(old_text)}</del> → <ins>{self._esc(new_text)}</ins>'
                    elif new_text:
                        html += f'<ins>{self._esc(new_text)}</ins>'
                    elif old_text:
                        html += f'<del>{self._esc(old_text)}</del>'
                    elif description:
                        html += self._esc(description)
                    html += '</li>'
                html += '</ul>'
            else:
                note = section.get('note', 'No detailed changes')
                html += f'<p>{self._esc(note)}</p>'
            
            if change_type == 'modified':
                old_idx = section.get('old_section_index')
                new_idx = section.get('new_section_index')
                old_content = old_sections.get(old_idx, {}).get("content", "")
                new_content = new_sections.get(new_idx, {}).get("content", "")
                
                if old_content or new_content:
                    html += '<h2>Seitenansicht</h2><div class="cols">'
                    html += '<div class="panel"><h3>Alt</h3>'
                    html += f'<div class="panel-content">{self._format_content_with_changes(old_content, new_content, is_old=True)}</div></div>'
                    html += '<div class="panel"><h3>Neu</h3>'
                    html += f'<div class="panel-content">{self._format_content_with_changes(old_content, new_content, is_old=False)}</div></div>'
                    html += '</div>'
            
            html += '</div>'
        
        html += '</div></body></html>'
        Path(html_output).write_text(html, encoding='utf-8')
    
    def _esc(self, text):
        return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
    
    def _format_content_with_changes(self, old_content: str, new_content: str, is_old: bool):
        old_lines = old_content.split('\n')
        new_lines = new_content.split('\n')
        
        if is_old:
            result = []
            for line in old_lines:
                if line.strip() and line not in new_lines:
                    result.append(f'<del>{self._esc(line)}</del>')
                else:
                    result.append(self._esc(line))
            return '<br>'.join(result)
        else:
            result = []
            for line in new_lines:
                if line.strip() and line not in old_lines:
                    result.append(f'<ins>{self._esc(line)}</ins>')
                else:
                    result.append(self._esc(line))
            return '<br>'.join(result)
    
    def run(self):
        success_count = 0
        for i, pair in enumerate(self.pairs, 1):
            print(f"# PAIR {i}/{len(self.pairs)}: {pair['pair_id']}")
            
            if self.process_pair(pair):
                success_count += 1
        
        print(f"Successfully processed: {success_count}/{len(self.pairs)}")
        
        return success_count == len(self.pairs)


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--pairs', required=True, 
                       help='Path to pairs.json file')
    parser.add_argument('--output-dir', default=None,
                       help='Base output directory (default: data/comparison/webtext)')
    parser.add_argument('--char-limit', type=int, default=2000,
                       help='Character limit for chunking (default: 2000= ~600 tokens)')
    parser.add_argument('--sim-threshold', type=float, default=0.55,
                       help='Similarity threshold for alignment (default: 0.55)')
    
    args = parser.parse_args()
    
    runner = WebtextComparisonRunner(
        args.pairs,
        args.output_dir,
        args.char_limit,
        args.sim_threshold
    )
    success = runner.run()
    
    exit(0 if success else 1)


if __name__ == "__main__":
    main()
