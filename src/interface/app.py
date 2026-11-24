from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
from functools import wraps
from passlib.hash import sha256_crypt
from pymongo import MongoClient
from dotenv import load_dotenv
import secrets
import sys
import os
import time

load_dotenv()

# Add your pipeline to the path
current_dir = os.path.dirname(os.path.abspath(__file__))
pipeline_dir = os.path.dirname(current_dir)
sys.path.append(pipeline_dir)

# Import your GraphRAG system
from src.llm.query_graphrag import GraphRAG

app = Flask(__name__)
# Without FLASK_SECRET_KEY a random key is used, so logins are lost on restart
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

# MongoDB Configuration (see .env.example)
MONGO_USER = os.getenv("MONGO_INITDB_ROOT_USERNAME", "admin")
MONGO_PASS = os.getenv("MONGO_INITDB_ROOT_PASSWORD", "tutorai")
MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = os.getenv("MONGO_PORT", "27017")

# Try to connect to MongoDB (optional for now)
mongo_client = None
users_collection = None
chats_collection = None

try:
    CONNECTION_STRING = f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}:{MONGO_PORT}/admin"
    mongo_client = MongoClient(CONNECTION_STRING, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command('ping')  # Test connection
    print(f"✅ Connected to MongoDB at {MONGO_HOST}:{MONGO_PORT}")
    
    mongo_db = mongo_client['tutorai']
    users_collection = mongo_db['users']
    chats_collection = mongo_db['chats']
    
except Exception as e:
    print(f"⚠️ MongoDB connection failed: {e}")
    print("📝 Running without persistent chat storage")

def login_required(route_function):
    @wraps(route_function)
    def decorated_route(*args, **kwargs):
        if 'username' not in session:
            flash('Bitte erst einloggen', 'warning')
            return redirect('/login')
        return route_function(*args, **kwargs)
    return decorated_route

# Initialize GraphRAG system globally
graphrag = None

def init_graphrag():
    """Initialize the GraphRAG system"""
    global graphrag
    if graphrag is None:
        try:
            graphrag = GraphRAG()
            print("✅ GraphRAG system initialized")
            
        except Exception as e:
            print(f"❌ Error initializing GraphRAG: {e}")
            graphrag = None
    return graphrag

@app.route('/')
@login_required
def index():
    # Get chat history from MongoDB if available
    chat_history = []
    if chats_collection is not None:
        chat_doc = chats_collection.find_one({'username': session['username']})
        if chat_doc and 'chat' in chat_doc:
            # Convert MongoDB format to template format
            for entry in chat_doc['chat']:
                if isinstance(entry, dict):
                    chat_history.append({'type': 'user', 'message': entry.get('user', '')})
                    chat_history.append({'type': 'bot', 'message': entry.get('bot', '')})
    
    return render_template('chat.html', username=session.get('username', 'User'), chat=chat_history)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # If MongoDB is available, check against database
        if users_collection is not None:
            user = users_collection.find_one({'username': username})
            if user and sha256_crypt.verify(password, user['password']):
                session['username'] = user['username']
                flash('Login erfolgreich!', 'login_success')
                return redirect('/')
            else:
                flash('Ungültige Zugangsdaten, bitte erneut versuchen.', 'login_danger')
        else:
            # Simple fallback authentication when MongoDB is not available
            if username and password:
                session['username'] = username
                flash('Login erfolgreich (Demo-Modus)!', 'login_success')
                return redirect('/')
            else:
                flash('Bitte Benutzername und Passwort eingeben.', 'login_danger')

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if users_collection is not None:
            # MongoDB registration
            if users_collection.find_one({'username': username}):
                flash('Benutername existiert bereits, wähle einen anderen.', 'register_warning')
            else:
                hashed_password = sha256_crypt.hash(password)
                users_collection.insert_one({'username': username, 'password': hashed_password})
                chats_collection.insert_one({'username': username, 'chat': []})
                flash('Erfolgreich registriert! Bitte einloggen.', 'register_success')
                return redirect('/login')
        else:
            # Simple fallback registration when MongoDB is not available
            session['username'] = username
            flash('Erfolgreich registriert (Demo-Modus)!', 'register_success')
            return redirect('/')
            
    return render_template('register.html')

@app.route('/demo', methods=['POST'])
def demo():
    """Demo mode - no registration required"""
    session['username'] = 'Demo-User'
    flash('Demo-Modus aktiviert! Keine Registrierung erforderlich.', 'login_success')
    return redirect('/')

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.pop('username', None)
    
    if users_collection is not None:
        flash('Du wurdest ausgeloggt.', 'info')
    else:
        flash('Du wurdest ausgeloggt (Demo-Modus).', 'info')
    
    return redirect(url_for('login'))

@app.route('/chat', methods=['POST'])
@app.route('/send', methods=['POST'])  # Backward compatibility
@login_required
def chat():
    """Handle chat requests using GraphRAG with chat memory"""
    try:
        data = request.get_json()
        user_message = data.get('message', '')
        username = session['username']
        
        if not user_message:
            return jsonify({'error': 'No message provided'}), 400
        
        # Retrieve chat memory for context (if MongoDB available)
        chat_memory = []
        if chats_collection is not None:
            chat_doc = chats_collection.find_one({'username': username})
            chat_history = chat_doc['chat'] if chat_doc and 'chat' in chat_doc else []
            
            # Convert chat history to string format for the GraphRAG system
            for entry in chat_history:
                if isinstance(entry, dict):
                    chat_memory.append(f"User: {entry.get('user', '')}")
                    chat_memory.append(f"Bot: {entry.get('bot', '')}")
                else:
                    chat_memory.append(str(entry))
        
        # Initialize GraphRAG if needed
        rag_system = init_graphrag()
        if not rag_system:
            return jsonify({'error': 'GraphRAG system not available. Please check Neo4j connection.'}), 500
        
        # Query the GraphRAG system with chat memory context
        # TODO: add chat history
        results = rag_system.smart_query(user_message)
        
        # Extract the answer from the new simplified format
        response = results.get('answer', 'Entschuldigung, ich konnte keine Antwort finden.')
        
        # Store the conversation in MongoDB (if available)
        if chats_collection is not None:
            new_chat_entry = {
                "user": user_message,
                "bot": response,
                "timestamp": time.time()
            }
            chats_collection.update_one(
                {'username': username},
                {'$push': {'chat': new_chat_entry}},
                upsert=True  # Create document if it doesn't exist
            )
        
        # Also include source information if available
        sources = []
        context_results = results.get('context', [])
        if context_results:
            for item in context_results:  # Show all sources given to LLM
                # Check if this is a hybrid result
                if isinstance(item, dict) and 'type' in item:
                    # Hybrid result with type field
                    result_type = item.get('type')
                    vector_result = item.get('vector_result', {})  # For score
                    context = item.get('context', {})  # For all content fields
                    
                    if result_type == 'slide':
                        # Format slide source with moodle_url and embedded URLs
                        resource_name = context.get('resource_name', 'Unbekannt')
                        page_number = context.get('page_number', '?')
                        title = context.get('title', 'Ohne Titel')
                        moodle_url = context.get('moodle_url', '')
                        urls = context.get('urls', [])
                        change_id = context.get('change_id')  # Track if this slide has changes
                        
                        # Filter out empty URL entries (urls is a list of URL strings)
                        valid_urls = [url for url in urls if url] if isinstance(urls, list) else []
                        
                        sources.append({
                            'subject': f"Folie: {title} - {resource_name} (Seite {page_number})",
                            'score': vector_result.get('score', 0),
                            'author': '',  # Slides don't have authors
                            'course': context.get('course_name', 'Unbekannt'),
                            'url': moodle_url,  # Add moodle URL for clickable link
                            'urls': valid_urls,  # Include embedded URLs from slide content
                            'has_changes': change_id is not None,  # Flag for UI
                            'change_id': change_id
                        })
                    elif result_type == 'chunk':
                        # Format webtext chunk source with moodle_url
                        resource_name = context.get('resource_name', 'Unbekannt')
                        section_title = context.get('section_title', 'Ohne Titel')
                        chunk_index = context.get('chunk_index', 0)
                        moodle_url = context.get('moodle_url', '')
                        change_id = context.get('change_id')  # Track if this chunk has changes
                        
                        sources.append({
                            'subject': f"Webtext: {section_title} - {resource_name} (Teil {chunk_index + 1})",
                            'score': vector_result.get('score', 0),
                            'author': '',  # Webtext chunks don't have authors
                            'course': context.get('course_name', 'Unbekannt'),
                            'url': moodle_url,  # Add moodle URL for clickable link
                            'urls': [],  # Webtext chunks don't have embedded URLs like slides
                            'has_changes': change_id is not None,  # Flag for UI
                            'change_id': change_id
                        })
                    elif result_type == 'post':
                        # Format post source with permalink and embedded links
                        permalink = context.get('permalink', '')
                        links = context.get('links', [])
                        
                        # Filter out empty link entries
                        valid_links = [link for link in links if link.get('url') and link.get('link_id')]
                        
                        sources.append({
                            'subject': context.get('subject', 'Unbekannt'),
                            'score': vector_result.get('score', 0),
                            'author': context.get('author_name', 'Unbekannt'),
                            'course': context.get('course_name', 'Unbekannt'),
                            'url': permalink,  # Use permalink instead of thread URL
                            'links': valid_links  # Include embedded links
                        })
                else:
                    # Legacy result (direct post result)
                    sources.append({
                        'subject': item.get('subject', 'Unbekannt'),
                        'score': item.get('score', 0),
                        'author': item.get('author_name', 'Unbekannt'),
                        'course': item.get('course_name', 'Unbekannt'),
                        'url': ''  
                    })
        
        return jsonify({
            'response': response,
            'sources': sources,
            'query_type': results.get('query_type', 'UNKNOWN')
        })
        
    except Exception as e:
        print(f"❌ Error in chat endpoint: {e}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@app.route('/clear-chat', methods=['POST'])
@login_required
def clear_chat():
    """Clear chat history for the current user"""
    try:
        username = session['username']
        
        # Clear chat history in MongoDB (if available)
        if chats_collection is not None:
            chats_collection.update_one(
                {'username': username},
                {'$set': {'chat': []}},
                upsert=True
            )
            return jsonify({'message': 'Chat history cleared successfully'})
        else:
            return jsonify({'message': 'Chat history cleared (demo mode - no persistent storage)'})
            
    except Exception as e:
        print(f"❌ Error clearing chat: {e}")
        return jsonify({'error': f'Failed to clear chat: {str(e)}'}), 500

@app.route('/get-diff/<change_id>', methods=['GET'])
@login_required
def get_diff(change_id):
    """Serve the diff HTML for a slide comparison"""
    try:
        rag_system = init_graphrag()
        if not rag_system:
            return jsonify({'error': 'GraphRAG system not available'}), 500
        
        # Get the diff HTML path from the graph retrieval system
        from pathlib import Path
        diff_path = rag_system.retrieval.get_diff_html_path(change_id)
        
        if not diff_path:
            return jsonify({'error': 'Diff path not found in database'}), 404
        
        # Construct full path relative to project root
        project_root = Path(__file__).parent.parent.parent
        full_path = project_root / diff_path
        
        if not full_path.exists():
            return jsonify({'error': f'Diff HTML file not found: {diff_path}'}), 404
        
        # Read and return the HTML content
        with open(full_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        return html_content, 200, {'Content-Type': 'text/html; charset=utf-8'}
        
    except Exception as e:
        print(f"❌ Error fetching diff HTML: {e}")
        return jsonify({'error': f'Failed to fetch diff: {str(e)}'}), 500

@app.route('/landing')
def landing():
    return render_template('landing.html')

if __name__ == '__main__':
    print("🚀 Starting TutorAI web interface...")
    print("📊 Initializing GraphRAG system...")
    init_graphrag()
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", host='0.0.0.0', port=5001)