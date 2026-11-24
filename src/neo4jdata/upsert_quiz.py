"""
Quiz Question Version Upserter for Neo4j Knowledge Graph

Manages the upload and tracking of quiz questions as QuizQuestionVersions with
Change nodes between versions and causal links to forum posts.

Graph Structure:
    Course -> Semester -> Resource (type: quiz) -> QuizQuestionVersion
    
Change Tracking:
    Change nodes connect QuizQuestionVersions across versions:
    - [:FROM_QUESTION] -> QuizQuestionVersion (old)
    - [:TO_QUESTION] -> QuizQuestionVersion (new)
    
Causal Links:
    - Post [:TRIGGERED_CHANGE] -> QuizQuestionVersion (new version)
    - QuizQuestionVersion (old) [:REPLACED_BY] -> QuizQuestionVersion (new)
    
Change Types:
    - unchanged: Exact match (shouldn't happen with our strict comparison)
    - modified: Changed content (1→1)
    - added: New question (0→1)
    - removed: Deleted question (1→0)
"""

import sys
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional

sys.path.append(str(Path(__file__).parent.parent))

from src.neo4jdata.start import Neo4jConnection


class QuizLoader:
    """Handles uploading Quiz QuestionVersions with change tracking to Neo4j"""
    
    def __init__(self, neo_connection: Neo4jConnection):
        self.neo = neo_connection
        self.db = neo_connection.db
        self._create_constraints()
        self._create_indexes()
    
    def _create_constraints(self):
        """Create unique constraints for quiz nodes"""
        constraints = [
            "CREATE CONSTRAINT quizquestionversion_id IF NOT EXISTS FOR (q:QuizQuestionVersion) REQUIRE q.questionversion_id IS UNIQUE",
            "CREATE CONSTRAINT quiz_change_id IF NOT EXISTS FOR (c:QuizChange) REQUIRE c.change_id IS UNIQUE",
        ]
        
        for constraint in constraints:
            try:
                self.db.run(constraint)
            except Exception:
                pass  # Constraint might already exist
        
        print("✅ Quiz constraints created")
    
    def _create_indexes(self):
        """Create indexes for better search performance"""
        indexes = [
            "CREATE INDEX quizquestion_name IF NOT EXISTS FOR (q:QuizQuestionVersion) ON (q.question_name)",
            "CREATE INDEX quizquestion_type IF NOT EXISTS FOR (q:QuizQuestionVersion) ON (q.question_type)",
            "CREATE INDEX quizchange_type IF NOT EXISTS FOR (c:QuizChange) ON (c.change_type)"
        ]
        
        for index in indexes:
            try:
                self.db.run(index)
            except Exception:
                pass
        
        print("✅ Quiz indexes created")
    
    def _create_or_update_resource(self, resource_name: str, resource_id: str,
                                  course_slug: str) -> str:
        """
        Create or update Resource node (quiz).
        
        Args:
            resource_name: Clean name for the quiz resource
            resource_id: Unique identifier for this quiz
            course_slug: Course slug for linking
        """
        self.db.run("""
            MERGE (r:Resource {resource_id: $resource_id})
            SET r.resource_type = 'quiz',
                r.resource_name = $resource_name,
                r.course_slug = $course_slug
        """, {
            'resource_id': resource_id,
            'resource_name': resource_name,
            'course_slug': course_slug
        })
        
        return resource_id
    
    def _create_quizquestionversion(self, question_data: Dict[str, Any], 
                                   resource_id: str,
                                   version_label: str) -> str:
        """
        Create QuizQuestionVersion node.
        
        Args:
            question_data: Question dictionary from parse_quiz.py
            resource_id: ID of the parent Resource
            version_label: Version label (e.g., "WiSe 2024/25")
        
        Returns:
            questionversion_id
        """
        
        # Generate questionversion_id from question_id + version_label
        questionversion_id = hashlib.md5(
            f"{question_data['question_id']}:{version_label}".encode()
        ).hexdigest()
        
        # Create QuizQuestionVersion node
        self.db.run("""
            MERGE (q:QuizQuestionVersion {questionversion_id: $questionversion_id})
            SET q.question_id = $question_id,
                q.question_name = $question_name,
                q.question_text = $question_text,
                q.question_type = $question_type,
                q.answers = $answers,
                q.version_label = $version_label,
                q.source_file = $source_file,
                q.created_at = datetime()
        """, {
            'questionversion_id': questionversion_id,
            'question_id': question_data['question_id'],
            'question_name': question_data['question_name'],
            'question_text': question_data['question_text'],
            'question_type': question_data['question_type'],
            'answers': question_data.get('answers', []),
            'version_label': version_label,
            'source_file': question_data.get('metadata', {}).get('source_file', '')
        })
        
        # Link Resource -> QuizQuestionVersion
        self.db.run("""
            MATCH (r:Resource {resource_id: $resource_id})
            MATCH (q:QuizQuestionVersion {questionversion_id: $questionversion_id})
            MERGE (r)-[:HAS_QUESTION]->(q)
        """, {
            'resource_id': resource_id,
            'questionversion_id': questionversion_id
        })
        
        return questionversion_id
    
    def _create_change(self, change_data: Dict[str, Any],
                      old_questionversion_id: Optional[str],
                      new_questionversion_id: Optional[str]):
        """
        Create QuizChange node tracking differences between two QuizQuestionVersions.
        Links Change nodes to QuizQuestionVersions with FROM_QUESTION and TO_QUESTION.
        """
        
        change_type = change_data['change_type']
        
        # Generate change_id
        if old_questionversion_id and new_questionversion_id:
            change_id = hashlib.md5(
                f"{old_questionversion_id}:{new_questionversion_id}".encode()
            ).hexdigest()
        elif new_questionversion_id:
            change_id = hashlib.md5(f"added:{new_questionversion_id}".encode()).hexdigest()
        elif old_questionversion_id:
            change_id = hashlib.md5(f"removed:{old_questionversion_id}".encode()).hexdigest()
        else:
            return  # Skip if no IDs
        
        # Create QuizChange node
        self.db.run("""
            MERGE (c:QuizChange {change_id: $change_id})
            SET c.change_type = $change_type,
                c.similarity = $similarity,
                c.text_diff = $text_diff,
                c.old_text = $old_text,
                c.new_text = $new_text,
                c.created_at = datetime()
        """, {
            'change_id': change_id,
            'change_type': change_type,
            'similarity': change_data.get('similarity', 0.0),
            'text_diff': change_data.get('text_diff', ''),
            'old_text': change_data.get('old_text', ''),
            'new_text': change_data.get('new_text', '')
        })
        
        # Link FROM_QUESTION (old)
        if old_questionversion_id:
            self.db.run("""
                MATCH (c:QuizChange {change_id: $change_id})
                MATCH (q:QuizQuestionVersion {questionversion_id: $questionversion_id})
                MERGE (c)-[:FROM_QUESTION]->(q)
            """, {
                'change_id': change_id,
                'questionversion_id': old_questionversion_id
            })
        
        # Link TO_QUESTION (new)
        if new_questionversion_id:
            self.db.run("""
                MATCH (c:QuizChange {change_id: $change_id})
                MATCH (q:QuizQuestionVersion {questionversion_id: $questionversion_id})
                MERGE (c)-[:TO_QUESTION]->(q)
            """, {
                'change_id': change_id,
                'questionversion_id': new_questionversion_id
            })
        
        # Create REPLACED_BY edge (old -> new)
        if old_questionversion_id and new_questionversion_id:
            self.db.run("""
                MATCH (old:QuizQuestionVersion {questionversion_id: $old_id})
                MATCH (new:QuizQuestionVersion {questionversion_id: $new_id})
                MERGE (old)-[:REPLACED_BY]->(new)
            """, {
                'old_id': old_questionversion_id,
                'new_id': new_questionversion_id
            })
    
    def link_post_to_question(self, post_id: str, questionversion_id: str):
        """
        Create TRIGGERED_CHANGE edge from Post to QuizQuestionVersion.
        
        Args:
            post_id: Forum post ID
            questionversion_id: Question version ID that was triggered by the post
        """
        result = self.db.run("""
            MATCH (p:Post {post_id: $post_id})
            MATCH (q:QuizQuestionVersion {questionversion_id: $questionversion_id})
            MERGE (p)-[:TRIGGERED_CHANGE]->(q)
            RETURN p.post_id as post_id, q.question_name as question_name
        """, {
            'post_id': post_id,
            'questionversion_id': questionversion_id
        })
        
        record = result.single()
        if record:
            print(f"✅ Linked Post {record['post_id']} -> Question '{record['question_name']}'")
        else:
            print(f"⚠️ Could not link Post {post_id} -> Question {questionversion_id}")
    
    def upload_quiz_with_changes(
        self,
        old_questions: List[Dict[str, Any]],
        new_questions: List[Dict[str, Any]],
        changes_data: Dict[str, Any],
        resource_name: str,
        resource_id: str,
        course_slug: str,
        old_version_label: str,
        new_version_label: str,
        trigger_post_id: Optional[str] = None
    ):
        """
        Upload two quiz versions with their changes.
        
        Args:
            old_questions: List of questions from old version (from parse_quiz.py)
            new_questions: List of questions from new version
            changes_data: Changes from compare_quiz.py
            resource_name: Name of the quiz resource
            resource_id: Unique ID for the quiz resource
            course_slug: Course slug
            old_version_label: Version label for old (e.g., "WiSe 2024/25")
            new_version_label: Version label for new (e.g., "WiSe 2025/26")
            trigger_post_id: Optional forum post ID that triggered the change
        """
        
        print(f"\n📊 Uploading quiz: {resource_name}")
        print(f"   Old version: {old_version_label} ({len(old_questions)} questions)")
        print(f"   New version: {new_version_label} ({len(new_questions)} questions)")
        
        # Create Resource
        self._create_or_update_resource(resource_name, resource_id, course_slug)
        
        # Upload old version questions
        old_question_map = {}
        for q in old_questions:
            qv_id = self._create_quizquestionversion(q, resource_id, old_version_label)
            old_question_map[q['question_id']] = qv_id
        
        # Upload new version questions
        new_question_map = {}
        for q in new_questions:
            qv_id = self._create_quizquestionversion(q, resource_id, new_version_label)
            new_question_map[q['question_id']] = qv_id
        
        # Upload changes
        print(f"\n📝 Creating {len(changes_data['detailed_changes'])} Change nodes...")
        for change in changes_data['detailed_changes']:
            old_qv_id = old_question_map.get(change.get('old_question_id'))
            new_qv_id = new_question_map.get(change.get('new_question_id'))
            
            self._create_change(change, old_qv_id, new_qv_id)
        
        # Link trigger post if provided
        if trigger_post_id and new_questions:
            # Link to the modified question in new version
            for change in changes_data['detailed_changes']:
                if change['change_type'] == 'modified':
                    new_qv_id = new_question_map.get(change.get('new_question_id'))
                    if new_qv_id:
                        self.link_post_to_question(trigger_post_id, new_qv_id)
                        break
        
        print("\n✅ Quiz upload complete!")
        print(f"   Summary: {changes_data['summary']}")


