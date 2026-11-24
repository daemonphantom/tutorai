// MongoDB initialization script
// This creates the tutorai database and sets up initial collections

db = db.getSiblingDB('tutorai');

// Create users collection with indexes
db.createCollection('users');
db.users.createIndex({ "username": 1 }, { unique: true });

// Create chats collection with indexes  
db.createCollection('chats');
db.chats.createIndex({ "username": 1 }, { unique: true });

// Create ratings collection
db.createCollection('ratings');
db.ratings.createIndex({ "username": 1 });

print('✅ TutorAI database initialized with collections: users, chats, ratings');
