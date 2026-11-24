from .start import Neo4jConnection 
import json
from pathlib import Path
import re
from typing import Dict
from datetime import datetime
from ..utils import Helpers

class ForumDataLoader:
    def __init__(self, neo_connection: Neo4jConnection, embedder=None):
        self.neo = neo_connection
        self.db = neo_connection.db
        self.embedder = embedder
        if self.embedder:
            self.create_vector_index()
    
    def _create_constraints(self):
        """Create unique constraints for better performance"""
        constraints = [
            "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.user_id IS UNIQUE",
            "CREATE CONSTRAINT post_id IF NOT EXISTS FOR (p:Post) REQUIRE p.post_id IS UNIQUE",
            "CREATE CONSTRAINT course_id IF NOT EXISTS FOR (c:Course) REQUIRE c.course_id IS UNIQUE",
            "CREATE CONSTRAINT semester_course_id IF NOT EXISTS FOR (s:Semester) REQUIRE s.course_id IS UNIQUE",
            "CREATE CONSTRAINT forum_id IF NOT EXISTS FOR (f:Forum) REQUIRE f.forum_id IS UNIQUE",
            "CREATE CONSTRAINT thread_id IF NOT EXISTS FOR (t:Thread) REQUIRE t.thread_id IS UNIQUE",
            "CREATE CONSTRAINT link_id IF NOT EXISTS FOR (l:Link) REQUIRE l.link_id IS UNIQUE"
        ]
        
        for constraint in constraints:
            try:
                self.db.run(constraint)
            except Exception:
                pass # Constraint already exist
        
        print("✅ Database constraints created")
    
    def _create_indexes(self):
        """Create indexes for better search performance"""
        indexes = [
            "CREATE INDEX post_content IF NOT EXISTS FOR (p:Post) ON (p.content)",
            "CREATE INDEX user_name IF NOT EXISTS FOR (u:User) ON (u.name)",
            "CREATE INDEX post_subject IF NOT EXISTS FOR (p:Post) ON (p.subject)",
            "CREATE INDEX thread_title IF NOT EXISTS FOR (t:Thread) ON (t.title)",
            "CREATE INDEX forum_name IF NOT EXISTS FOR (f:Forum) ON (f.name)",
            "CREATE INDEX semester_label IF NOT EXISTS FOR (s:Semester) ON (s.semester_label)"
        ]
        
        for index in indexes:
            try:
                self.db.run(index)
            except Exception:
                pass # Index already exist
        
        print("✅ Database indexes created")

    def create_vector_index(self):
        """Create vector index for Post embeddings"""
        self.db.run("""
            CREATE VECTOR INDEX post_embedding IF NOT EXISTS
            FOR (p:Post) ON (p.embedding)
            OPTIONS {
                indexConfig: {
                    `vector.dimensions`: $dims,
                    `vector.similarity_function`: 'cosine'
                }
            }
        """, dims=self.embedder.dims)
        print(f"✅ Post vector index (dims={self.embedder.dims}) ready")
    
    def _extract_datetime_from_filename(self, filename: str) -> datetime:
        """Extract datetime from filename pattern: __2025-08-16T20-58+00-00.json"""
        # Pattern: __YYYY-MM-DDTHH-MM+TZ-TZ.json
        match = re.search(r'__(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}\+\d{2}-\d{2})\.json$', filename)
        if match:
            datetime_str = match.group(1)
            
            # Convert: 2025-08-16T20-58+00-00 -> 2025-08-16T20:58+00:00 (UTC)
            if 'T' in datetime_str:
                date_part, time_tz_part = datetime_str.split('T', 1)
                time_tz_part = time_tz_part.replace('-', ':', 1)  # Fix hour:minute
                time_tz_part = time_tz_part.replace('-', ':', 1)  # Fix timezone
                
                fixed_datetime_str = f"{date_part}T{time_tz_part}"
                
                try:
                    dt = datetime.fromisoformat(fixed_datetime_str)
                    # Note: This is UTC time (+00:00)
                    return dt
                except ValueError as e:
                    print(f"⚠️ Warning: Could not parse datetime '{fixed_datetime_str}': {e}")
                    return datetime.min
        
        return datetime.min
    
    def _extract_forum_name_from_filename(self, forum_file_path: str) -> str:
        """Extract forum name from filename"""
        filename = Path(forum_file_path).name
        match = re.search(r'forum_\d+_([^_]+(?:_[^_]+)*)_\d+__', filename)
        if match:
            return match.group(1)
        
        return "unknown_forum"
    
    def _extract_forum_id_from_filename(self, forum_file_path: str) -> str:
        """Extract forum_id directly from filename"""
        filename = Path(forum_file_path).name
        match = re.search(r'forum_\d+_([^_]+(?:_[^_]+)*)_(\d+)__', filename)
        if match:
            return match.group(2)  # Returns the forum_id
        
        print(f"⚠️ Warning: Could not extract forum_id from filename: {filename}")
        return "unknown_forum"
    

    
    def _get_latest_forum_files(self, forum_folder: Path) -> Dict[str, Path]:
        """Get the latest file for each forum type (summary, forum_01, forum_02, etc.)"""
        all_files = list(forum_folder.glob("*.json"))
        
        # Group files by their base pattern
        file_groups = {}
        
        for file_path in all_files:
            filename = file_path.name
            match = re.match(r'(\d+_forum_\d+_[^_]+(?:_[^_]+)*_\d+)', filename)
            if match:
                base_key = match.group(1)
            else:
                continue

            # Add to group with datetime for sorting
            if base_key not in file_groups:
                file_groups[base_key] = []
            
            file_datetime = self._extract_datetime_from_filename(filename)
            file_groups[base_key].append((file_datetime, file_path))
        
        # Get the latest file from each group
        latest_files = {}
        for base_key, file_list in file_groups.items():
            latest_file = sorted(file_list, key=lambda x: x[0], reverse=True)[0][1]
            latest_files[base_key] = latest_file

        return latest_files

    def _create_post_and_relationships(self, post_data: Dict, thread_id: str, forum_id: str):
        """Create post node and all its relationships"""
        metadata = post_data['metadata']
        # Create Course node (normalized title)
        course = metadata['course']
        course_slug = Helpers.normalize_course_title(course['name'])
        
        self.db.run("""
            MERGE (c:Course {course_slug: $course_slug})
            SET c.course_name = $course_name
        """, {
            'course_slug': course_slug,
            'course_name': course['name']
        })
        
        # Create Semester node
        self.db.run("""
            MERGE (s:Semester {course_id: $course_id})
            SET s.semester_label = $semester_label,
                s.course_name = $course_name
        """, {
            'course_id': course['id'],
            'semester_label': course['semester'],
            'course_name': course['name']
        })
        
        # Link Course → Semester
        self.db.run("""
            MATCH (c:Course {course_slug: $course_slug})
            MATCH (s:Semester {course_id: $course_id})
            MERGE (c)-[:HAS_SEMESTER]->(s)
        """, {
            'course_slug': course_slug,
            'course_id': course['id']
        })
        
        # Create User node
        self.db.run("""
            MERGE (u:User {user_id: $user_id})
            SET u.name = $name,
                u.role = $role
        """, {
            'user_id': metadata['author_user_id'],
            'name': metadata['author'],
            'role': metadata['author_role']
        })
        
        # Create Post node with all metadata
        self.db.run("""
            MERGE (p:Post {post_id: $post_id})
            SET p.content = $content,
                p.subject = $subject,
                p.datetime = $datetime,
                p.permalink = $permalink,
                p.is_reply = $is_reply,
                p.is_thread_root = $is_thread_root,
                p.has_attachments = $has_attachments,
                p.chunk_type = $chunk_type
        """, {
            'post_id': metadata['post_id'],
            'content': post_data['content'],
            'subject': metadata['subject'],
            'datetime': metadata['post_datetime'],
            'permalink': metadata['permalink'],
            'is_reply': metadata['is_reply'],
            'is_thread_root': metadata['is_thread_root'],
            'has_attachments': metadata['has_attachments'],
            'chunk_type': post_data['chunk_type']
        })
        
        # Create relationships
        # User authored Post
        self.db.run("""
            MATCH (u:User {user_id: $user_id})
            MATCH (p:Post {post_id: $post_id})
            MERGE (u)-[:AUTHORED]->(p)
        """, {
            'user_id': metadata['author_user_id'],
            'post_id': metadata['post_id']
        })
        
        # Post belongs to Thread
        self.db.run("""
            MATCH (t:Thread {thread_id: $thread_id})
            MATCH (p:Post {post_id: $post_id})
            MERGE (p)-[:BELONGS_TO_THREAD]->(t)
        """, {
            'thread_id': thread_id,
            'post_id': metadata['post_id']
        })
        
        # Semester has Forum
        self.db.run("""
            MATCH (s:Semester {course_id: $course_id})
            MATCH (f:Forum {forum_id: $forum_id})
            MERGE (s)-[:HAS_FORUM]->(f)
        """, {
            'course_id': metadata['course']['id'],  # Link via ISIS course ID (which in reality is Course Semester ID)
            'forum_id': forum_id
        })
        
        # Reply relationships
        if metadata['response_to'] and metadata['is_reply']:
            self.db.run("""
                MATCH (original:Post {post_id: $original_id})
                MATCH (reply:Post {post_id: $reply_id})
                MERGE (reply)-[:REPLIES_TO]->(original)
            """, {
                'original_id': metadata['response_to'],
                'reply_id': metadata['post_id']
            })
        
        # Handle links as separate entities
        if metadata.get('links'):
            for i, link in enumerate(metadata['links']):
                link_id = f"{metadata['post_id']}_link_{i}"
                
                # Create Link node
                self.db.run("""
                    MERGE (l:Link {link_id: $link_id})
                    SET l.url = $url,
                        l.text = $text,
                        l.post_id = $post_id
                """, {
                    'link_id': link_id,
                    'url': link['url'],
                    'text': link['text'],
                    'post_id': metadata['post_id']
                })
                
                # Create relationship Post -> Link
                self.db.run("""
                    MATCH (p:Post {post_id: $post_id})
                    MATCH (l:Link {link_id: $link_id})
                    MERGE (p)-[:HAS_LINK]->(l)
                """, {
                    'post_id': metadata['post_id'],
                    'link_id': link_id
                })

    def _process_single_forum_file(self, forum_data_file: str):
        with open(forum_data_file, 'r', encoding='utf-8') as f:
            threads_data = json.load(f)
        
        if not threads_data or not threads_data[0].get('posts'):
            print(f"⚠️ No data found in {forum_data_file}")
            return
        
        forum_id = self._extract_forum_id_from_filename(forum_data_file)
        first_post_meta = threads_data[0]['posts'][0] # Get course_id from post metadata
        course_id = first_post_meta['metadata']['course']['id']
        
        print(f"📖 Loading {Path(forum_data_file).name}")
        print(f"    Course ID: {course_id} (from post metadata)")
        print(f"    Forum ID: {forum_id} (directly from filename)")
        
        # Create Forum node
        forum_name = self._extract_forum_name_from_filename(forum_data_file)
        self.db.run("""
            MERGE (f:Forum {forum_id: $forum_id})
            SET f.name = $forum_name
        """, {
            'forum_id': forum_id,
            'forum_name': forum_name
        })

        for thread_data in threads_data:
            if not thread_data.get('posts'):
                continue
                
            # Get thread info from first post
            first_post = thread_data['posts'][0]
            thread_id = first_post['metadata']['thread_id']
            thread_title = thread_data['thread_title']
            thread_url = thread_data['thread_url']
            
            # Create Thread node
            self.db.run("""
                MERGE (t:Thread {thread_id: $thread_id})
                SET t.title = $title,
                    t.url = $url,
                    t.post_count = $post_count
            """, {
                'thread_id': thread_id,
                'title': thread_title,
                'url': thread_url,
                'post_count': len(thread_data['posts'])
            })
            
            # Link Forum to Thread
            self.db.run("""
                MATCH (f:Forum {forum_id: $forum_id})
                MATCH (t:Thread {thread_id: $thread_id})
                MERGE (f)-[:CONTAINS_THREAD]->(t)
            """, {'forum_id': forum_id, 'thread_id': thread_id})
            
            # Create Course + Semester + User + Post + Link nodes with the _create_post_and_relationships function
            for post in thread_data['posts']:
                self._create_post_and_relationships(post, thread_id, forum_id)
        
        print(f"✅ Loaded {len(threads_data)} threads from {Path(forum_data_file).name}")
    
    
    def load_forums_from_course(self, course_forums_folder: str):
        """Load all forum data for a course using your existing structure - LATEST FILES ONLY"""
        forum_folder = Path(course_forums_folder)
        
        # Get only the latest files for each forum type
        print(f"🔍 Scanning for latest forum files in {course_forums_folder}")
        latest_files = self._get_latest_forum_files(forum_folder)
        
        if not latest_files:
            print("⚠️ No forum files found!")
            return

        print(f"📂 Loading {len(latest_files)} latest forum data files")

        for file_type, forum_file in latest_files.items():
            print(f"📖 Processing latest {file_type}: {forum_file.name}")
            self._process_single_forum_file(str(forum_file))
        
        print(f"✅ Completed loading latest forums from {course_forums_folder}")

    def generate_embeddings_forum_posts(self, batch_size: int = 100):
        """Generate embeddings for all posts that don't have them (idempotent)"""
        if not self.embedder:
            print("⚠️ No embedder provided! Cannot generate embeddings.")
            return
        
        total_result = self.db.run("""
            MATCH (p:Post) 
            WHERE p.embedding IS NULL 
            RETURN count(p) as total
        """)
        total_posts = total_result.single()['total']
        
        if total_posts == 0:
            print("✅ All posts already have embeddings!")
            return
        
        print(f"🔢 Generating embeddings for {total_posts} posts...")
        
        processed = 0
        batch_count = 0
        
        while processed < total_posts:
            batch_count += 1
            print(f"   🔄 Batch {batch_count}: Processing posts {processed + 1}-{min(processed + batch_size, total_posts)}...")
            
            # Get batch of posts without embeddings
            result = self.db.run("""
                MATCH (p:Post)
                WHERE p.embedding IS NULL
                RETURN p.post_id as post_id, 
                       p.subject as subject, 
                       p.content as content
                LIMIT $batch_size
            """, batch_size=batch_size)
            
            batch_posts = list(result)
            
            if not batch_posts:
                break
            
            # Prepare texts for embedding (combine subject + content)
            texts = []
            post_ids = []
            
            for post in batch_posts:
                combined_text = f"{post['subject']}\n\n{post['content']}"
                texts.append(combined_text)
                post_ids.append(post['post_id'])
            
            # Generate embeddings for the batch
            embeddings = self.embedder.embed_list(texts)
            
            # Store embeddings back to Neo4j
            for post_id, embedding in zip(post_ids, embeddings, strict=False):
                self.db.run("""
                    MATCH (p:Post {post_id: $post_id})
                    SET p.embedding = $embedding
                """, 
                    post_id=post_id,
                    embedding=embedding.tolist()
                )
            
            processed += len(batch_posts)
            progress = (processed / total_posts) * 100
            print(f"   ✅ Batch {batch_count} complete! Progress: {processed}/{total_posts} ({progress:.1f}%)")
        
        print(f"🎉 All {total_posts} posts now have embeddings!")


