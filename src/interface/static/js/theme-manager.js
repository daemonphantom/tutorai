function setCookie(name, value, days = 365) {
    const expires = new Date();
    expires.setTime(expires.getTime() + (days * 24 * 60 * 60 * 1000));
    document.cookie = `${name}=${value};expires=${expires.toUTCString()};path=/;SameSite=Lax`;
}

function getCookie(name) {
    const nameEQ = name + "=";
    const ca = document.cookie.split(';');
    for(let i = 0; i < ca.length; i++) {
        let c = ca[i];
        while (c.charAt(0) === ' ') c = c.substring(1, c.length);
        if (c.indexOf(nameEQ) === 0) return c.substring(nameEQ.length, c.length);
    }
    return null;
}

class ThemeManager {
    constructor() {
        this.body = document.body;
        this.chatBox = document.getElementById('chat-box');
        this.settingsPanel = document.getElementById('settings-panel');
        this.settingsToggle = document.getElementById('settings-toggle');
        this.settingsToggle2 = document.getElementById('settings-toggle2');
        this.settingsOverlay = document.getElementById('settings-overlay');
        this.darkModeButton = document.getElementById('dark-mode-button');
        this.designSelect = document.getElementById('design-select');
        
        this.applySavedPreferences();
        this.bindEventListeners();
        this.bindSettingsPanel();
    }
    
    applySavedPreferences() {
        const savedTheme = getCookie('theme') || 'light'; // Default to light
        const savedDesign = getCookie('design') || 'design-3'; // Default to design-3

        if(savedTheme === 'dark') {
            this.body.classList.add('dark-mode');
            if(this.chatBox) this.chatBox.classList.add('dark-mode');
            if(this.settingsPanel) this.settingsPanel.classList.add('dark-mode');
        }

        this.body.classList.add(savedDesign);
        if(this.designSelect) this.designSelect.value = savedDesign;
        
        setCookie('design', savedDesign);
        setCookie('theme', savedTheme);
    }
    
    changeDesign(selectedDesign) {
        this.body.classList.remove('design-1', 'design-2', 'design-3', 'design-4', 'design-5');
        if(this.chatBox) this.chatBox.classList.remove('design-1', 'design-2', 'design-3', 'design-4', 'design-5');
        if(this.settingsPanel) this.settingsPanel.classList.remove('design-1', 'design-2', 'design-3', 'design-4', 'design-5');
        
        this.body.classList.add(selectedDesign);
        if(this.chatBox) this.chatBox.classList.add(selectedDesign);
        if(this.settingsPanel) this.settingsPanel.classList.add(selectedDesign);

        setCookie('design', selectedDesign, 365);
    }
    
    toggleDarkMode() {
        this.body.classList.toggle('dark-mode');
        if(this.chatBox) this.chatBox.classList.toggle('dark-mode');
        if(this.settingsPanel) this.settingsPanel.classList.toggle('dark-mode');
        
        const isDarkMode = this.body.classList.contains('dark-mode');
        
        setCookie('theme', isDarkMode ? 'dark' : 'light', 365);
    }
    
    bindEventListeners() {
        // Design selector change event
        if(this.designSelect) {
            this.designSelect.addEventListener('change', (e) => {
                this.changeDesign(e.target.value);
            });
        }
        
        // Dark mode toggle event
        if(this.darkModeButton) {
            this.darkModeButton.addEventListener('click', () => {
                this.toggleDarkMode();
            });
        }
    }

    bindSettingsPanel() {
        // Settings panel toggle functionality - common across all pages
        if(this.settingsToggle) {
            this.settingsToggle.addEventListener('click', () => {
                if(this.settingsPanel) this.settingsPanel.classList.toggle('open');
                if(this.settingsOverlay) this.settingsOverlay.classList.toggle('active');
            });
        }

        if(this.settingsToggle2) {
            this.settingsToggle2.addEventListener('click', () => {
                if(this.settingsPanel) this.settingsPanel.classList.toggle('open');
                if(this.settingsOverlay) this.settingsOverlay.classList.toggle('active');
            });
        }

        // Close settings panel when clicking outside
        if(this.settingsOverlay) {
            this.settingsOverlay.addEventListener('click', () => {
                if(this.settingsPanel) this.settingsPanel.classList.remove('open');
                this.settingsOverlay.classList.remove('active');
            });
        }
    }
}

// Initialize theme manager when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    window.themeManager = new ThemeManager();
});

// Also initialize immediately if DOM is already loaded
if (document.readyState === 'loading') {
    // DOM is still loading
    document.addEventListener('DOMContentLoaded', function() {
        if(!window.themeManager) window.themeManager = new ThemeManager();
    });
} else {
    // DOM has already loaded
    window.themeManager = new ThemeManager();
}
