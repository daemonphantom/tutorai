import re
from typing import List, Dict, Any


class WebtextChunker:
    
    def __init__(self, char_limit: int = 2400):
        self.char_limit = char_limit
    
    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4
    
    def needs_chunking(self, text: str) -> bool:
        return len(text) > self.char_limit
    
    def chunk_text(self, text: str) -> List[str]:
        if not text or len(text) <= self.char_limit:
            return [text]
        
        sentences = re.split(r'(?<=[.!?])\s+', text)
        
        chunks = []
        current_chunk = ""
        
        for sentence in sentences:
            if current_chunk and len(current_chunk) + len(sentence) + 1 > self.char_limit:
                chunks.append(current_chunk.strip())
                current_chunk = sentence
            else:
                if current_chunk:
                    current_chunk += " " + sentence
                else:
                    current_chunk = sentence
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks
    
    def chunk_section(self, section: Dict[str, Any], section_index: int) -> List[Dict[str, Any]]:
        text = section.get('text', '')
        
        if not self.needs_chunking(text):
            return [{
                "section_title": section.get('section_title', ''),
                "section_index": section_index,
                "chunk_index": 0,
                "total_chunks": 1,
                "content": text,
                "content_chars": len(text),
                "content_tokens": self.estimate_tokens(text),
                "tables": section.get('tables', []),
                "links": section.get('links', []),
                "metadata": section.get('metadata', {})
            }]
        
        text_chunks = self.chunk_text(text)
        total_chunks = len(text_chunks)
        
        chunked_sections = []
        for i, chunk_text in enumerate(text_chunks):
            chunked_sections.append({
                "section_title": section.get('section_title', ''),
                "section_index": section_index,
                "chunk_index": i,
                "total_chunks": total_chunks,
                "content": chunk_text,
                "content_chars": len(chunk_text),
                "content_tokens": self.estimate_tokens(chunk_text),
                "tables": section.get('tables', []) if i == 0 else [],
                "links": section.get('links', []) if i == 0 else [],
                "metadata": section.get('metadata', {})
            })
        
        return chunked_sections
    
    def chunk_aligned_sections(self, alignment: Dict[str, Any]) -> Dict[str, Any]:
        chunked_alignment = {
            "comparison_metadata": alignment["comparison_metadata"],
            "chunked_sections": []
        }
        
        stats = {'sections_chunked': 0, 'total_chunks': 0, 'sections_unchanged_kept': 0}
        
        for aligned_section in alignment['aligned_sections']:
            change_type = aligned_section['change_type']
            
            if change_type == 'unchanged':
                chunked_alignment['chunked_sections'].append(aligned_section)
                stats['sections_unchanged_kept'] += 1
                continue
            
            if change_type == 'modified':
                old_section = aligned_section['old_section']
                new_section = aligned_section['new_section']
                
                old_chunks = self.chunk_section(old_section, aligned_section['old_section_index'])
                new_chunks = self.chunk_section(new_section, aligned_section['new_section_index'])
                
                chunked_alignment['chunked_sections'].append({
                    "change_type": "modified",
                    "old_section_index": aligned_section['old_section_index'],
                    "new_section_index": aligned_section['new_section_index'],
                    "similarity_score": aligned_section['similarity_score'],
                    "old_chunks": old_chunks,
                    "new_chunks": new_chunks
                })
                
                if len(old_chunks) > 1 or len(new_chunks) > 1:
                    stats['sections_chunked'] += 1
                stats['total_chunks'] += len(old_chunks) + len(new_chunks)
            
            elif change_type == 'added':
                new_section = aligned_section['new_section']
                new_chunks = self.chunk_section(new_section, aligned_section['new_section_index'])
                
                chunked_alignment['chunked_sections'].append({
                    "change_type": "added",
                    "new_section_index": aligned_section['new_section_index'],
                    "new_chunks": new_chunks
                })
                
                if len(new_chunks) > 1:
                    stats['sections_chunked'] += 1
                stats['total_chunks'] += len(new_chunks)
            
            elif change_type == 'removed':
                old_section = aligned_section['old_section']
                old_chunks = self.chunk_section(old_section, aligned_section['old_section_index'])
                
                chunked_alignment['chunked_sections'].append({
                    "change_type": "removed",
                    "old_section_index": aligned_section['old_section_index'],
                    "old_chunks": old_chunks
                })
                
                if len(old_chunks) > 1:
                    stats['sections_chunked'] += 1
                stats['total_chunks'] += len(old_chunks)
            
            elif change_type == 'split':
                old_section = aligned_section['old_section']
                new_sections = aligned_section['new_sections']
                
                old_chunks = self.chunk_section(old_section, aligned_section['old_section_index'])
                new_chunks_list = [
                    self.chunk_section(ns, aligned_section['new_section_indices'][i])
                    for i, ns in enumerate(new_sections)
                ]
                
                chunked_alignment['chunked_sections'].append({
                    "change_type": "split",
                    "old_section_index": aligned_section['old_section_index'],
                    "new_section_indices": aligned_section['new_section_indices'],
                    "similarity_scores": aligned_section['similarity_scores'],
                    "old_chunks": old_chunks,
                    "new_chunks_list": new_chunks_list
                })
                
                total_new_chunks = sum(len(chunks) for chunks in new_chunks_list)
                if len(old_chunks) > 1 or total_new_chunks > len(new_sections):
                    stats['sections_chunked'] += 1
                stats['total_chunks'] += len(old_chunks) + total_new_chunks
            
            elif change_type == 'merge':
                old_sections = aligned_section['old_sections']
                new_section = aligned_section['new_section']
                
                old_chunks_list = [
                    self.chunk_section(os, aligned_section['old_section_indices'][i])
                    for i, os in enumerate(old_sections)
                ]
                new_chunks = self.chunk_section(new_section, aligned_section['new_section_index'])
                
                chunked_alignment['chunked_sections'].append({
                    "change_type": "merge",
                    "old_section_indices": aligned_section['old_section_indices'],
                    "new_section_index": aligned_section['new_section_index'],
                    "similarity_scores": aligned_section['similarity_scores'],
                    "old_chunks_list": old_chunks_list,
                    "new_chunks": new_chunks
                })
                
                total_old_chunks = sum(len(chunks) for chunks in old_chunks_list)
                if total_old_chunks > len(old_sections) or len(new_chunks) > 1:
                    stats['sections_chunked'] += 1
                stats['total_chunks'] += total_old_chunks + len(new_chunks)
        
        chunked_alignment['comparison_metadata']['chunking_stats'] = stats
        
        return chunked_alignment
