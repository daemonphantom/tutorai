from src.neo4jdata.start import Neo4jConnection
neo = Neo4jConnection()

def reset_database(self):
        """Complete database reset: clear data, drop constraints and indexes"""
        # Clear all data
        self.db.run("MATCH (n) DETACH DELETE n")
        print("Cleared all nodes and relationships")
        
        # Drop all constraints
        try:
            result = self.db.run("SHOW CONSTRAINTS")
            for record in result:
                constraint_name = record['name']
                try:
                    self.db.run(f"DROP CONSTRAINT {constraint_name}")
                    print(f"   Dropped constraint: {constraint_name}")
                except Exception as e:
                    print(f"Could not drop constraint {constraint_name}: {e}")
        except Exception as e:
            print(f"Error listing constraints: {e}")
        
        # Drop all indexes
        try:
            result = self.db.run("SHOW INDEXES")
            for record in result:
                index_name = record['name']
                # Skip built-in lookup indexes
                if not index_name.startswith('__'):
                    try:
                        self.db.run(f"DROP INDEX {index_name}")
                        print(f"   Dropped index: {index_name}")
                    except Exception as e:
                        print(f"Could not drop index {index_name}: {e}")
        except Exception as e:
            print(f"Error listing indexes: {e}")
        
        print("Database completely reset")
    
reset_database(neo)
neo.close()