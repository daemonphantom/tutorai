// Course data (forum titles, author names, links) and LLM answers are untrusted:
// escape plain text, sanitize the bot's HTML answer, and only link to http(s) URLs.
function escapeHtml(text) {
    return String(text ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function safeUrl(url) {
    try {
        const parsed = new URL(url, window.location.origin);
        return ['http:', 'https:'].includes(parsed.protocol) ? escapeHtml(parsed.href) : '';
    } catch (e) {
        return '';
    }
}

function sanitizeBotHtml(html) {
    return DOMPurify.sanitize(html, { ALLOWED_TAGS: ['b', 'strong', 'i', 'em', 'br', 'p', 'ul', 'ol', 'li', 'a', 'code', 'pre'] });
}

function sendMessage() {
    const userInput = document.getElementById('message-input');
    const message = userInput.value.trim();
    
    if (message === '') return;
    
    // Add user message to chat
    addMessage(message, 'user');
    
    // Clear input
    userInput.value = '';
    
    // Show typing indicator
    showTypingIndicator();
    
    // Send to backend - CHANGE FROM /send TO /chat
    fetch('/chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: message })
    })
    .then(response => response.json())
    .then(data => {
        hideTypingIndicator();
        
        if (data.error) {
            addMessage('❌ Fehler: ' + data.error, 'bot');
        } else {
            addMessage(data.response, 'bot');
            
            // Add sources if available
            if (data.sources && data.sources.length > 0) {
                addSourcesMessage(data.sources);
            }
        }
    })
    .catch(error => {
        hideTypingIndicator();
        console.error('Error:', error);
        addMessage('❌ Verbindungsfehler. Bitte versuchen Sie es erneut.', 'bot');
    });
}

function clearChat() {
    const chatMessages = document.getElementById('chat-box');
    
    // Remove all dynamically added messages, keep only the initial welcome messages
    const messages = chatMessages.querySelectorAll('.media.message');
    messages.forEach((message, index) => {
        // Keep the first 2 messages (user greeting and bot welcome)
        if (index >= 2) {
            message.remove();
        }
    });
    
    // Optional: Send request to backend to clear chat history in database
    fetch('/clear-chat', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .catch(error => {
        console.log('Chat history clear request failed (this is normal for demo mode):', error);
    });
}

function addMessage(message, sender) {
    const chatMessages = document.getElementById('chat-box');
    const messageDiv = document.createElement('div');
    
    if (sender === 'user') {
        messageDiv.className = 'media message user mb-3';
        messageDiv.innerHTML = `
            <div class="media-body">
                <p class="text-names" style="text-align: right;"><span class="text-names"></span>User<span class="ml-1">👨‍🎓</span></p>
                <div class="user-bubble rounded py-2 px-3 mb-2">
                    <p class="text-small mb-0">${escapeHtml(message)}</p>
                </div>
            </div>
        `;
    } else {
        messageDiv.className = 'media message bot mb-3';
        messageDiv.innerHTML = `
            <div class="media-body ml-3">
                <p class="text-names"><span class="mr-1"><img src="/static/images/logo-book.png" width="32" height="32"></span>TutorAI<span class="text-names"></span></p>
                <div class="bot-bubble rounded py-2 px-3 mb-2">
                    <p class="text-small mb-0">${sanitizeBotHtml(message)}</p>
                </div>
            </div>
        `;
    }
    
    chatMessages.appendChild(messageDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function addSourcesMessage(sources) {
    const chatMessages = document.getElementById('chat-box');
    const sourcesDiv = document.createElement('div');
    sourcesDiv.className = 'media message bot mb-3';
    
    const sourcesId = 'sources-' + Date.now(); // Unique ID for this sources list
    
    let sourcesHtml = `
        <div class="media-body ml-3">
            <p class="text-names" style="font-size: 0.85rem; cursor: pointer;" onclick="toggleSources('${sourcesId}')">
                <span class="mr-1"><img src="/static/images/logo-book.png" width="24" height="24"></span>
                Quellen <span id="${sourcesId}-count" style="font-size: 0.7rem; color: #666;">(${sources.length} Treffer)</span>
                <span id="${sourcesId}-toggle" style="font-size: 0.8rem;">▶</span>
            </p>
            <div id="${sourcesId}" class="bot-bubble rounded py-2 px-3 mb-2" style="display: none;">
                <ul style="font-size: 0.6rem; margin-bottom: 0; padding-left: 1.2rem;">`;
    
    sources.forEach(source => {
        sourcesHtml += `<li>`;
        
        // Add clickable link if URL is available
        const subject = escapeHtml(source.subject);
        const sourceUrl = safeUrl(source.url);
        if (sourceUrl) {
            sourcesHtml += `<a href="${sourceUrl}" target="_blank" rel="noopener noreferrer" style="color: inherit; text-decoration: underline;"><strong>${subject}</strong></a>`;
        } else {
            sourcesHtml += `<strong>${subject}</strong>`;
        }
        
        sourcesHtml += ` (Relevanz: ${(Number(source.score) * 100).toFixed(1)}%)`;
        
        // Only show author if it exists and is not empty
        if (source.author && source.author !== 'Unbekannt' && source.author !== '') {
            sourcesHtml += ` - von ${escapeHtml(source.author)}`;
        }
        
        // Show "View Changes" button if this slide has changes
        if (source.has_changes && source.change_id) {
            sourcesHtml += ` <button data-change-id="${escapeHtml(source.change_id)}" onclick="showDiffModal(this.dataset.changeId)" 
                style="margin-top: 0px; padding: 0px 1px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.85em;">
                🔄 diff 
            </button>`;
        }
        
        // Show embedded links if available (for posts)
        if (source.links && source.links.length > 0) {
            sourcesHtml += `<br><span style="font-size: 0.9em; color: #6c757d;">🔗 Erwähnte Links: `;
            source.links.forEach((link, index) => {
                if (index > 0) sourcesHtml += ', ';
                sourcesHtml += `<a href="${safeUrl(link.url)}" target="_blank" rel="noopener noreferrer" style="color: #007bff;">${escapeHtml(link.text || link.url)}</a>`;
            });
            sourcesHtml += `</span>`;
        }
        
        // Show embedded URLs if available (for slides)
        if (source.urls && source.urls.length > 0) {
            sourcesHtml += `<br><span style="font-size: 0.9em; color: #6c757d;">🔗 URLs auf Folie: `;
            source.urls.forEach((url, index) => {
                if (index > 0) sourcesHtml += ', ';
                // Extract domain or show shortened URL
                const displayUrl = url.length > 50 ? url.substring(0, 47) + '...' : url;
                sourcesHtml += `<a href="${safeUrl(url)}" target="_blank" rel="noopener noreferrer" style="color: #007bff;">${escapeHtml(displayUrl)}</a>`;
            });
            sourcesHtml += `</span>`;
        }
        
        sourcesHtml += '</li>';
    });
    sourcesHtml += `
                </ul>
            </div>
        </div>
    `;
    
    sourcesDiv.innerHTML = sourcesHtml;
    chatMessages.appendChild(sourcesDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function toggleSources(sourcesId) {
    const sourcesElement = document.getElementById(sourcesId);
    const toggleIcon = document.getElementById(sourcesId + '-toggle');
    
    if (sourcesElement.style.display === 'none') {
        sourcesElement.style.display = 'block';
        toggleIcon.textContent = '▼';
    } else {
        sourcesElement.style.display = 'none';
        toggleIcon.textContent = '▶';
    }
}

function showDiffModal(changeId) {
    // Create modal overlay
    const modal = document.createElement('div');
    modal.id = 'diff-modal';
    modal.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.7);
        z-index: 9999;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 20px;
    `;
    
    // Create modal content container
    const modalContent = document.createElement('div');
    modalContent.style.cssText = `
        background: white;
        width: 90%;
        height: 90%;
        border-radius: 12px;
        overflow: hidden;
        display: flex;
        flex-direction: column;
        box-shadow: 0 10px 40px rgba(0,0,0,0.3);
    `;
    
    // Create modal header
    const modalHeader = document.createElement('div');
    modalHeader.style.cssText = `
        padding: 15px 20px;
        background: #f8f9fa;
        border-bottom: 1px solid #dee2e6;
        display: flex;
        justify-content: space-between;
        align-items: center;
    `;
    modalHeader.innerHTML = `
        <h3 style="margin: 0; font-size: 18px; color: #333;">📊 Folienänderungen</h3>
        <button onclick="closeDiffModal()" style="background: none; border: none; font-size: 24px; cursor: pointer; color: #666; line-height: 1;">×</button>
    `;
    
    // Create iframe container
    const iframeContainer = document.createElement('div');
    iframeContainer.style.cssText = `
        flex: 1;
        overflow: hidden;
        padding: 15px;
        background: #fafafa;
    `;
    
    // Create loading indicator
    const loadingIndicator = document.createElement('div');
    loadingIndicator.style.cssText = `
        text-align: center;
        padding: 50px;
        font-size: 16px;
        color: #666;
    `;
    loadingIndicator.innerHTML = 'Lade Änderungen<span id="loading-dots"></span>';
    iframeContainer.appendChild(loadingIndicator);
    
    // Start loading animation
    let dotCount = 0;
    const loadingInterval = setInterval(() => {
        const dotsElement = document.getElementById('loading-dots');
        if (dotsElement) {
            dotCount = (dotCount + 1) % 4;
            dotsElement.textContent = '.'.repeat(dotCount);
        }
    }, 400);
    
    // Assemble modal
    modalContent.appendChild(modalHeader);
    modalContent.appendChild(iframeContainer);
    modal.appendChild(modalContent);
    document.body.appendChild(modal);
    
    // Fetch diff HTML using change_id
    fetch(`/get-diff/${encodeURIComponent(changeId)}`)
        .then(response => {
            if (!response.ok) {
                throw new Error('Diff konnte nicht geladen werden');
            }
            return response.text();
        })
        .then(html => {
            clearInterval(loadingInterval);
            
            // Create iframe to display diff HTML
            const iframe = document.createElement('iframe');
            iframe.style.cssText = `
                width: 100%;
                height: 100%;
                border: none;
                border-radius: 8px;
                background: white;
            `;
            
            iframeContainer.innerHTML = '';
            iframeContainer.appendChild(iframe);
            
            // Sandboxed: the diff page is shown, but its HTML can't run scripts
            iframe.setAttribute('sandbox', '');
            iframe.srcdoc = html;
        })
        .catch(error => {
            clearInterval(loadingInterval);
            iframeContainer.innerHTML = `
                <div style="text-align: center; padding: 50px; color: #dc3545;">
                    <p style="font-size: 18px; margin-bottom: 10px;">❌ Fehler beim Laden der Änderungen</p>
                    <p style="font-size: 14px; color: #666;">${escapeHtml(error.message)}</p>
                </div>
            `;
        });
    
    // Close modal on background click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            closeDiffModal();
        }
    });
}

function closeDiffModal() {
    const modal = document.getElementById('diff-modal');
    if (modal) {
        modal.remove();
    }
}

function showTypingIndicator() {
    const chatMessages = document.getElementById('chat-box');
    const typingDiv = document.createElement('div');
    typingDiv.id = 'typing-indicator';
    typingDiv.className = 'media message bot mb-3';
    typingDiv.innerHTML = `
        <div class="media-body ml-3">
            <p class="text-names"><span class="mr-1"><img src="/static/images/logo-book.png" width="32" height="32"></span>TutorAI<span class="text-names"></span></p>
            <div class="bot-bubble rounded py-2 px-3 mb-2">
                <p class="text-small mb-0">denke nach<span id="thinking-dots"></span></p>
            </div>
        </div>
    `;
    chatMessages.appendChild(typingDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    
    // Start animated dots
    startThinkingAnimation();
}

function startThinkingAnimation() {
    let dotCount = 0;
    
    window.thinkingInterval = setInterval(() => {
        const dotsElement = document.getElementById('thinking-dots');
        if (!dotsElement) {
            clearInterval(window.thinkingInterval);
            return;
        }
        
        // Update dot count
        dotCount++;
        if (dotCount >= 4) {
            dotCount = 0;
        }

        // Update dots display
        dotsElement.textContent = '.'.repeat(dotCount);
    }, 400); // Change every 400ms for smooth animation
}

function hideTypingIndicator() {
    const typingIndicator = document.getElementById('typing-indicator');
    if (typingIndicator) {
        typingIndicator.remove();
    }
    
    // Clear the thinking animation interval
    if (window.thinkingInterval) {
        clearInterval(window.thinkingInterval);
        window.thinkingInterval = null;
    }
}

// Enter key support
document.addEventListener('DOMContentLoaded', function() {
    const userInput = document.getElementById('message-input');
    if (userInput) {
        userInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault(); // Prevent newline
                sendMessage();
            }
        });
    }
    
    // Initialize scroll-to-bottom functionality
    initializeScrollToBottom();
});

// Scroll-to-bottom button functionality
function initializeScrollToBottom() {
    const chatBox = document.getElementById('chat-box');
    const scrollToBottomBtn = document.getElementById('scroll-to-bottom');
    
    if (!chatBox || !scrollToBottomBtn) return;
    
    // Function to check if user is near bottom of chat
    function checkScrollPosition() {
        const scrollTop = chatBox.scrollTop;
        const scrollHeight = chatBox.scrollHeight;
        const clientHeight = chatBox.clientHeight;
        
        // Show button when user is more than 200px from bottom
        const isNearBottom = scrollHeight - scrollTop - clientHeight < 200;
        
        if (isNearBottom) {
            scrollToBottomBtn.classList.remove('opacity-100');
            scrollToBottomBtn.classList.add('opacity-0');
        } else {
            scrollToBottomBtn.classList.remove('opacity-0');
            scrollToBottomBtn.classList.add('opacity-100');
        }
    }
    
    // Function to scroll to bottom smoothly
    function scrollToBottom() {
        chatBox.scrollTo({
            top: chatBox.scrollHeight,
            behavior: 'smooth'
        });
    }
    
    // Auto-scroll to bottom on page load/refresh
    setTimeout(() => {
        chatBox.scrollTop = chatBox.scrollHeight;
    }, 0);
    
    // Initial scroll position check
    checkScrollPosition();
    
    // Check scroll position when user scrolls
    chatBox.addEventListener('scroll', checkScrollPosition);
    
    // Scroll to bottom when button is clicked
    scrollToBottomBtn.addEventListener('click', scrollToBottom);
    
    // Auto-scroll to bottom when new messages are added
    const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
                // Check if we were already near the bottom before adding new content
                const wasNearBottom = chatBox.scrollHeight - chatBox.scrollTop - chatBox.clientHeight < 200;
                if (wasNearBottom) {
                    setTimeout(scrollToBottom, 100); // Small delay to ensure content is rendered
                }
                // Update scroll position check after new content
                setTimeout(checkScrollPosition, 100);
            }
        });
    });
    
    // Observe the chat box for new messages
    observer.observe(chatBox, { childList: true, subtree: true });
}