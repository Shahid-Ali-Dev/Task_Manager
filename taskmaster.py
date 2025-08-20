#!/usr/bin/env python3
"""
Features:
- Create / Edit / Delete tasks
- Mark tasks completed
- Search and filter by text, priority, status, and due date
- Persist tasks in an SQLite DB (file: tasks.db)
- Export tasks to CSV
- Input validation and user-friendly messages
- Logging to file 'taskmaster.log'

Requirements: Python 3.8+ (only standard library)
Author: Shahid Ali
License: MIT
"""

from __future__ import annotations
import csv
import datetime as dt
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    StringVar,
    Tk,
    Toplevel,
    messagebox,
    filedialog,
)
from tkinter import ttk
from typing import Iterable, List, Optional, Tuple

# -------------------------- Configuration & Logging ------------------------- #

APP_NAME = "TaskMaster"
DB_FILENAME = Path(__file__).with_name("tasks.db")
LOG_FILE = Path(__file__).with_name("taskmaster.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(APP_NAME)


# ------------------------------- Domain Model -------------------------------- #

@dataclass
class Task:
    id: Optional[int]
    title: str
    description: str
    priority: int  # 1 (low) .. 5 (high)
    due_date: Optional[dt.date]
    completed: bool

    def as_tuple(self) -> Tuple:
        """Return tuple for DB insertion (excluding id)."""
        return (
            self.title,
            self.description,
            self.priority,
            self.due_date.isoformat() if self.due_date else None,
            1 if self.completed else 0,
        )


# ------------------------------- Repository ---------------------------------- #

class TaskRepository:
    """SQLite-backed repository for Task objects."""

    def __init__(self, db_path: Path = DB_FILENAME):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()
        logger.info("TaskRepository initialized using DB: %s", self.db_path)

    def _ensure_schema(self) -> None:
        sql = """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            priority INTEGER NOT NULL,
            due_date TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
        self._conn.execute(sql)
        self._conn.commit()

    def add(self, task: Task) -> int:
        cur = self._conn.execute(
            "INSERT INTO tasks (title, description, priority, due_date, completed) VALUES (?, ?, ?, ?, ?)",
            task.as_tuple(),
        )
        self._conn.commit()
        task_id = cur.lastrowid
        logger.info("Added task %s (id=%s)", task.title, task_id)
        return task_id

    def update(self, task: Task) -> None:
        if task.id is None:
            raise ValueError("Task id is required for update.")
        self._conn.execute(
            "UPDATE tasks SET title=?, description=?, priority=?, due_date=?, completed=? WHERE id=?",
            (*task.as_tuple(), 1 if task.completed else 0, task.id),
        )
        self._conn.commit()
        logger.info("Updated task id=%s title=%s", task.id, task.title)

    def delete(self, task_id: int) -> None:
        self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self._conn.commit()
        logger.info("Deleted task id=%s", task_id)

    def get(self, task_id: int) -> Optional[Task]:
        cur = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
        row = cur.fetchone()
        return self._row_to_task(row) if row else None

    def list_all(self) -> List[Task]:
        cur = self._conn.execute("SELECT * FROM tasks ORDER BY completed, priority DESC, due_date IS NULL, due_date")
        rows = cur.fetchall()
        return [self._row_to_task(r) for r in rows]

    def search(self, text: str = "", priority: Optional[int] = None, show_completed: Optional[bool] = None) -> List[Task]:
        sql = "SELECT * FROM tasks WHERE 1=1"
        params: List = []
        if text:
            sql += " AND (title LIKE ? OR description LIKE ?)"
            txt = f"%{text}%"
            params.extend([txt, txt])
        if priority is not None:
            sql += " AND priority = ?"
            params.append(priority)
        if show_completed is not None:
            sql += " AND completed = ?"
            params.append(1 if show_completed else 0)
        sql += " ORDER BY completed, priority DESC, due_date IS NULL, due_date"
        cur = self._conn.execute(sql, params)
        return [self._row_to_task(r) for r in cur.fetchall()]

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        due = row["due_date"]
        due_date = dt.date.fromisoformat(due) if due else None
        return Task(
            id=int(row["id"]),
            title=row["title"],
            description=row["description"] or "",
            priority=int(row["priority"]),
            due_date=due_date,
            completed=bool(row["completed"]),
        )

    def close(self) -> None:
        self._conn.close()
        logger.info("TaskRepository connection closed.")


# ------------------------------- GUI Application ------------------------------ #

class TaskMasterApp:
    PRIORITY_CHOICES = [1, 2, 3, 4, 5]

    def __init__(self, root: Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.repo = TaskRepository()
        self._build_ui()
        self._refresh_tasks()

    # -------------------- UI construction -------------------- #
    def _build_ui(self) -> None:
        # Main frames
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=BOTH, expand=True)

        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=(0, 10))

        # Search box
        self.search_var = StringVar()
        ttk.Label(controls, text="Search:").pack(side=LEFT, padx=(0, 6))
        self.search_entry = ttk.Entry(controls, textvariable=self.search_var, width=30)
        self.search_entry.pack(side=LEFT)
        self.search_entry.bind("<Return>", lambda e: self._on_search())

        ttk.Button(controls, text="Search", command=self._on_search).pack(side=LEFT, padx=6)
        ttk.Button(controls, text="Reset", command=self._on_reset).pack(side=LEFT)

        # Buttons
        ttk.Button(controls, text="New Task", command=self._open_new_task).pack(side=RIGHT, padx=(6, 0))
        ttk.Button(controls, text="Export CSV", command=self._export_csv).pack(side=RIGHT)

        # Treeview (task list)
        columns = ("id", "title", "priority", "due_date", "completed")
        self.tree = ttk.Treeview(main, columns=columns, show="headings", selectmode="browse", height=14)
        self.tree.heading("id", text="ID")
        self.tree.heading("title", text="Title")
        self.tree.heading("priority", text="Priority")
        self.tree.heading("due_date", text="Due Date")
        self.tree.heading("completed", text="Done")
        self.tree.column("id", width=40, anchor="center")
        self.tree.column("title", width=300)
        self.tree.column("priority", width=80, anchor="center")
        self.tree.column("due_date", width=100, anchor="center")
        self.tree.column("completed", width=60, anchor="center")
        self.tree.pack(fill=BOTH, expand=True)

        # Context / action buttons
        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Edit", command=self._on_edit).pack(side=LEFT)
        ttk.Button(actions, text="Delete", command=self._on_delete).pack(side=LEFT, padx=6)
        ttk.Button(actions, text="Toggle Done", command=self._on_toggle_done).pack(side=LEFT)
        ttk.Button(actions, text="Show All", command=self._refresh_tasks).pack(side=LEFT, padx=6)

        # Double-click to edit
        self.tree.bind("<Double-1>", lambda e: self._on_edit())

    # -------------------- Data / Helpers -------------------- #
    def _refresh_tasks(self) -> None:
        try:
            tasks = self.repo.list_all()
            self._populate_tree(tasks)
        except Exception as e:
            logger.exception("Failed to refresh tasks: %s", e)
            messagebox.showerror(APP_NAME, f"Failed to refresh tasks: {e}")

    def _populate_tree(self, tasks: Iterable[Task]) -> None:
        self.tree.delete(*self.tree.get_children())
        for t in tasks:
            due = t.due_date.isoformat() if t.due_date else ""
            self.tree.insert("", END, iid=str(t.id), values=(t.id, t.title, t.priority, due, "✔" if t.completed else ""))

    def _selected_task_id(self) -> Optional[int]:
        sel = self.tree.selection()
        if not sel:
            return None
        return int(sel[0])

    # -------------------- Actions -------------------- #
    def _on_search(self) -> None:
        txt = self.search_var.get().strip()
        try:
            results = self.repo.search(text=txt)
            self._populate_tree(results)
        except Exception as e:
            logger.exception("Search failed: %s", e)
            messagebox.showerror(APP_NAME, "Search failed: " + str(e))

    def _on_reset(self) -> None:
        self.search_var.set("")
        self._refresh_tasks()

    def _open_new_task(self) -> None:
        editor = TaskEditor(self.root, self.repo)
        self.root.wait_window(editor.top)
        # after dialog closes, refresh
        self._refresh_tasks()

    def _on_edit(self) -> None:
        tid = self._selected_task_id()
        if not tid:
            messagebox.showinfo(APP_NAME, "Select a task to edit.")
            return
        task = self.repo.get(tid)
        if not task:
            messagebox.showerror(APP_NAME, "Task not found.")
            return
        editor = TaskEditor(self.root, self.repo, task)
        self.root.wait_window(editor.top)
        self._refresh_tasks()

    def _on_delete(self) -> None:
        tid = self._selected_task_id()
        if not tid:
            messagebox.showinfo(APP_NAME, "Select a task to delete.")
            return
        if messagebox.askyesno(APP_NAME, "Delete selected task?"):
            try:
                self.repo.delete(tid)
                self._refresh_tasks()
            except Exception as e:
                logger.exception("Delete failed: %s", e)
                messagebox.showerror(APP_NAME, "Delete failed: " + str(e))

    def _on_toggle_done(self) -> None:
        tid = self._selected_task_id()
        if not tid:
            messagebox.showinfo(APP_NAME, "Select a task.")
            return
        t = self.repo.get(tid)
        if not t:
            messagebox.showerror(APP_NAME, "Task not found.")
            return
        t.completed = not t.completed
        try:
            self.repo.update(t)
            self._refresh_tasks()
        except Exception as e:
            logger.exception("Toggle failed: %s", e)
            messagebox.showerror(APP_NAME, "Toggle failed: " + str(e))

    def _export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")], title="Export tasks to CSV"
        )
        if not path:
            return
        try:
            tasks = self.repo.list_all()
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["id", "title", "description", "priority", "due_date", "completed"])
                for t in tasks:
                    writer.writerow([t.id, t.title, t.description, t.priority, t.due_date.isoformat() if t.due_date else "", int(t.completed)])
            messagebox.showinfo(APP_NAME, f"Tasks exported to {path}")
            logger.info("Exported tasks to CSV: %s", path)
        except Exception as e:
            logger.exception("Export failed: %s", e)
            messagebox.showerror(APP_NAME, "Export failed: " + str(e))


# ------------------------------- Task Editor Dialog --------------------------- #

class TaskEditor:
    """Modal dialog to create or edit a task."""

    def __init__(self, parent: Tk, repo: TaskRepository, task: Optional[Task] = None):
        self.repo = repo
        self.task = task
        self.top = Toplevel(parent)
        self.top.title("Edit Task" if task else "New Task")
        self.top.transient(parent)
        self.top.grab_set()

        # Fields
        ttk.Label(self.top, text="Title:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.title_var = StringVar(value=task.title if task else "")
        self.title_entry = ttk.Entry(self.top, textvariable=self.title_var, width=50)
        self.title_entry.grid(row=0, column=1, padx=6, pady=6, columnspan=2)

        ttk.Label(self.top, text="Priority (1-5):").grid(row=1, column=0, sticky="w", padx=6)
        self.priority_var = StringVar(value=str(task.priority) if task else "3")
        self.priority_combo = ttk.Combobox(self.top, values=[str(p) for p in TaskMasterApp.PRIORITY_CHOICES], width=5, textvariable=self.priority_var, state="readonly")
        self.priority_combo.grid(row=1, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self.top, text="Due date (YYYY-MM-DD):").grid(row=2, column=0, sticky="w", padx=6)
        self.due_var = StringVar(value=task.due_date.isoformat() if task and task.due_date else "")
        self.due_entry = ttk.Entry(self.top, textvariable=self.due_var, width=15)
        self.due_entry.grid(row=2, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self.top, text="Completed:").grid(row=3, column=0, sticky="w", padx=6)
        self.completed_var = StringVar(value="Yes" if task and task.completed else "No")
        self.completed_combo = ttk.Combobox(self.top, values=["No", "Yes"], textvariable=self.completed_var, state="readonly", width=5)
        self.completed_combo.grid(row=3, column=1, sticky="w", padx=6, pady=6)

        ttk.Label(self.top, text="Description:").grid(row=4, column=0, sticky="nw", padx=6)
        self.desc_text = ttk.Entry(self.top, width=60)
        self.desc_text.grid(row=4, column=1, padx=6, pady=6, columnspan=2)
        if task:
            self.desc_text.delete(0, END)
            self.desc_text.insert(0, task.description)

        # Buttons
        btn_frame = ttk.Frame(self.top)
        btn_frame.grid(row=5, column=0, columnspan=3, pady=(6, 10))
        ttk.Button(btn_frame, text="Save", command=self._on_save).pack(side=LEFT, padx=6)
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side=LEFT)

        # Focus
        self.title_entry.focus_set()

    def _validate(self) -> Tuple[bool, Optional[str]]:
        title = self.title_var.get().strip()
        if not title:
            return False, "Title cannot be empty."
        try:
            p = int(self.priority_var.get())
            if p < 1 or p > 5:
                return False, "Priority must be between 1 and 5."
        except ValueError:
            return False, "Priority must be an integer between 1 and 5."
        due = self.due_var.get().strip()
        if due:
            try:
                dt.date.fromisoformat(due)
            except Exception:
                return False, "Due date must be in YYYY-MM-DD format."
        return True, None

    def _on_save(self) -> None:
        ok, err = self._validate()
        if not ok:
            messagebox.showerror("Validation error", err)
            return
        title = self.title_var.get().strip()
        desc = self.desc_text.get().strip()
        priority = int(self.priority_var.get())
        due = self.due_var.get().strip()
        due_date = dt.date.fromisoformat(due) if due else None
        completed = True if self.completed_var.get() == "Yes" else False

        if self.task:
            updated = Task(id=self.task.id, title=title, description=desc, priority=priority, due_date=due_date, completed=completed)
            try:
                # Update existing
                self.repo.update(updated)
                messagebox.showinfo("Saved", "Task updated.")
            except Exception as e:
                logger.exception("Failed to update task: %s", e)
                messagebox.showerror("Error", f"Failed to update task: {e}")
        else:
            new_task = Task(id=None, title=title, description=desc, priority=priority, due_date=due_date, completed=completed)
            try:
                self.repo.add(new_task)
                messagebox.showinfo("Saved", "Task added.")
            except Exception as e:
                logger.exception("Failed to add task: %s", e)
                messagebox.showerror("Error", f"Failed to add task: {e}")
        self.top.destroy()

    def _on_cancel(self) -> None:
        self.top.destroy()


# ------------------------------- Application Entry --------------------------- #

def main() -> None:
    logger.info("Starting %s", APP_NAME)
    root = Tk()
    # Use a modern theme if possible
    try:
        style = ttk.Style()
        style.theme_use("clam")  # 'clam' is usually available and neutral
    except Exception:
        pass
    app = TaskMasterApp(root)
    # Populate with a sample if DB empty
    if not app.repo.list_all():
        try:
            app.repo.add(Task(id=None, title="Welcome to TaskMaster", description="Edit or delete this sample task.", priority=3, due_date=None, completed=False))
            app.repo.add(Task(id=None, title="Finish report", description="Complete the quarterly report.", priority=4, due_date=dt.date.today() + dt.timedelta(days=3), completed=False))
            app.repo.add(Task(id=None, title="Pay bills", description="Utilities and internet", priority=2, due_date=dt.date.today() + dt.timedelta(days=7), completed=False))
            app._refresh_tasks()
        except Exception:
            logger.exception("Failed to insert sample tasks.")

    try:
        root.mainloop()
    finally:
        app.repo.close()
        logger.info("Exiting %s", APP_NAME)


if __name__ == "__main__":
    main()
