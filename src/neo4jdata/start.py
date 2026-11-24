import os

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()


class Neo4jConnection:
    def __init__(self, uri=None, user=None, password=None):
        # Fall back to NEO4J_* environment variables (see .env.example)
        uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = user or os.getenv("NEO4J_USER", "neo4j")
        password = password or os.getenv("NEO4J_PASSWORD", "password")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.db = self.driver.session()
        print(f"Connected to Neo4j at {uri}")
    
    def close(self):
        self.db.close()
        self.driver.close()
        print("Neo4j connection closed")
    
    def clear_database(self):
        """Clear all nodes and relationships"""
        self.db.run("MATCH (n) DETACH DELETE n")
        print("Database cleared")
    
    def get_database_stats(self):
        stats = {}        
        # Count nodes
        stats['courses'] = self.db.run("MATCH (c:Course) RETURN count(c) as count").single()['count']
        stats['semesters'] = self.db.run("MATCH (s:Semester) RETURN count(s) as count").single()['count']
        stats['forums'] = self.db.run("MATCH (f:Forum) RETURN count(f) as count").single()['count']
        stats['threads'] = self.db.run("MATCH (t:Thread) RETURN count(t) as count").single()['count']
        stats['posts'] = self.db.run("MATCH (p:Post) RETURN count(p) as count").single()['count']
        stats['slideunits'] = self.db.run("MATCH (su:SlideUnit) RETURN count(su) as count").single()['count']

        return stats
    
    def print_database_stats(self):
        stats = self.get_database_stats()
        print("Database Statistics:")
        print(f"Courses: {stats['courses']}")
        print(f"Semesters: {stats['semesters']}")
        print(f"Forums: {stats['forums']}")  
        print(f"Threads: {stats['threads']}")
        print(f"Posts: {stats['posts']}")
        print(f"SlideUnits: {stats['slideunits']}")


def demo_connection():
    neo = Neo4jConnection()
    
    try:
        neo.print_database_stats()
    finally:
        neo.close()


if __name__ == "__main__":
    demo_connection()