def demo_upload_quiz():
    """Demo: Upload the Fachsemester quiz with causal link"""
    from capture.parsing.comparer_quiz.parse_quiz import parse_quiz_xml
    import json
    
    neo = Neo4jConnection()
    loader = QuizLoader(neo)
    
    try:
        # Paths
        quiz_dir = Path(__file__).resolve().parents[2] / "data" / "comparison" / "quiz"
        old_file = quiz_dir / "quiz-[WiSe 202526] Praktika Meta-Fachsemester-20251028-1435-4.xml"
        new_file = quiz_dir / "quiz-[WiSe 202526] Praktika Meta-Fachsemester-20251028-1435-5.xml"
        changes_file = quiz_dir / "quiz_changes.json"
        
        # Parse questions
        old_questions = parse_quiz_xml(old_file)
        new_questions = parse_quiz_xml(new_file)
        
        # Load changes
        with open(changes_file) as f:
            changes_data = json.load(f)
        
        # Hardcoded version labels (extracted from actual question content)
        old_version = "WiSe 24/25"  # From file -4.xml
        new_version = "WiSe 25/26"  # From file -5.xml
        
        # Upload with causal link
        loader.upload_quiz_with_changes(
            old_questions=old_questions,
            new_questions=new_questions,
            changes_data=changes_data,
            resource_name="Praktika Meta - Fachsemester",
            resource_id="praktika_meta_fachsemester",
            course_slug="meta-praktikum",
            old_version_label=old_version,
            new_version_label=new_version,
            trigger_post_id="1196190"  # Post that reported the error
        )
        
        # Verify with Cypher
        print("\n🔍 Verification Query:")
        result = neo.db.run("""
            MATCH (p:Post {post_id: '1196190'})-[:TRIGGERED_CHANGE]->(q:QuizQuestionVersion)
            MATCH (old:QuizQuestionVersion)-[:REPLACED_BY]->(q)
            RETURN 
                p.author_name as author,
                old.question_text as old_text,
                q.question_text as new_text,
                q.question_name as question_name
        """)
        
        for rec in result:
            print("\n✅ Causal Chain Found:")
            print(f"   Author: {rec['author']}")
            print(f"   Question: {rec['question_name']}")
            print(f"   Old: {rec['old_text'][:80]}...")
            print(f"   New: {rec['new_text'][:80]}...")
        
    finally:
        neo.close()


if __name__ == "__main__":
    demo_upload_quiz()
