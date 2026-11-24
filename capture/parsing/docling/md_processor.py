import re
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional


class MarkdownProcessor:
    
    def __init__(self, course_versioning_path: str = None):
        self.page_pattern = re.compile(r'^<!--\s*PAGE\s+(\d+)/\d+\s*-->', re.MULTILINE)
        
        if course_versioning_path is None:
            course_versioning_path = "capture/crawling/course_ids/courses_versioning.json"
        
        self.course_lookup = self._load_course_metadata(course_versioning_path)
        
        self.heading_patterns = [
            re.compile(r'^# (.+)$', re.MULTILINE),      # H1 (biggest)
            re.compile(r'^## (.+)$', re.MULTILINE),     # H2
            re.compile(r'^### (.+)$', re.MULTILINE),    # H3
            re.compile(r'^#### (.+)$', re.MULTILINE),   # H4
        ]
        
        self.image_pattern = re.compile(r'<!--\s*image\s*-->', re.IGNORECASE)
    
    def _load_course_metadata(self, path: str) -> Dict[str, Dict[str, str]]:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            lookup = {}
            for course in data.get('courses', []):
                course_title = course.get('title', 'Unknown Course')
                for sem in course.get('semesters', []):
                    course_id = sem.get('course_id')
                    semester = sem.get('semester', 'Unknown Semester')
                    if course_id:
                        lookup[course_id] = {
                            'course_name': course_title,
                            'semester': semester
                        }
            
            return lookup
        except Exception as e:
            print(f"Could not load course metadata from {path}: {e}")
            return {}

    def _load_document_metadata(self, course_id: str, pdf_filename: str = None) -> Optional[Dict[str, Any]]:
        try:
            doc_json_path = Path(f"data/course_{course_id}/document/documents.json")
            
            if not doc_json_path.exists():
                print(f"documents.json not found at {doc_json_path}")
                return None
            
            with open(doc_json_path, 'r', encoding='utf-8') as f:
                documents = json.load(f)
            
            if pdf_filename:
                for doc in documents:
                    if doc.get('saved_filename') == pdf_filename:
                        return doc
                print(f"No document found for filename: {pdf_filename}")
                return None
            
            return documents[0] if documents else None
            
        except Exception as e:
            print(f"Could not load document metadata: {e}")
            return None
    
    def _extract_pdf_filename_from_markdown(self, markdown_path: str) -> Optional[str]:
        md_path = Path(markdown_path)
        stem = md_path.stem
        
        for suffix in ['_old', '_new', '_v1', '_v2', '_version1', '_version2']:
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
                break
        
        if '_document' in stem:
            return f"{stem}.pdf"
        
        return None
    
    def parse_markdown_file(self, file_path: str) -> List[Dict[str, Any]]:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        pages = []
        
        page_splits = self.page_pattern.split(content)
        
        if len(page_splits) > 1:
            for i in range(1, len(page_splits), 2):
                if i + 1 < len(page_splits):
                    page_number = int(page_splits[i])
                    page_content = page_splits[i + 1].strip()
                    
                    if page_content:  
                        page_data = self.extract_page_data(page_number, page_content)
                        if page_data:
                            pages.append(page_data)
        return pages
    
    def extract_page_data(self, page_number: int, content: str) -> Optional[Dict[str, Any]]:
        title = self.extract_title(content)
        if not title:
            title = f"Page {page_number}"
        
        clean_content = self.clean_page_content(content, title)
        clean_title = re.sub(r'\*\*(.*?)\*\*', r'\1', title) 
        clean_title = re.sub(r'[−–]\s*', '- ', clean_title)
        
        urls = re.findall(r'https?://[^\s\)]+', content)
        tables = self.extract_tables(content)
        
        clean_content = self.replace_tables_with_references(clean_content, tables)
        
        extracted_titles = []
        for table in tables:
            if 'extracted_title' in table:
                extracted_titles.append(table['extracted_title'])
        
        if extracted_titles:
            extracted_text = '\n\n' + '\n\n'.join(extracted_titles)
            clean_content = (clean_content + extracted_text).strip()
        
        content_lines = self.content_to_lines(clean_content)

        return {
            'page_number': page_number,
            'title': clean_title,
            'content': clean_content,              
            'content_lines': content_lines,        
            'urls': urls,
            'tables': tables,                      
            'raw_content': content  # Keep original for debug
        }

    def clean_page_content(self, content: str, title: str) -> str:
        content = self.remove_title_from_content(content, title) 
        content = self.image_pattern.sub('', content)  # Remove <!-- image --> markers
        
        # Normalize markdown formatting
        content = re.sub(r'^#{1,6}\s*', '', content, flags=re.MULTILINE)  
        content = re.sub(r'\*\*(.*?)\*\*', r'\1', content)  
        content = re.sub(r'#{1,6}\s*•\s*', '• ', content, flags=re.MULTILINE) 
        content = re.sub(r'[−–]\s*', '- ', content) 
        
        # Normalize various bullet formats
        content = re.sub(r'^-\s*-\s*', '- ', content, flags=re.MULTILINE)  
        content = re.sub(r'^-\s+', '- ', content, flags=re.MULTILINE) 
        content = re.sub(r'^·\s*', '- ', content, flags=re.MULTILINE) 
        content = re.sub(r'^\*\s*', '- ', content, flags=re.MULTILINE) 
        
        # Remove academic semester footers/headers ( "Organisation und Inhalt | Manfred Hauswirth | Einführung in die Programmierung, WS 24/25")
        content = self.remove_semester_footers(content)
        
        content = re.sub(r'\n\s*\n\s*\n+', '\n\n', content) 
        return content.strip()
    
    def remove_semester_footers(self, content: str) -> str:
        lines = content.split('\n')
        clean_lines = []
        semester_pattern = re.compile(
            r'^[^|]*\|[^|]*\|.*(?:WS|WiSe|SoSe|SS)\s*(?:\d{2,4}(?:/\d{2,4})?|\d{2,4}).*$',
            re.IGNORECASE
        )
        
        for line in lines:
            line = line.strip()
            if line.count('|') >= 2 and semester_pattern.match(line):
                continue
            else:
                clean_lines.append(line)
        
        return '\n'.join(clean_lines)
    
    def content_to_lines(self, content: str) -> List[str]:
        lines = []
        for line in content.split('\n'):
            line = line.strip()
            line = re.sub(r' {2,}', ' ', line)
            if line:  
                lines.append(line)
        return lines

    def extract_title(self, content: str) -> Optional[str]:
        for pattern in self.heading_patterns:
            matches = pattern.findall(content)
            if matches:
                return matches[0].strip()
        
        return None
    
    def extract_tables(self, content: str) -> List[Dict[str, Any]]:
        tables = []
        extracted_titles = [] 
        table_pattern = re.compile(
            r'(\|[^\n]+\|\n\|[-:|\s]+\|\n(?:\|[^\n]+\|\n?)+)',
            re.MULTILINE
        )
        
        for match in table_pattern.finditer(content):
            table_text = match.group(1).strip()
            parsed_table = self.parse_markdown_table(table_text)
            if parsed_table:
                table_entry = {
                    'raw_table': table_text,
                    'headers': parsed_table['headers'],
                    'rows': parsed_table['rows'],
                    '_position': match.start()  
                }
                
                if 'extracted_title' in parsed_table:
                    table_entry['extracted_title'] = parsed_table['extracted_title']
                    extracted_titles.append(parsed_table['extracted_title'])
                
                tables.append(table_entry)
        
        return tables
    
    def parse_markdown_table(self, table_text: str) -> Optional[Dict[str, Any]]:
        lines = [line.strip() for line in table_text.split('\n') if line.strip()]
        
        if len(lines) < 3:  
            return None
        
        header_line = lines[0]
        headers = [cell.strip() for cell in header_line.split('|')[1:-1]]  

        rows = []
        for line in lines[2:]:
            if line.startswith('|') and line.endswith('|'):
                cells = [cell.strip() for cell in line.split('|')[1:-1]]
                if len(cells) == len(headers): 
                    rows.append(cells)
        
        cleaned_result = self.clean_false_table_headers(headers, rows)
        if cleaned_result:
            return cleaned_result
        
        return {
            'headers': headers,
            'rows': rows
        }
    
    def clean_false_table_headers(self, headers: List[str], rows: List[List[str]]) -> Optional[Dict[str, Any]]:
        if not headers or not rows:
            return None
        
        first_header = headers[0]
        other_headers = headers[1:]
        
        generic_pattern = re.compile(r'^(Col\d+|Column\d*|\s*)$', re.IGNORECASE)
        has_generic_headers = all(generic_pattern.match(h) for h in other_headers)
        
        if has_generic_headers and len(headers) > 1:
            if rows and len(rows[0]) == len(headers):
                actual_headers = rows[0]
                actual_data_rows = rows[1:]
                
                return {
                    'headers': actual_headers,
                    'rows': actual_data_rows,
                    'extracted_title': first_header  
                }
        
        return None
    
    def replace_tables_with_references(self, content: str, tables: List[Dict[str, Any]]) -> str:
        if not tables:
            return content
        
        sorted_tables = sorted(tables, key=lambda t: t['_position'], reverse=True)
        
        for table in sorted_tables:
            table_title = table.get('extracted_title', 'Table')
            headers = ', '.join(table['headers'])
            row_count = len(table['rows'])
            
            table_reference = f"[TABLE: {table_title} - Columns: {headers} - Rows: {row_count}]"
            
            raw_table = table['raw_table']
            if raw_table in content:
                content = content.replace(raw_table, table_reference)
        
        return content
    
    def remove_title_from_content(self, content: str, title: str) -> str:
        for pattern in self.heading_patterns:
            match = pattern.search(content)
            if match and match.group(1).strip() == title:
                content = content.replace(match.group(0), '', 1)
                break
        
        return content
    

    
    def process_to_json(self, markdown_file: str, output_file: str = None) -> List[Dict[str, Any]]:
        print(f"Processing Docling markdown file: {markdown_file}")
        pages = self.parse_markdown_file(markdown_file)
        print(f"Processing {len(pages)} pages")
        
        # Extract course_id from file path (e.g., data/course_30422/...)
        course_id = None
        path_parts = Path(markdown_file).parts
        for part in path_parts:
            if part.startswith('course_'):
                course_id = part.split('_')[1]
                break
        
        # If not found in path, try extracting from filename
        if not course_id:
            stem = Path(markdown_file).stem
            match = re.match(r'^(\d{5})_', stem)
            if match:
                course_id = match.group(1)
        
        # metadata from documents.json
        doc_metadata = None
        if course_id:
            pdf_filename = self._extract_pdf_filename_from_markdown(markdown_file)
            if pdf_filename:
                loaded_doc_meta = self._load_document_metadata(course_id, pdf_filename)
                if loaded_doc_meta:
                    print(f"Loaded metadata from documents.json: {loaded_doc_meta.get('title', 'N/A')}")
                    doc_metadata = {
                        'course_id': course_id,
                        'document_title': loaded_doc_meta.get('title', '').replace('\n', ' '),
                        'document_file': loaded_doc_meta.get('saved_filename'),
                        'document_url': loaded_doc_meta.get('download_url'),
                        'moodle_url': loaded_doc_meta.get('moodle_url')
                    }
                else:
                    print(f"Could not find metadata for PDF: {pdf_filename}")
            else:
                print(f"Could not extract PDF filename from: {markdown_file}")
        
        course_info = self.course_lookup.get(course_id, {
            'course_name': 'Unknown Course',
            'semester': 'Unknown Semester'
        })
        
        with open(markdown_file, 'rb') as f:
            doc_hash = hashlib.md5(f.read()).hexdigest()
        
        ingest_ts = datetime.now(timezone.utc).isoformat()
        chunks = []
        
        for i, page in enumerate(pages):
            page_number = page['page_number']
            chunk_id = f"{doc_hash}-p{page_number}"
            
            prev_chunk_id = None
            next_chunk_id = None
            if i > 0:
                prev_page_number = pages[i-1]['page_number']
                prev_chunk_id = f"{doc_hash}-p{prev_page_number}"
            if i < len(pages) - 1:
                next_page_number = pages[i+1]['page_number']
                next_chunk_id = f"{doc_hash}-p{next_page_number}"
            
            chunk = {
                'page_number': page['page_number'],
                'title': page['title'],
                'content': page['content'],
                'content_lines': page['content_lines'],
                'metadata': {
                    'node_type': 'DocVersion',
                    'source': 'pdf',
                    'course_id': course_id,
                    'course_name': course_info['course_name'],
                    'course_semester': course_info['semester'],
                    'chunk_type': 'pdf_page',
                    'document_file': doc_metadata.get('document_file', Path(markdown_file).stem) if doc_metadata else Path(markdown_file).stem,
                    'document_title': doc_metadata.get('document_title', page['title']) if doc_metadata else page['title'],
                    'document_url': doc_metadata.get('document_url') if doc_metadata else None,
                    'moodle_url': doc_metadata.get('moodle_url') if doc_metadata else None,
                    'chunk_id': chunk_id,
                    'prev_chunk_id': prev_chunk_id,
                    'next_chunk_id': next_chunk_id,
                    'ingest_ts': ingest_ts,
                    'urls': page.get('urls', []),
                    'tables': page.get('tables', [])
                }
            }
            
            chunks.append(chunk)
        
        if output_file:
            self.save_json(chunks, output_file)
            print(f"Saved to: {output_file}")
        
        return chunks
    
    def save_json(self, chunks: List[Dict[str, Any]], output_file: str) -> None:
        clean_chunks = []
        for chunk in chunks:
            clean_chunk = chunk.copy()
            
            if 'metadata' in clean_chunk and 'tables' in clean_chunk['metadata']:
                clean_tables = []
                for table in clean_chunk['metadata']['tables']:
                    clean_table = {k: v for k, v in table.items() if not k.startswith('_')}
                    clean_tables.append(clean_table)
                clean_chunk['metadata']['tables'] = clean_tables
                
            clean_chunks.append(clean_chunk)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(clean_chunks, f, ensure_ascii=False, indent=2)
    
def main():
    import argparse    
    parser = argparse.ArgumentParser(description="Convert markdown to structured JSON chunks for DocVersion nodes")
    parser.add_argument('markdown_file', help='Input markdown file')
    parser.add_argument('-o', '--output', help='Output JSON file (default: same name with .json extension)')
    
    args = parser.parse_args()
    if not args.output:
        input_path = Path(args.markdown_file)
        args.output = str(input_path.with_suffix('.json'))
    
    processor = MarkdownProcessor()
    processor.process_to_json(
        args.markdown_file, 
        args.output
    )

if __name__ == "__main__":
    main()