# Demo/test function
def demo_load_forum_data(course_forums_folder: str, reset: bool = False):
    """Load the latest forum files of one course into Neo4j"""
    neo = Neo4jConnection()
    forum = ForumDataLoader(neo)
    try:
        # Setup database. Loading uses MERGE, so re-running without --reset is safe.
        if reset:
            neo.clear_database()
        forum._create_constraints()
        forum._create_indexes()
        
        # Create embedder and loader with embedder
        from .embedder import TextEmbedder
        embedder = TextEmbedder(neo)
        loader = ForumDataLoader(neo, embedder=embedder)
        
        # Load forum data from your structure - LATEST ONLY
        loader.load_forums_from_course(course_forums_folder)
        
        # Generate embeddings for all posts
        print("\n🔢 Generating embeddings for posts...")
        loader.generate_embeddings_forum_posts(batch_size=50)
        
        # Show stats
        neo.print_database_stats()
        
        # Test query
        print("\n🔍 Sample forum hierarchy query:")
        result = neo.db.run("""
            MATCH (c:Course)-[:HAS_SEMESTER]->(s:Semester)-[:HAS_FORUM]->(f:Forum)-[:CONTAINS_THREAD]->(t:Thread)<-[:BELONGS_TO_THREAD]-(p:Post)
            WHERE p.is_thread_root = true
            RETURN c.course_name as course, 
                   c.course_slug as course_slug,
                   s.course_id as semester_id,
                   s.semester_label as semester,
                   f.name as forum, 
                   f.forum_id as forum_id, 
                   t.title as thread, 
                   p.subject as subject
            LIMIT 5
        """)
        
        for record in result:
            print(f"   Course: {record['course']} ({record['course_slug']})")
            print(f"   Semester: {record['semester']} (ID: {record['semester_id']})")
            print(f"   Forum: {record['forum']} (ID: {record['forum_id']})")
            print(f"   Thread: {record['thread']}")
    
    finally:
        neo.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Load the forums of one crawled course into Neo4j")
    parser.add_argument("forums_folder", help="Forum folder of a crawled course, e.g. data/course_44009/forums")
    parser.add_argument("--reset", action="store_true",
                        help="Delete the WHOLE graph (slides, web text, quizzes too) before loading")
    args = parser.parse_args()
    demo_load_forum_data(args.forums_folder, reset=args.reset)