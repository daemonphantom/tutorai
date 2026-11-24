from dataclasses import dataclass
from typing import Optional

@dataclass
class SearchFilters:
    course_id: Optional[str] = None
    semester: Optional[str] = None
    author: Optional[str] = None
    author_role: Optional[str] = None
    has_links: Optional[bool] = None
    has_attachments: Optional[bool] = None
    before: Optional[str] = None
    after: Optional[str] = None
    
    def has_any_filters(self) -> bool:
        """Check if any filters are applied"""
        return any([
            self.course_id, self.semester, self.author, 
            self.author_role, self.has_links, 
            self.has_attachments, self.before, self.after
        ])
    
    def to_dict(self) -> dict:
        """Convert to dictionary for **kwargs unpacking, excluding None values"""
        return {k: v for k, v in {
            'course_id': self.course_id,
            'semester': self.semester,
            'author': self.author,
            'author_role': self.author_role,
            'has_links': self.has_links,
            'has_attachments': self.has_attachments,
            'before': self.before,
            'after': self.after
        }.items() if v is not None}

class Helpers:
    @staticmethod
    def normalize_course_title(course_name: str) -> str:
        """Normalize course title to create a stable course slug"""
        import re
        normalized = re.sub(r'[^\w\s-]', '', course_name.lower())
        normalized = re.sub(r'[\s-]+', '_', normalized)
        words = [w for w in normalized.split('_')]
        return '_'.join(words)