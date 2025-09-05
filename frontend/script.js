// Frontend - script.js
class TodoApp {
    constructor() {
        this.todos = [];
        this.currentFilter = 'all';
        this.editingTodoId = null;
        this.apiBaseUrl = 'http://localhost:8080'; // backend FastAPI service

        this.init();
    }

    init() {
        this.bindEvents();
        this.loadTodos();
        this.updateStats();
    }

    bindEvents() {
        // Add new todo
        document.getElementById('todoForm').addEventListener('submit', (e) => {
            e.preventDefault();
            this.addTodo();
        });

        // Filter buttons
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.setFilter(e.target.dataset.filter);
            });
        });

        // Modal events
        document.getElementById('closeModal').addEventListener('click', () => this.closeModal());
        document.getElementById('cancelEdit').addEventListener('click', () => this.closeModal());
        document.getElementById('editForm').addEventListener('submit', (e) => {
            e.preventDefault();
            this.saveEdit();
        });
        document.getElementById('editModal').addEventListener('click', (e) => {
            if (e.target.id === 'editModal') this.closeModal();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') this.closeModal();
        });
    }

    // ================= Load =================
    async loadTodos() {
        try {
            this.showLoading(true);
            const response = await fetch(`${this.apiBaseUrl}/todos`);
            if (!response.ok) throw new Error(await response.text());
            this.todos = await response.json();
            this.renderTodos();
            this.updateStats();
        } catch (err) {
            this.showToast(`Failed to load todos: ${err.message}`, 'error');
        } finally {
            this.showLoading(false);
        }
    }

    // ================= Add =================
    async addTodo() {
        const titleInput = document.getElementById('todoTitle');
        const descriptionInput = document.getElementById('todoDescription');

        const title = titleInput.value.trim();
        const description = descriptionInput.value.trim();

        if (!title) {
            this.showToast('Please enter a todo title', 'warning');
            return;
        }

        try {
            this.showLoading(true);
            const response = await fetch(`${this.apiBaseUrl}/todos`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, description: description || null })
            });
            if (!response.ok) throw new Error(await response.text());
            const newTodo = await response.json();
            this.todos.unshift(newTodo);
            titleInput.value = '';
            descriptionInput.value = '';
            this.renderTodos();
            this.updateStats();
            this.showToast('Todo added!', 'success');
        } catch (err) {
            this.showToast(`Failed to add todo: ${err.message}`, 'error');
        } finally {
            this.showLoading(false);
        }
    }

    // ================= Toggle Complete =================
    async toggleTodo(todoId) {
        const todo = this.todos.find(t => t.id === todoId);
        if (!todo) return;

        try {
            const payload = { completed: !todo.completed };  // ✅ strictly only completed

            const response = await fetch(`${this.apiBaseUrl}/todos/${todoId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!response.ok) throw new Error(await response.text());

            const updated = await response.json();
            const index = this.todos.findIndex(t => t.id === todoId);
            this.todos[index] = updated;
            this.renderTodos();
            this.updateStats();
            this.showToast(updated.completed ? 'Todo completed!' : 'Todo marked pending', 'success');
        } catch (err) {
            this.showToast(`Failed to update todo: ${err.message}`, 'error');
        }
    }

    // ================= Delete =================
    async deleteTodo(todoId) {
        if (!confirm('Delete this todo?')) return;
        try {
            const response = await fetch(`${this.apiBaseUrl}/todos/${todoId}`, { method: 'DELETE' });
            if (!response.ok) throw new Error(await response.text());
            this.todos = this.todos.filter(t => t.id !== todoId);
            this.renderTodos();
            this.updateStats();
            this.showToast('Todo deleted!', 'success');
        } catch (err) {
            this.showToast(`Failed to delete: ${err.message}`, 'error');
        }
    }

    // ================= Edit =================
    openEditModal(todoId) {
        const todo = this.todos.find(t => t.id === todoId);
        if (!todo) return;
        this.editingTodoId = todoId;
        document.getElementById('editTitle').value = todo.title;
        document.getElementById('editDescription').value = todo.description || '';
        document.getElementById('editModal').classList.add('show');
    }

    closeModal() {
        document.getElementById('editModal').classList.remove('show');
        this.editingTodoId = null;
    }

    async saveEdit() {
        const title = document.getElementById('editTitle').value.trim();
        const description = document.getElementById('editDescription').value.trim();
        if (!title) {
            this.showToast('Please enter a todo title', 'warning');
            return;
        }
        try {
            const response = await fetch(`${this.apiBaseUrl}/todos/${this.editingTodoId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title, description: description || null })
            });
            if (!response.ok) throw new Error(await response.text());
            const updated = await response.json();
            const index = this.todos.findIndex(t => t.id === this.editingTodoId);
            this.todos[index] = updated;
            this.renderTodos();
            this.updateStats();
            this.closeModal();
            this.showToast('Todo updated!', 'success');
        } catch (err) {
            this.showToast(`Failed to update: ${err.message}`, 'error');
        }
    }

    // ================= Rendering =================
    setFilter(filter) {
        this.currentFilter = filter;
        document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
        document.querySelector(`[data-filter="${filter}"]`).classList.add('active');
        this.renderTodos();
    }

    getFilteredTodos() {
        switch (this.currentFilter) {
            case 'pending': return this.todos.filter(t => !t.completed);
            case 'completed': return this.todos.filter(t => t.completed);
            default: return this.todos;
        }
    }

    renderTodos() {
        const todosList = document.getElementById('todosList');
        const emptyState = document.getElementById('emptyState');
        const filtered = this.getFilteredTodos();
        if (filtered.length === 0) {
            todosList.innerHTML = '';
            emptyState.classList.add('show');
            return;
        }
        emptyState.classList.remove('show');
        todosList.innerHTML = filtered.map(todo => this.createTodoHTML(todo)).join('');
    }

    createTodoHTML(todo) {
        let formattedDate = '';
        try {
            formattedDate = todo.created_at ? new Date(todo.created_at).toLocaleString() : '';
        } catch {}
        return `
            <div class="todo-item ${todo.completed ? 'completed' : ''}" data-id="${todo.id}">
                <div class="todo-header">
                    <div class="todo-checkbox ${todo.completed ? 'checked' : ''}" 
                         onclick="app.toggleTodo(${todo.id})"></div>
                    <div class="todo-content">
                        <div class="todo-title">${this.escapeHtml(todo.title)}</div>
                        ${todo.description ? `<div class="todo-description">${this.escapeHtml(todo.description)}</div>` : ''}
                        <div class="todo-meta"><small>${formattedDate}</small></div>
                    </div>
                    <div class="todo-actions">
                        <button class="action-btn edit-btn" onclick="app.openEditModal(${todo.id})"><i class="fas fa-edit"></i></button>
                        <button class="action-btn delete-btn" onclick="app.deleteTodo(${todo.id})"><i class="fas fa-trash"></i></button>
                    </div>
                </div>
            </div>
        `;
    }

    // ================= Utils =================
    updateStats() {
        const total = this.todos.length;
        const completed = this.todos.filter(t => t.completed).length;
        const pending = total - completed;
        document.getElementById('totalTodos').textContent = total;
        document.getElementById('completedTodos').textContent = completed;
        document.getElementById('pendingTodos').textContent = pending;
    }

    showLoading(show) {
        const overlay = document.getElementById('loadingOverlay');
        if (!overlay) return;
        if (show) overlay.classList.add('show');
        else overlay.classList.remove('show');
    }

    showToast(message, type = 'info') {
        const container = document.getElementById('toastContainer');
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        container.appendChild(toast);
        setTimeout(() => {
            toast.style.animation = 'slideOutRight 0.3s ease-out forwards';
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    }

    addStylesheet() {
        const style = document.createElement('style');
        style.textContent = `
            @keyframes slideOutRight {
                from { opacity: 1; transform: translateX(0); }
                to { opacity: 0; transform: translateX(100%); }
            }
            .todo-meta { margin-top: 0.5rem; }
            .todo-date { color: var(--text-muted); font-size: 0.8rem; }
        `;
        document.head.appendChild(style);
    }
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    window.app = new TodoApp();
    window.app.addStylesheet();
});
