import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from decimal import Decimal, InvalidOperation
from datetime import datetime
import json
import csv
import zipfile
import re
import os
import hashlib
import secrets
import xml.etree.ElementTree as ET

try:
    import openpyxl
except ImportError:
    openpyxl = None


# ============================================================
# STANDALONE .XLSX READER (stdlib-only fallback)
# ============================================================
# Used automatically when the optional 'openpyxl' package isn't installed.
# An .xlsx file is just a zip of XML parts, so plain values (numbers, text)
# can be read with zipfile + xml.etree from the standard library alone.
# This does NOT handle formulas, styles, or multiple sheets by name - it
# reads the first sheet's raw values, which is all transaction import needs.

_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_XLSX_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _xlsx_col_to_index(cell_ref):
    """Converts a cell reference like 'C7' to a zero-based column index (2)."""
    letters = re.match(r"[A-Za-z]+", cell_ref).group()
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch.upper()) - ord("A") + 1)
    return index - 1


def read_xlsx_stdlib(path):
    """Reads the first sheet of an .xlsx file into a list of {header: value}
    dicts, using only the standard library. Raises zipfile.BadZipFile,
    xml.etree.ElementTree.ParseError, or ValueError on malformed input.
    """
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()

        # Shared strings table (most text cells are stored here, not inline)
        shared_strings = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{_XLSX_NS}si"):
                text = "".join(t.text or "" for t in si.iter(f"{_XLSX_NS}t"))
                shared_strings.append(text)

        # Resolve the first sheet's actual XML part via workbook.xml + its .rels
        sheet_path = "xl/worksheets/sheet1.xml"
        if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
            wb_root = ET.fromstring(archive.read("xl/workbook.xml"))
            rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            rel_map = {
                rel.get("Id"): rel.get("Target")
                for rel in rels_root.findall(f"{_PKG_REL_NS}Relationship")
            }
            sheets_el = wb_root.find(f"{_XLSX_NS}sheets")
            first_sheet = sheets_el.find(f"{_XLSX_NS}sheet") if sheets_el is not None else None
            if first_sheet is not None:
                rid = first_sheet.get(f"{_XLSX_REL_NS}id")
                target = rel_map.get(rid)
                if target:
                    target = target.lstrip("/")
                    sheet_path = target if target.startswith("xl/") else f"xl/{target}"

        if sheet_path not in names:
            raise ValueError("Could not locate a worksheet inside the Excel file.")

        sheet_root = ET.fromstring(archive.read(sheet_path))

    sheet_data = sheet_root.find(f"{_XLSX_NS}sheetData")
    if sheet_data is None:
        return []

    grid = []
    max_col = 0
    for row_el in sheet_data.findall(f"{_XLSX_NS}row"):
        row_values = {}
        for cell_el in row_el.findall(f"{_XLSX_NS}c"):
            ref = cell_el.get("r")
            if not ref:
                continue
            col_index = _xlsx_col_to_index(ref)
            max_col = max(max_col, col_index)

            cell_type = cell_el.get("t")
            value_el = cell_el.find(f"{_XLSX_NS}v")

            if cell_type == "s" and value_el is not None:
                try:
                    value = shared_strings[int(value_el.text)]
                except (ValueError, IndexError, TypeError):
                    value = value_el.text
            elif cell_type == "inlineStr":
                is_el = cell_el.find(f"{_XLSX_NS}is")
                value = "".join(t.text or "" for t in is_el.iter(f"{_XLSX_NS}t")) if is_el is not None else ""
            elif value_el is not None:
                value = value_el.text
            else:
                value = None

            row_values[col_index] = value
        grid.append(row_values)

    if not grid:
        return []

    header_row = grid[0]
    header = [str(header_row.get(i, "") or "").strip() for i in range(max_col + 1)]

    raw_rows = []
    for row_values in grid[1:]:
        if not row_values:
            continue
        row_dict = {
            header[i]: row_values.get(i)
            for i in range(max_col + 1)
            if header[i]
        }
        if any(v not in (None, "") for v in row_dict.values()):
            raw_rows.append(row_dict)

    return raw_rows

# ============================================================
# ZIMBABWE VAT RETURN SYSTEM
# ============================================================

VAT_RATE = Decimal("0.155")

# Row styling: (transaction type, VAT category) -> background, text color, and font.
# Font is used semantically, not just decoratively: Standard-rated transactions
# actually carry VAT so they're bold; Zero-rated are regular weight; Exempt
# transactions sit outside VAT's scope entirely so they're shown in italic.
ROW_STYLES = {
    ("Sale", "Standard"):       {"bg": "#E8F5E9", "fg": "#1B5E20", "font": ("Arial", 10, "bold")},
    ("Sale", "Zero-rated"):     {"bg": "#E0F7FA", "fg": "#00695C", "font": ("Arial", 10, "normal")},
    ("Sale", "Exempt"):         {"bg": "#FFFDE7", "fg": "#8D6E00", "font": ("Arial", 10, "italic")},
    ("Purchase", "Standard"):   {"bg": "#E3F2FD", "fg": "#0D47A1", "font": ("Arial", 10, "bold")},
    ("Purchase", "Zero-rated"): {"bg": "#F3E5F5", "fg": "#6A1B9A", "font": ("Arial", 10, "normal")},
    ("Purchase", "Exempt"):     {"bg": "#F5F5F5", "fg": "#424242", "font": ("Arial", 10, "italic")},
}
DEFAULT_ROW_STYLE = {"bg": "#FFFFFF", "fg": "#000000", "font": ("Arial", 10, "normal")}

# Per-column text alignment, applied wherever these column names appear.
# Monetary columns are right-aligned like real accounting figures; the ID
# column is left-aligned; everything else stays centered.
COLUMN_ALIGNMENT = {
    "id": "w",
    "date": "center",
    "type": "center",
    "category": "center",
    "amount": "e",
    "taxable": "e",
    "vat": "e",
    "calculation": "w",
}

# Recognized column header variants for bulk import, mapped to canonical field names
IMPORT_COLUMN_ALIASES = {
    "id": ["id", "transaction id", "transactionid", "txn id", "txn_id"],
    "date": ["date", "transaction date"],
    "type": ["type", "transaction type"],
    "category": ["category", "vat category", "vat_category"],
    "amount": ["amount", "value"],
    "inclusive": ["inclusive", "vat inclusive", "vat_inclusive"],
}
VALID_TYPES = {"sale": "Sale", "purchase": "Purchase"}
VALID_CATEGORIES = {
    "standard": "Standard", "zero-rated": "Zero-rated", "zero rated": "Zero-rated",
    "exempt": "Exempt",
}
TRUE_STRINGS = {"true", "yes", "y", "1", "inclusive"}

# ============================================================
# LOCAL ACCOUNT STORAGE / AUTOSAVE CONFIG
# ============================================================
# Everything below lives in a hidden folder in the user's home directory so
# accounts and autosaves persist between runs without cluttering wherever
# this script happens to be launched from.

APP_DIR = os.path.join(os.path.expanduser("~"), ".zw_vat_system")
USERS_FILE = os.path.join(APP_DIR, "users.json")
AUTOSAVE_INTERVAL_MS = 60_000  # autosave every 60 seconds


def generate_salt():
    return secrets.token_hex(16)


def hash_password(password, salt):
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


# ============================================================
# VAT CALCULATION
# ============================================================

def calculate_vat(amount, vat_category, vat_inclusive):
    """
    Calculates taxable value and VAT.

    Standard-rated:
        VAT exclusive = amount x 15.5%
        VAT inclusive = amount x 15.5 / 115.5

    Zero-rated: VAT = 0
    Exempt:     VAT = 0
    """
    amount = Decimal(str(amount))

    if vat_category == "Standard":
        if vat_inclusive:
            vat = amount * VAT_RATE / (Decimal("1") + VAT_RATE)
            taxable_value = amount - vat
        else:
            taxable_value = amount
            vat = amount * VAT_RATE
    elif vat_category == "Zero-rated":
        taxable_value = amount
        vat = Decimal("0.00")
    elif vat_category == "Exempt":
        taxable_value = amount
        vat = Decimal("0.00")
    else:
        taxable_value = amount
        vat = Decimal("0.00")

    taxable_value = taxable_value.quantize(Decimal("0.01"))
    vat = vat.quantize(Decimal("0.01"))

    return taxable_value, vat


def is_valid_date(date_text):
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
        return True
    except ValueError:
        return False


# ============================================================
# LOGIN / REGISTRATION SCREEN
# ============================================================

class LoginWindow:
    """A simple local login/registration gate shown before the main app.

    Credentials are stored locally (salted + hashed) in USERS_FILE. This is
    basic access control for a shared desktop machine, not a substitute for
    real multi-user/network security.
    """

    def __init__(self, root, on_success):
        self.root = root
        self.on_success = on_success
        self.users = self.load_users()
        self.mode = "login"  # or "register"

        self.root.title("Zimbabwe VAT Return System - Login")
        self.root.geometry("440x540")
        self.root.minsize(440, 540)
        self.root.configure(bg="#17365D")

        self.build_ui()

    # ---- persistence ----
    def load_users(self):
        if os.path.exists(USERS_FILE):
            try:
                with open(USERS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def save_users(self):
        os.makedirs(APP_DIR, exist_ok=True)
        try:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.users, f, indent=2)
        except OSError:
            pass

    # ---- UI ----
    def build_ui(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        card = tk.Frame(self.root, bg="white", bd=0)
        card.place(relx=0.5, rely=0.5, anchor="center", width=360, height=460)

        logo = tk.Canvas(card, width=70, height=70, bg="white", highlightthickness=0)
        logo.pack(pady=(28, 0))
        logo.create_oval(5, 5, 65, 65, fill="#17365D", outline="")
        logo.create_text(35, 35, text="VAT", fill="white", font=("Arial", 14, "bold"))

        tk.Label(card, text="ZIMBABWE VAT SYSTEM", bg="white",
                 font=("Arial", 15, "bold"), fg="#17365D").pack(pady=(10, 0))

        self.subtitle_label = tk.Label(
            card, text="Sign in to continue", bg="white", font=("Arial", 9), fg="#666666"
        )
        self.subtitle_label.pack(pady=(2, 18))

        tk.Label(card, text="Username", bg="white", font=("Arial", 9, "bold"),
                 fg="#333333", anchor="w").pack(fill="x", padx=40)
        self.username_entry = ttk.Entry(card, font=("Arial", 11))
        self.username_entry.pack(fill="x", padx=40, pady=(2, 12))

        tk.Label(card, text="Password", bg="white", font=("Arial", 9, "bold"),
                 fg="#333333", anchor="w").pack(fill="x", padx=40)
        self.password_entry = ttk.Entry(card, show="*", font=("Arial", 11))
        self.password_entry.pack(fill="x", padx=40, pady=(2, 4))
        self.password_entry.bind("<Return>", lambda e: self.submit())

        self.error_label = tk.Label(card, text="", bg="white", fg="#B00020",
                                     font=("Arial", 8, "bold"), wraplength=280, justify="center")
        self.error_label.pack(pady=(6, 4))

        self.submit_button = ttk.Button(card, text="LOG IN", command=self.submit)
        self.submit_button.pack(fill="x", padx=40, pady=(10, 8))

        self.toggle_button = ttk.Button(card, text="Need an account? Register",
                                         command=self.toggle_mode)
        self.toggle_button.pack(fill="x", padx=40)

        tk.Label(card, text="Credentials are stored locally on this computer only.",
                 bg="white", font=("Arial", 7), fg="#999999", wraplength=280,
                 justify="center").pack(side="bottom", pady=15)

        self.username_entry.focus_set()

    def toggle_mode(self):
        self.mode = "register" if self.mode == "login" else "login"
        self.error_label.config(text="")
        if self.mode == "register":
            self.subtitle_label.config(text="Create a new account")
            self.submit_button.config(text="REGISTER")
            self.toggle_button.config(text="Already have an account? Log in")
        else:
            self.subtitle_label.config(text="Sign in to continue")
            self.submit_button.config(text="LOG IN")
            self.toggle_button.config(text="Need an account? Register")
        self.username_entry.delete(0, tk.END)
        self.password_entry.delete(0, tk.END)
        self.username_entry.focus_set()

    def submit(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        if not username or not password:
            self.error_label.config(text="Please enter a username and password.")
            return

        if self.mode == "register":
            if username in self.users:
                self.error_label.config(text="That username is already taken.")
                return
            if len(password) < 4:
                self.error_label.config(text="Password must be at least 4 characters.")
                return
            salt = generate_salt()
            self.users[username] = {"salt": salt, "hash": hash_password(password, salt)}
            self.save_users()
            self.on_success(username)
        else:
            record = self.users.get(username)
            if record is None or hash_password(password, record["salt"]) != record["hash"]:
                self.error_label.config(text="Incorrect username or password.")
                return
            self.on_success(username)


# ============================================================
# MAIN APPLICATION
# ============================================================

class VATApp:
    def __init__(self, root, username="user"):
        self.root = root
        self.username = username
        self.root.title(f"Zimbabwe VAT Return System - {username}")
        self.root.geometry("1300x820")
        self.root.minsize(1150, 700)

        self.transactions = []
        self.current_file = None  # path of the currently loaded/saved transaction file

        # Autosave setup - a per-user autosave file lives alongside the login accounts
        os.makedirs(APP_DIR, exist_ok=True)
        self.autosave_path = os.path.join(APP_DIR, f"autosave_{username}.json")
        self.autosave_enabled = tk.BooleanVar(value=True)

        self.setup_styles()
        self.create_menu()
        self.create_header()
        self.create_dashboard()
        self.create_transaction_section()
        self.create_search_bar()
        self.create_transaction_table()
        self.create_legend()
        self.create_totals_bar()
        self.create_buttons()
        self.create_status_bar()
        self.refresh_table()
        self.update_dashboard()

        # Editing/deleting via keyboard, and edit via double-click
        self.table.bind("<Double-1>", self.edit_transaction)
        self.root.bind("<Delete>", lambda event: self.delete_transaction())

        # Warn before closing if there is unsaved data
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Offer to restore an autosaved session, then start the autosave loop
        self.root.after(300, self.offer_autosave_restore)
        self.root.after(AUTOSAVE_INTERVAL_MS, self.autosave_tick)

    # ========================================================
    # STYLES
    # ========================================================
    def setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TButton", font=("Arial", 10, "bold"), padding=8)
        style.configure("Treeview", rowheight=30, font=("Arial", 10))
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"))
        style.configure("TLabelframe.Label", font=("Arial", 11, "bold"))

    # ========================================================
    # MENU BAR
    # ========================================================
    def create_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New", command=self.new_file, accelerator="Ctrl+N")
        file_menu.add_command(label="Open Transactions...", command=self.load_transactions, accelerator="Ctrl+O")
        file_menu.add_command(label="Save Transactions", command=self.save_transactions, accelerator="Ctrl+S")
        file_menu.add_command(label="Save Transactions As...", command=self.save_transactions_as)
        file_menu.add_separator()
        file_menu.add_command(label="Import Transactions (CSV/Excel)...", command=self.import_transactions)
        file_menu.add_command(label="Download Import Template (CSV)...", command=self.download_import_template)
        file_menu.add_separator()
        file_menu.add_checkbutton(label="Enable Autosave (every 60s)", variable=self.autosave_enabled)
        file_menu.add_command(label="Restore Last Autosave...", command=self.restore_autosave_manual)
        file_menu.add_separator()
        file_menu.add_command(label="Export VAT Return (CSV)...", command=self.export_vat_return_csv)
        file_menu.add_command(label="Export Audit Trail (CSV)...", command=self.export_audit_trail_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Log Out", command=self.logout)
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        reports_menu = tk.Menu(menubar, tearoff=0)
        reports_menu.add_command(label="VAT Return Summary", command=self.show_vat_return)
        reports_menu.add_command(label="Audit Trail", command=self.show_audit_trail)
        reports_menu.add_command(label="Monthly VAT Analysis", command=self.show_monthly_analysis)
        menubar.add_cascade(label="Reports", menu=reports_menu)

        self.root.config(menu=menubar)

        self.root.bind("<Control-n>", lambda e: self.new_file())
        self.root.bind("<Control-o>", lambda e: self.load_transactions())
        self.root.bind("<Control-s>", lambda e: self.save_transactions())

    def restore_autosave_manual(self):
        if not os.path.exists(self.autosave_path):
            messagebox.showinfo("No Autosave", "No autosave file was found for this account.")
            return
        answer = messagebox.askyesno(
            "Restore Autosave", "This will replace the current transactions with the last autosaved copy. Continue?"
        )
        if answer:
            self.load_transactions_from_path(self.autosave_path, is_autosave=True)

    # ========================================================
    # HEADER
    # ========================================================
    def create_header(self):
        header = tk.Frame(self.root, bg="#17365D", height=80)
        header.pack(fill="x")

        title = tk.Label(
            header, text="ZIMBABWE VAT RETURN SYSTEM",
            bg="#17365D", fg="white", font=("Arial", 22, "bold")
        )
        title.pack(side="left", padx=25, pady=15)

        right_frame = tk.Frame(header, bg="#17365D")
        right_frame.pack(side="right", padx=25, pady=10)

        tk.Label(
            right_frame, text=f"Logged in as {self.username}",
            bg="#17365D", fg="white", font=("Arial", 9, "bold")
        ).pack(side="top", anchor="e")

        tk.Label(
            right_frame, text="Automated VAT Calculation & Return",
            bg="#17365D", fg="#CFCFCF", font=("Arial", 9)
        ).pack(side="top", anchor="e")

        ttk.Button(right_frame, text="Log Out", command=self.logout).pack(side="top", anchor="e", pady=(4, 0))

    def logout(self):
        if self.transactions:
            self.autosave()
        for widget in self.root.winfo_children():
            widget.destroy()
        LoginWindow(self.root, lambda username: launch_main_app(self.root, username))

    # ========================================================
    # DASHBOARD (styled)
    # ========================================================
    def create_dashboard(self):
        outer = tk.Frame(self.root, bg="#F2F2F2")
        outer.pack(fill="x", padx=20, pady=15)

        header_row = tk.Frame(outer, bg="#F2F2F2")
        header_row.pack(fill="x", pady=(0, 8))
        tk.Label(
            header_row, text="VAT DASHBOARD", bg="#F2F2F2",
            font=("Arial", 12, "bold"), fg="#17365D"
        ).pack(side="left")
        self.dashboard_period_label = tk.Label(
            header_row, text="", bg="#F2F2F2", font=("Arial", 9), fg="#777777"
        )
        self.dashboard_period_label.pack(side="right")

        cards_row = tk.Frame(outer, bg="#F2F2F2")
        cards_row.pack(fill="x")

        self.output_card = self.create_card(cards_row, "OUTPUT VAT", "#2E7D32", "$0.00", "on sales")
        self.output_card.pack(side="left", fill="both", expand=True, padx=6)

        self.input_card = self.create_card(cards_row, "ALLOWABLE INPUT VAT", "#1565C0", "$0.00", "on purchases")
        self.input_card.pack(side="left", fill="both", expand=True, padx=6)

        self.net_card = self.create_card(cards_row, "NET VAT", "#B00020", "$0.00", "payable")
        self.net_card.pack(side="left", fill="both", expand=True, padx=6)

        self.count_card = self.create_card(cards_row, "TRANSACTIONS", "#6A1B9A", "0", "recorded")
        self.count_card.pack(side="left", fill="both", expand=True, padx=6)

        self.rate_card = self.create_card(cards_row, "EFFECTIVE VAT RATE", "#EF6C00", "0.0%", "of taxable sales")
        self.rate_card.pack(side="left", fill="both", expand=True, padx=6)

    @staticmethod
    def round_rectangle(canvas, x1, y1, x2, y2, radius=14, **kwargs):
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        return canvas.create_polygon(points, smooth=True, **kwargs)

    def create_card(self, parent, title, accent, value, subtitle):
        outer = tk.Frame(parent, bg="#F2F2F2")
        canvas = tk.Canvas(outer, height=140, bg="#F2F2F2", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        canvas.value_text = value
        canvas.subtitle_text = subtitle
        canvas.accent = accent

        def draw(event=None):
            canvas.delete("all")
            w = canvas.winfo_width() or 220
            h = canvas.winfo_height() or 140
            self.round_rectangle(canvas, 2, 2, w - 2, h - 2, radius=14, fill="white", outline="#E0E0E0")
            canvas.create_rectangle(2, 2, w - 2, 7, fill=canvas.accent, outline=canvas.accent)
            canvas.create_oval(16, 22, 30, 36, fill=canvas.accent, outline=canvas.accent)
            canvas.create_text(38, 29, text=title, font=("Arial", 9, "bold"), fill="#555555", anchor="w")
            canvas.create_text(18, 68, text=canvas.value_text, font=("Arial", 19, "bold"), fill="#1A1A1A", anchor="w")
            canvas.create_text(18, 102, text=canvas.subtitle_text, font=("Arial", 8), fill="#999999", anchor="w")

        canvas.bind("<Configure>", draw)
        outer.canvas = canvas
        outer.redraw = draw
        return outer

    def _set_card_value(self, card, value, subtitle=None, accent=None):
        canvas = card.canvas
        canvas.value_text = value
        if subtitle is not None:
            canvas.subtitle_text = subtitle
        if accent is not None:
            canvas.accent = accent
        card.redraw()

    # ========================================================
    # TRANSACTION INPUT
    # ========================================================
    def create_transaction_section(self):
        frame = ttk.LabelFrame(self.root, text=" Enter Transaction ", padding=12)
        frame.pack(fill="x", padx=20, pady=5)

        # Transaction ID
        ttk.Label(frame, text="Transaction ID").grid(row=0, column=0, padx=5, pady=3)
        self.transaction_id = ttk.Entry(frame, width=15)
        self.transaction_id.grid(row=1, column=0, padx=5)

        # Date
        ttk.Label(frame, text="Date (YYYY-MM-DD)").grid(row=0, column=1, padx=5, pady=3)
        self.date_entry = ttk.Entry(frame, width=15)
        self.date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))
        self.date_entry.grid(row=1, column=1, padx=5)

        # Transaction Type
        ttk.Label(frame, text="Transaction Type").grid(row=0, column=2, padx=5, pady=3)
        self.transaction_type = ttk.Combobox(
            frame, values=["Sale", "Purchase"], state="readonly", width=14
        )
        self.transaction_type.current(0)
        self.transaction_type.grid(row=1, column=2, padx=5)

        # VAT Category
        ttk.Label(frame, text="VAT Category").grid(row=0, column=3, padx=5, pady=3)
        self.vat_category = ttk.Combobox(
            frame, values=["Standard", "Zero-rated", "Exempt"], state="readonly", width=14
        )
        self.vat_category.current(0)
        self.vat_category.grid(row=1, column=3, padx=5)

        # Amount
        ttk.Label(frame, text="Amount").grid(row=0, column=4, padx=5, pady=3)
        self.amount_entry = ttk.Entry(frame, width=15)
        self.amount_entry.grid(row=1, column=4, padx=5)

        # VAT Inclusive
        self.inclusive = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="VAT Inclusive", variable=self.inclusive
        ).grid(row=1, column=5, padx=10)

        # Add / Update Transaction button (label changes when editing)
        self.add_button = ttk.Button(
            frame, text="ADD TRANSACTION", command=self.add_transaction
        )
        self.add_button.grid(row=1, column=6, padx=5)

        # Cancel edit button (hidden unless editing)
        self.cancel_edit_button = ttk.Button(
            frame, text="CANCEL EDIT", command=self.cancel_edit
        )
        # not gridded initially - shown only during edit mode

        # Live preview of the calculated VAT for the entered values
        self.preview_label = ttk.Label(frame, text="", font=("Arial", 9, "italic"))
        self.preview_label.grid(row=2, column=0, columnspan=7, sticky="w", padx=5, pady=(6, 0))

        self.amount_entry.bind("<KeyRelease>", self.update_preview)
        self.vat_category.bind("<<ComboboxSelected>>", self.update_preview)
        self.inclusive.trace_add("write", lambda *args: self.update_preview())

        # Tracks the transaction currently being edited (None = adding new)
        self.editing_id = None

    def update_preview(self, event=None):
        amount_text = self.amount_entry.get().strip()
        if not amount_text:
            self.preview_label.config(text="")
            return
        try:
            amount = Decimal(amount_text)
            taxable, vat = calculate_vat(amount, self.vat_category.get(), self.inclusive.get())
            self.preview_label.config(
                text=f"Preview -> Taxable value: ${taxable:,.2f}   VAT: ${vat:,.2f}"
            )
        except (InvalidOperation, ValueError):
            self.preview_label.config(text="Preview -> enter a valid amount")

    # ========================================================
    # SEARCH / FILTER BAR
    # ========================================================
    def create_search_bar(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="x", padx=20, pady=(5, 0))

        ttk.Label(frame, text="Search:").pack(side="left", padx=(0, 5))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *args: self.refresh_table())
        search_entry = ttk.Entry(frame, textvariable=self.search_var, width=25)
        search_entry.pack(side="left", padx=(0, 15))

        ttk.Label(frame, text="Type:").pack(side="left", padx=(0, 5))
        self.filter_type = ttk.Combobox(
            frame, values=["All", "Sale", "Purchase"], state="readonly", width=10
        )
        self.filter_type.current(0)
        self.filter_type.bind("<<ComboboxSelected>>", lambda e: self.refresh_table())
        self.filter_type.pack(side="left", padx=(0, 15))

        ttk.Label(frame, text="Category:").pack(side="left", padx=(0, 5))
        self.filter_category = ttk.Combobox(
            frame, values=["All", "Standard", "Zero-rated", "Exempt"], state="readonly", width=12
        )
        self.filter_category.current(0)
        self.filter_category.bind("<<ComboboxSelected>>", lambda e: self.refresh_table())
        self.filter_category.pack(side="left", padx=(0, 15))

        ttk.Button(frame, text="Clear Filters", command=self.clear_filters).pack(side="left")

    def clear_filters(self):
        self.search_var.set("")
        self.filter_type.current(0)
        self.filter_category.current(0)
        self.refresh_table()

    # ========================================================
    # TRANSACTION TABLE
    # ========================================================
    def create_transaction_table(self):
        frame = ttk.LabelFrame(self.root, text=" Transactions (double-click a row to edit) ", padding=10)
        frame.pack(fill="both", expand=True, padx=20, pady=10)

        columns = ("id", "date", "type", "category", "amount", "taxable", "vat")
        self.table = ttk.Treeview(frame, columns=columns, show="headings")

        headings = {
            "id": "Transaction ID",
            "date": "Date",
            "type": "Type",
            "category": "VAT Category",
            "amount": "Amount",
            "taxable": "Taxable Value",
            "vat": "VAT",
        }

        for column in columns:
            self.table.heading(
                column, text=headings[column],
                command=lambda c=column: self.sort_by_column(c)
            )
            self.table.column(column, width=130, anchor=COLUMN_ALIGNMENT.get(column, "center"))

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)

        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.configure_row_color_tags(self.table)

        self.sort_column = None
        self.sort_reverse = False

    def configure_row_color_tags(self, table_widget):
        """Registers a Treeview tag per (type, category) combination for color + font."""
        for (ttype, category), style in ROW_STYLES.items():
            table_widget.tag_configure(
                f"{ttype}_{category}",
                background=style["bg"], foreground=style["fg"], font=style["font"]
            )
        table_widget.tag_configure(
            "default_row",
            background=DEFAULT_ROW_STYLE["bg"], foreground=DEFAULT_ROW_STYLE["fg"],
            font=DEFAULT_ROW_STYLE["font"]
        )

    def row_tag_for(self, transaction):
        key = (transaction["type"], transaction["category"])
        return f"{transaction['type']}_{transaction['category']}" if key in ROW_STYLES else "default_row"

    def sort_by_column(self, column):
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self.refresh_table()

    # ========================================================
    # COLOR LEGEND
    # ========================================================
    def create_legend(self):
        frame = tk.Frame(self.root, bg="#FAFAFA")
        frame.pack(fill="x", padx=20, pady=(0, 4))

        tk.Label(frame, text="Legend:", bg="#FAFAFA", font=("Arial", 9, "bold")).pack(side="left", padx=(0, 8))

        for (ttype, category), style in ROW_STYLES.items():
            swatch = tk.Frame(frame, bg="#FAFAFA")
            swatch.pack(side="left", padx=6)
            tk.Frame(
                swatch, bg=style["bg"], width=14, height=14,
                highlightbackground="#999", highlightthickness=1
            ).pack(side="left")
            # Font size trimmed down slightly so bold/italic weights still fit the legend row
            legend_font = (style["font"][0], 8, style["font"][2])
            tk.Label(
                swatch, text=f" {ttype} / {category}", bg="#FAFAFA", fg=style["fg"], font=legend_font
            ).pack(side="left")

    # ========================================================
    # TOTALS BAR
    # ========================================================
    def create_totals_bar(self):
        frame = tk.Frame(self.root, bg="#EDEDED")
        frame.pack(fill="x", padx=20)
        self.totals_label = tk.Label(
            frame, text="", bg="#EDEDED", font=("Arial", 9, "bold"), anchor="w"
        )
        self.totals_label.pack(fill="x", padx=5, pady=4)

    def update_totals_bar(self, visible_transactions):
        count = len(visible_transactions)
        total_amount = sum((t["amount"] for t in visible_transactions), Decimal("0.00"))
        total_taxable = sum((t["taxable"] for t in visible_transactions), Decimal("0.00"))
        total_vat = sum((t["vat"] for t in visible_transactions), Decimal("0.00"))
        self.totals_label.config(
            text=(
                f"Showing {count} transaction(s)   |   "
                f"Total amount: ${total_amount:,.2f}   |   "
                f"Total taxable value: ${total_taxable:,.2f}   |   "
                f"Total VAT: ${total_vat:,.2f}"
            )
        )

    # ========================================================
    # STATUS BAR (with autosave indicator)
    # ========================================================
    def create_status_bar(self):
        frame = tk.Frame(self.root, bd=1, relief="sunken")
        frame.pack(side="bottom", fill="x")

        self.status_var = tk.StringVar(value="Ready. No file loaded.")
        tk.Label(frame, textvariable=self.status_var, anchor="w").pack(side="left", padx=6, pady=2)

        self.autosave_status_var = tk.StringVar(value="Autosave: waiting for first save...")
        tk.Label(frame, textvariable=self.autosave_status_var, anchor="e", fg="#555555").pack(
            side="right", padx=6, pady=2
        )

    def set_status(self, text):
        self.status_var.set(text)

    # ========================================================
    # BUTTONS
    # ========================================================
    def create_buttons(self):
        frame = tk.Frame(self.root)
        frame.pack(fill="x", padx=20, pady=(0, 15))

        ttk.Button(frame, text="CALCULATE VAT", command=self.update_dashboard).pack(side="left", padx=5)
        ttk.Button(frame, text="VAT RETURN SUMMARY", command=self.show_vat_return).pack(side="left", padx=5)
        ttk.Button(frame, text="MONTHLY ANALYSIS", command=self.show_monthly_analysis).pack(side="left", padx=5)
        ttk.Button(frame, text="AUDIT TRAIL", command=self.show_audit_trail).pack(side="left", padx=5)
        ttk.Button(frame, text="EDIT SELECTED", command=self.edit_transaction).pack(side="left", padx=5)
        ttk.Button(frame, text="DELETE SELECTED", command=self.delete_transaction).pack(side="left", padx=5)
        ttk.Button(frame, text="CLEAR ALL", command=self.clear_all).pack(side="right", padx=5)

    # ========================================================
    # VALIDATION HELPERS
    # ========================================================
    def read_and_validate_form(self):
        """Reads the input form, validates it, and returns a dict or None on failure."""
        transaction_id = self.transaction_id.get().strip()
        date = self.date_entry.get().strip()
        transaction_type = self.transaction_type.get()
        category = self.vat_category.get()
        amount_text = self.amount_entry.get().strip()

        if not transaction_id:
            messagebox.showerror("Missing Information", "Please enter a transaction ID.")
            return None

        # Prevent duplicate transaction IDs, except when editing that same transaction
        existing_ids = {
            t["id"] for t in self.transactions if t["id"] != self.editing_id
        }
        if transaction_id in existing_ids:
            messagebox.showerror(
                "Duplicate ID",
                f"Transaction ID '{transaction_id}' already exists. Please use a unique ID."
            )
            return None

        if not date:
            messagebox.showerror("Missing Information", "Please enter a date.")
            return None

        if not is_valid_date(date):
            messagebox.showerror(
                "Invalid Date", "Please enter the date in YYYY-MM-DD format, e.g. 2026-08-30."
            )
            return None

        if not amount_text:
            messagebox.showerror("Missing Information", "Please enter an amount.")
            return None

        try:
            amount = Decimal(amount_text)
            if amount < 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            messagebox.showerror("Invalid Amount", "Please enter a valid positive amount.")
            return None

        return {
            "id": transaction_id,
            "date": date,
            "type": transaction_type,
            "category": category,
            "amount": amount,
        }

    # ========================================================
    # ADD / UPDATE TRANSACTION
    # ========================================================
    def add_transaction(self):
        form = self.read_and_validate_form()
        if form is None:
            return

        taxable_value, vat = calculate_vat(form["amount"], form["category"], self.inclusive.get())

        transaction = {
            "id": form["id"],
            "date": form["date"],
            "type": form["type"],
            "category": form["category"],
            "amount": form["amount"],
            "taxable": taxable_value,
            "vat": vat,
            "inclusive": self.inclusive.get(),
        }

        if self.editing_id is not None:
            self.transactions = [
                transaction if t["id"] == self.editing_id else t
                for t in self.transactions
            ]
            self.cancel_edit()
            self.set_status(f"Transaction {form['id']} updated.")
        else:
            self.transactions.append(transaction)
            self.set_status(f"Transaction {form['id']} added.")

        self.clear_input_fields()
        self.refresh_table()
        self.update_dashboard()
        self.autosave()

    def clear_input_fields(self):
        self.transaction_id.delete(0, tk.END)
        self.amount_entry.delete(0, tk.END)
        self.preview_label.config(text="")

    # ========================================================
    # EDIT TRANSACTION
    # ========================================================
    def edit_transaction(self, event=None):
        selected = self.table.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a transaction to edit.")
            return

        item = selected[0]
        transaction_id = self.table.item(item, "values")[0]
        transaction = next((t for t in self.transactions if t["id"] == transaction_id), None)
        if transaction is None:
            return

        self.editing_id = transaction_id

        self.transaction_id.delete(0, tk.END)
        self.transaction_id.insert(0, transaction["id"])
        self.transaction_id.config(state="disabled")  # ID cannot change mid-edit

        self.date_entry.delete(0, tk.END)
        self.date_entry.insert(0, transaction["date"])

        self.transaction_type.set(transaction["type"])
        self.vat_category.set(transaction["category"])

        self.amount_entry.delete(0, tk.END)
        self.amount_entry.insert(0, str(transaction["amount"]))

        self.inclusive.set(transaction["inclusive"])

        self.add_button.config(text="UPDATE TRANSACTION")
        self.cancel_edit_button.grid(row=1, column=7, padx=5)
        self.update_preview()
        self.set_status(f"Editing transaction {transaction_id}...")

    def cancel_edit(self):
        self.editing_id = None
        self.transaction_id.config(state="normal")
        self.clear_input_fields()
        self.date_entry.delete(0, tk.END)
        self.date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))
        self.add_button.config(text="ADD TRANSACTION")
        self.cancel_edit_button.grid_forget()

    # ========================================================
    # REFRESH / FILTER TABLE
    # ========================================================
    def get_filtered_transactions(self):
        search_text = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        type_filter = self.filter_type.get() if hasattr(self, "filter_type") else "All"
        category_filter = self.filter_category.get() if hasattr(self, "filter_category") else "All"

        results = []
        for t in self.transactions:
            if search_text and search_text not in t["id"].lower() and search_text not in t["date"].lower():
                continue
            if type_filter != "All" and t["type"] != type_filter:
                continue
            if category_filter != "All" and t["category"] != category_filter:
                continue
            results.append(t)

        if self.sort_column:
            key_map = {
                "id": lambda t: t["id"],
                "date": lambda t: t["date"],
                "type": lambda t: t["type"],
                "category": lambda t: t["category"],
                "amount": lambda t: t["amount"],
                "taxable": lambda t: t["taxable"],
                "vat": lambda t: t["vat"],
            }
            results.sort(key=key_map[self.sort_column], reverse=self.sort_reverse)

        return results

    def refresh_table(self):
        for item in self.table.get_children():
            self.table.delete(item)

        visible = self.get_filtered_transactions()
        for t in visible:
            self.table.insert(
                "", "end",
                values=(
                    t["id"], t["date"], t["type"], t["category"],
                    f"${t['amount']:,.2f}", f"${t['taxable']:,.2f}", f"${t['vat']:,.2f}"
                ),
                tags=(self.row_tag_for(t),)
            )

        self.update_totals_bar(visible)

    # ========================================================
    # CALCULATE VAT SUMMARY
    # ========================================================
    def calculate_summary(self):
        standard_sales = Decimal("0.00")
        zero_rated_sales = Decimal("0.00")
        exempt_sales = Decimal("0.00")
        purchases = Decimal("0.00")
        output_vat = Decimal("0.00")
        input_vat = Decimal("0.00")

        for transaction in self.transactions:
            taxable = transaction["taxable"]
            vat = transaction["vat"]

            if transaction["type"] == "Sale":
                output_vat += vat
                if transaction["category"] == "Standard":
                    standard_sales += taxable
                elif transaction["category"] == "Zero-rated":
                    zero_rated_sales += taxable
                elif transaction["category"] == "Exempt":
                    exempt_sales += taxable
            elif transaction["type"] == "Purchase":
                purchases += taxable
                input_vat += vat

        # Adjustments currently zero.
        # These can be connected to your existing adjustment engine later.
        output_adjustment = Decimal("0.00")
        input_adjustment = Decimal("0.00")

        net_vat = (output_vat + output_adjustment) - (input_vat + input_adjustment)

        return {
            "standard_sales": standard_sales,
            "zero_rated_sales": zero_rated_sales,
            "exempt_sales": exempt_sales,
            "purchases": purchases,
            "output_vat": output_vat,
            "input_vat": input_vat,
            "output_adjustment": output_adjustment,
            "input_adjustment": input_adjustment,
            "net_vat": net_vat,
        }

    # ========================================================
    # MONTHLY VAT ANALYSIS
    # ========================================================
    def get_monthly_summary(self):
        months = {}
        for t in self.transactions:
            month_key = t["date"][:7] if len(t["date"]) >= 7 else "Unknown"
            entry = months.setdefault(month_key, {
                "sales": Decimal("0.00"), "purchases": Decimal("0.00"),
                "output_vat": Decimal("0.00"), "input_vat": Decimal("0.00"), "count": 0,
            })
            entry["count"] += 1
            if t["type"] == "Sale":
                entry["sales"] += t["taxable"]
                entry["output_vat"] += t["vat"]
            else:
                entry["purchases"] += t["taxable"]
                entry["input_vat"] += t["vat"]

        result = []
        for month_key in sorted(months.keys()):
            data = months[month_key]
            net = data["output_vat"] - data["input_vat"]
            result.append({"month": month_key, "net_vat": net, **data})
        return result

    def show_monthly_analysis(self):
        monthly = self.get_monthly_summary()

        window = tk.Toplevel(self.root)
        window.title("Monthly VAT Analysis")
        window.geometry("980x680")

        tk.Label(window, text="MONTHLY VAT ANALYSIS", font=("Arial", 18, "bold")).pack(pady=15)

        if not monthly:
            tk.Label(window, text="No transactions recorded yet.", font=("Arial", 11)).pack(pady=30)
            return

        columns = ("month", "sales", "purchases", "output_vat", "input_vat", "net_vat", "count")
        table = ttk.Treeview(window, columns=columns, show="headings", height=7)
        headings = {
            "month": "Month", "sales": "Taxable Sales", "purchases": "Taxable Purchases",
            "output_vat": "Output VAT", "input_vat": "Input VAT", "net_vat": "Net VAT", "count": "Txns",
        }
        for col in columns:
            table.heading(col, text=headings[col])
            table.column(col, width=125, anchor=COLUMN_ALIGNMENT.get(col, "center"))

        for row in monthly:
            table.insert("", "end", values=(
                row["month"], f"${row['sales']:,.2f}", f"${row['purchases']:,.2f}",
                f"${row['output_vat']:,.2f}", f"${row['input_vat']:,.2f}",
                f"${row['net_vat']:,.2f}", row["count"],
            ))
        table.pack(fill="x", padx=20, pady=(0, 15))

        chart_frame = ttk.LabelFrame(window, text=" Net VAT by Month ", padding=10)
        chart_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        canvas = tk.Canvas(chart_frame, bg="white", height=280, highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.bind("<Configure>", lambda e: self.draw_monthly_chart(canvas, monthly))

        ttk.Button(
            window, text="EXPORT MONTHLY ANALYSIS (CSV)",
            command=lambda: self.export_monthly_analysis_csv(monthly)
        ).pack(pady=10)

    def draw_monthly_chart(self, canvas, monthly):
        canvas.delete("all")
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 10 or h < 10 or not monthly:
            return

        margin_left, margin_right, margin_top, margin_bottom = 75, 20, 20, 40
        plot_w = w - margin_left - margin_right
        plot_h = h - margin_top - margin_bottom
        if plot_w <= 0 or plot_h <= 0:
            return

        values = [float(row["net_vat"]) for row in monthly]
        max_val = max(values + [0.0])
        min_val = min(values + [0.0])
        span = max(max_val - min_val, 1.0)

        zero_y = margin_top + plot_h * (max_val / span)

        canvas.create_line(margin_left, margin_top, margin_left, margin_top + plot_h, fill="#CCCCCC")
        canvas.create_line(margin_left, zero_y, margin_left + plot_w, zero_y, fill="#CCCCCC")

        n = len(monthly)
        step = plot_w / n
        bar_width = max(step * 0.55, 10)

        for i, row in enumerate(monthly):
            val = float(row["net_vat"])
            x_center = margin_left + step * i + step / 2
            bar_h = plot_h * (abs(val) / span)
            color = "#B00020" if val >= 0 else "#1B5E20"
            if val >= 0:
                y0, y1 = zero_y - bar_h, zero_y
            else:
                y0, y1 = zero_y, zero_y + bar_h
            canvas.create_rectangle(
                x_center - bar_width / 2, y0, x_center + bar_width / 2, y1,
                fill=color, outline=""
            )
            canvas.create_text(x_center, h - margin_bottom + 15, text=row["month"], font=("Arial", 8))
            label_y = y0 - 10 if val >= 0 else y1 + 10
            canvas.create_text(x_center, label_y, text=f"${val:,.0f}", font=("Arial", 7))

        canvas.create_text(margin_left - 10, margin_top, text=f"${max_val:,.0f}", font=("Arial", 7), anchor="e")
        canvas.create_text(margin_left - 10, margin_top + plot_h, text=f"${min_val:,.0f}", font=("Arial", 7), anchor="e")

    def export_monthly_analysis_csv(self, monthly):
        if not monthly:
            messagebox.showinfo("Nothing to Export", "There is no monthly data to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Monthly VAT Analysis"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Month", "Taxable Sales", "Taxable Purchases", "Output VAT", "Input VAT", "Net VAT", "Transactions"])
                for row in monthly:
                    writer.writerow([
                        row["month"], f"{row['sales']:.2f}", f"{row['purchases']:.2f}",
                        f"{row['output_vat']:.2f}", f"{row['input_vat']:.2f}",
                        f"{row['net_vat']:.2f}", row["count"],
                    ])
            self.set_status(f"Monthly analysis exported to {path}")
            messagebox.showinfo("Export Complete", f"Monthly VAT analysis exported to:\n{path}")
        except OSError as error:
            messagebox.showerror("Export Failed", f"Could not export file:\n{error}")

    # ========================================================
    # UPDATE DASHBOARD (styled)
    # ========================================================
    def update_dashboard(self):
        summary = self.calculate_summary()

        output_vat = summary["output_vat"]
        input_vat = summary["input_vat"]
        net_vat = summary["net_vat"]
        total_taxable_sales = (
            summary["standard_sales"] + summary["zero_rated_sales"] + summary["exempt_sales"]
        )
        effective_rate = (
            (output_vat / total_taxable_sales * 100) if total_taxable_sales else Decimal("0.00")
        )

        self._set_card_value(self.output_card, f"${output_vat:,.2f}")
        self._set_card_value(self.input_card, f"${input_vat:,.2f}")

        if net_vat >= 0:
            self._set_card_value(self.net_card, f"${net_vat:,.2f}", subtitle="payable", accent="#B00020")
        else:
            self._set_card_value(self.net_card, f"${abs(net_vat):,.2f}", subtitle="refundable", accent="#1B5E20")

        self._set_card_value(self.count_card, str(len(self.transactions)))
        self._set_card_value(self.rate_card, f"{effective_rate:.1f}%")

        self.dashboard_period_label.config(text=f"As of {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ========================================================
    # VAT RETURN SUMMARY
    # ========================================================
    def show_vat_return(self):
        summary = self.calculate_summary()

        window = tk.Toplevel(self.root)
        window.title("VAT Return Summary")
        window.geometry("650x680")

        title = tk.Label(window, text="ZIMBABWE VAT RETURN SUMMARY", font=("Arial", 20, "bold"))
        title.pack(pady=20)

        frame = tk.Frame(window)
        frame.pack(fill="both", expand=True, padx=35)

        rows = [
            ("Standard-rated sales", summary["standard_sales"]),
            ("Zero-rated sales", summary["zero_rated_sales"]),
            ("Exempt sales", summary["exempt_sales"]),
            ("Purchases", summary["purchases"]),
            ("Output VAT", summary["output_vat"]),
            ("Allowable input VAT", summary["input_vat"]),
            ("Output VAT adjustments", summary["output_adjustment"]),
            ("Input VAT adjustments", summary["input_adjustment"]),
        ]

        for label, value in rows:
            row = tk.Frame(frame)
            row.pack(fill="x", pady=7)
            tk.Label(row, text=label, font=("Arial", 11)).pack(side="left")
            tk.Label(row, text=f"${value:,.2f}", font=("Arial", 11, "bold")).pack(side="right")

        separator = tk.Frame(frame, height=2, bg="black")
        separator.pack(fill="x", pady=15)

        net = summary["net_vat"]
        if net >= 0:
            result = f"NET VAT PAYABLE: ${net:,.2f}"
        else:
            result = f"NET VAT REFUNDABLE: ${abs(net):,.2f}"

        tk.Label(frame, text=result, font=("Arial", 17, "bold")).pack(pady=20)
        tk.Label(frame, text="VAT rate used by this application: 15.5%", font=("Arial", 9)).pack(pady=5)

        ttk.Button(
            frame, text="EXPORT TO CSV", command=self.export_vat_return_csv
        ).pack(pady=10)

    # ========================================================
    # AUDIT TRAIL
    # ========================================================
    def show_audit_trail(self):
        window = tk.Toplevel(self.root)
        window.title("VAT Audit Trail")
        window.geometry("1050x600")

        tk.Label(window, text="VAT CALCULATION AUDIT TRAIL", font=("Arial", 18, "bold")).pack(pady=15)

        columns = ("id", "type", "category", "amount", "taxable", "vat", "calculation")
        table = ttk.Treeview(window, columns=columns, show="headings")

        headings = {
            "id": "ID",
            "type": "Type",
            "category": "VAT Category",
            "amount": "Amount",
            "taxable": "Taxable Value",
            "vat": "VAT",
            "calculation": "Calculation",
        }

        for column in columns:
            table.heading(column, text=headings[column])
            table.column(column, width=130, anchor=COLUMN_ALIGNMENT.get(column, "center"))

        self.configure_row_color_tags(table)

        for transaction in self.transactions:
            amount = transaction["amount"]
            category = transaction["category"]
            vat = transaction["vat"]
            taxable = transaction["taxable"]

            if category == "Standard":
                if transaction["inclusive"]:
                    calculation = f"{amount} x 15.5 / 115.5"
                else:
                    calculation = f"{amount} x 15.5%"
            else:
                calculation = "VAT = 0"

            table.insert(
                "", "end",
                values=(
                    transaction["id"], transaction["type"], category,
                    f"${amount:,.2f}", f"${taxable:,.2f}", f"${vat:,.2f}", calculation
                ),
                tags=(self.row_tag_for(transaction),)
            )

        table.pack(fill="both", expand=True, padx=15, pady=10)

        ttk.Button(
            window, text="EXPORT TO CSV", command=self.export_audit_trail_csv
        ).pack(pady=10)

    # ========================================================
    # DELETE TRANSACTION
    # ========================================================
    def delete_transaction(self):
        selected = self.table.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a transaction first.")
            return

        item = selected[0]
        values = self.table.item(item, "values")
        transaction_id = values[0]

        confirmed = messagebox.askyesno(
            "Confirm Delete", f"Delete transaction '{transaction_id}'? This cannot be undone."
        )
        if not confirmed:
            return

        self.transactions = [
            transaction for transaction in self.transactions
            if transaction["id"] != transaction_id
        ]

        if self.editing_id == transaction_id:
            self.cancel_edit()

        self.refresh_table()
        self.update_dashboard()
        self.autosave()
        self.set_status(f"Transaction {transaction_id} deleted.")

    # ========================================================
    # CLEAR ALL
    # ========================================================
    def clear_all(self):
        if not self.transactions:
            messagebox.showinfo("Nothing to Clear", "There are no transactions.")
            return

        answer = messagebox.askyesno(
            "Clear All Transactions", "Are you sure you want to delete all transactions?"
        )
        if not answer:
            return

        self.transactions.clear()
        self.cancel_edit()
        self.refresh_table()
        self.update_dashboard()
        self.autosave()
        self.set_status("All transactions cleared.")

    # ========================================================
    # SAVE / LOAD
    # ========================================================
    def transactions_to_serializable(self):
        serializable = []
        for t in self.transactions:
            serializable.append({
                "id": t["id"],
                "date": t["date"],
                "type": t["type"],
                "category": t["category"],
                "amount": str(t["amount"]),
                "taxable": str(t["taxable"]),
                "vat": str(t["vat"]),
                "inclusive": t["inclusive"],
            })
        return serializable

    def new_file(self):
        if self.transactions:
            answer = messagebox.askyesno(
                "New File", "This will clear all current transactions. Continue?"
            )
            if not answer:
                return
        self.transactions.clear()
        self.current_file = None
        self.cancel_edit()
        self.refresh_table()
        self.update_dashboard()
        self.set_status("New file. No file loaded.")

    def save_transactions(self):
        if self.current_file is None:
            self.save_transactions_as()
            return
        self._write_transactions_to_path(self.current_file)

    def save_transactions_as(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Save Transactions As"
        )
        if not path:
            return
        self._write_transactions_to_path(path)

    def _write_transactions_to_path(self, path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.transactions_to_serializable(), f, indent=2)
            self.current_file = path
            self.set_status(f"Saved {len(self.transactions)} transaction(s) to {path}")
        except OSError as error:
            messagebox.showerror("Save Failed", f"Could not save file:\n{error}")

    def load_transactions(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Open Transactions"
        )
        if not path:
            return
        self.load_transactions_from_path(path)

    def load_transactions_from_path(self, path, is_autosave=False):
        """Shared loader used by File > Open and by autosave restore."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as error:
            messagebox.showerror("Load Failed", f"Could not read file:\n{error}")
            return False

        loaded = []
        try:
            for item in data:
                loaded.append({
                    "id": item["id"],
                    "date": item["date"],
                    "type": item["type"],
                    "category": item["category"],
                    "amount": Decimal(item["amount"]),
                    "taxable": Decimal(item["taxable"]),
                    "vat": Decimal(item["vat"]),
                    "inclusive": bool(item["inclusive"]),
                })
        except (KeyError, InvalidOperation) as error:
            messagebox.showerror("Load Failed", f"File format is not recognized:\n{error}")
            return False

        self.transactions = loaded
        if not is_autosave:
            self.current_file = path
        self.cancel_edit()
        self.refresh_table()
        self.update_dashboard()
        self.set_status(f"Loaded {len(self.transactions)} transaction(s) from {path}")
        return True

    # ========================================================
    # AUTOMATED SAVING
    # ========================================================
    def offer_autosave_restore(self):
        if os.path.exists(self.autosave_path):
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(self.autosave_path)).strftime("%Y-%m-%d %H:%M:%S")
            except OSError:
                mtime = "an earlier session"
            answer = messagebox.askyesno(
                "Restore Autosaved Session",
                f"An autosaved session from {mtime} was found for '{self.username}'.\n"
                "Restore it now?"
            )
            if answer:
                self.load_transactions_from_path(self.autosave_path, is_autosave=True)

    def autosave_tick(self):
        if self.autosave_enabled.get():
            self.autosave()
        self.root.after(AUTOSAVE_INTERVAL_MS, self.autosave_tick)

    def autosave(self):
        """Silently writes the current transactions to the autosave file, and
        also to the active save file (if any) so File > Save stays in sync.
        """
        try:
            with open(self.autosave_path, "w", encoding="utf-8") as f:
                json.dump(self.transactions_to_serializable(), f, indent=2)
            if self.current_file:
                with open(self.current_file, "w", encoding="utf-8") as f:
                    json.dump(self.transactions_to_serializable(), f, indent=2)
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.autosave_status_var.set(f"Autosave: last saved at {timestamp}")
        except OSError:
            self.autosave_status_var.set("Autosave: failed to write file")

    # ========================================================
    # BULK IMPORT: CSV / EXCEL
    # ========================================================
    def import_transactions(self):
        path = filedialog.askopenfilename(
            filetypes=[
                ("CSV and Excel files", "*.csv *.xlsx"),
                ("CSV files", "*.csv"),
                ("Excel files", "*.xlsx"),
                ("All files", "*.*"),
            ],
            title="Import Transactions (CSV/Excel)"
        )
        if not path:
            return

        try:
            if path.lower().endswith(".xlsx"):
                raw_rows = self.read_xlsx_rows(path)
            elif path.lower().endswith(".csv"):
                raw_rows = self.read_csv_rows(path)
            else:
                messagebox.showerror(
                    "Unsupported File",
                    "Please choose a .csv or .xlsx file."
                )
                return
        except ImportError as error:
            messagebox.showerror("Missing Dependency", str(error))
            return
        except OSError as error:
            messagebox.showerror("Import Failed", f"Could not read file:\n{error}")
            return

        if raw_rows is None:
            return  # error already shown by the reader

        if not raw_rows:
            messagebox.showinfo("Nothing to Import", "The file has no data rows.")
            return

        new_transactions, errors = self.build_transactions_from_rows(raw_rows)

        if new_transactions:
            self.transactions.extend(new_transactions)
            self.cancel_edit()
            self.refresh_table()
            self.update_dashboard()
            self.autosave()
            self.set_status(
                f"Imported {len(new_transactions)} transaction(s) from {path}"
                + (f" ({len(errors)} skipped)" if errors else "")
            )

        self.show_import_report(path, len(raw_rows), len(new_transactions), errors)

    def read_csv_rows(self, path):
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                messagebox.showerror("Import Failed", "The CSV file has no header row.")
                return None
            return [dict(row) for row in reader]

    def read_xlsx_rows(self, path):
        if openpyxl is not None:
            workbook = openpyxl.load_workbook(path, data_only=True)
            sheet = workbook.active

            rows_iter = sheet.iter_rows(values_only=True)
            try:
                header = next(rows_iter)
            except StopIteration:
                messagebox.showerror("Import Failed", "The Excel file is empty.")
                return None

            header = [str(h).strip() if h is not None else "" for h in header]
            raw_rows = []
            for row in rows_iter:
                if row is None or all(cell is None for cell in row):
                    continue  # skip fully blank rows
                raw_rows.append({header[i]: row[i] for i in range(len(header)) if i < len(row)})
            return raw_rows

        # openpyxl isn't installed - fall back to the stdlib-only reader above.
        try:
            raw_rows = read_xlsx_stdlib(path)
        except (zipfile.BadZipFile, ET.ParseError, ValueError) as error:
            messagebox.showerror("Import Failed", f"Could not read Excel file:\n{error}")
            return None

        if not raw_rows:
            messagebox.showerror("Import Failed", "The Excel file has no data rows.")
            return None

        return raw_rows

    def normalize_import_row(self, raw_row):
        """Maps a raw {header: value} row onto canonical field names using IMPORT_COLUMN_ALIASES."""
        normalized = {}
        lookup = {k.strip().lower(): v for k, v in raw_row.items() if k}
        for canonical, aliases in IMPORT_COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in lookup:
                    value = lookup[alias]
                    normalized[canonical] = str(value).strip() if value is not None else ""
                    break
        return normalized

    def build_transactions_from_rows(self, raw_rows):
        """Validates and converts raw import rows into transaction dicts.
        Returns (new_transactions, errors) where errors is a list of
        (row_number, reason) tuples for rows that were skipped.
        """
        new_transactions = []
        errors = []
        seen_ids = {t["id"] for t in self.transactions}

        for index, raw_row in enumerate(raw_rows, start=2):  # row 1 is the header
            row = self.normalize_import_row(raw_row)

            transaction_id = row.get("id", "").strip()
            date = row.get("date", "").strip()
            type_raw = row.get("type", "").strip().lower()
            category_raw = row.get("category", "").strip().lower()
            amount_text = row.get("amount", "").strip()
            inclusive_text = row.get("inclusive", "").strip().lower()

            if not transaction_id:
                errors.append((index, "Missing transaction ID"))
                continue
            if transaction_id in seen_ids:
                errors.append((index, f"Duplicate transaction ID '{transaction_id}'"))
                continue
            if not date or not is_valid_date(date):
                errors.append((index, f"Invalid or missing date (use YYYY-MM-DD): '{date}'"))
                continue
            if type_raw not in VALID_TYPES:
                errors.append((index, f"Invalid transaction type: '{row.get('type', '')}' (use Sale or Purchase)"))
                continue
            if category_raw not in VALID_CATEGORIES:
                errors.append((
                    index,
                    f"Invalid VAT category: '{row.get('category', '')}' "
                    "(use Standard, Zero-rated, or Exempt)"
                ))
                continue
            if not amount_text:
                errors.append((index, "Missing amount"))
                continue

            try:
                amount = Decimal(amount_text)
                if amount < 0:
                    raise ValueError
            except (InvalidOperation, ValueError):
                errors.append((index, f"Invalid amount: '{amount_text}'"))
                continue

            inclusive = inclusive_text in TRUE_STRINGS
            transaction_type = VALID_TYPES[type_raw]
            category = VALID_CATEGORIES[category_raw]

            taxable_value, vat = calculate_vat(amount, category, inclusive)

            new_transactions.append({
                "id": transaction_id,
                "date": date,
                "type": transaction_type,
                "category": category,
                "amount": amount,
                "taxable": taxable_value,
                "vat": vat,
                "inclusive": inclusive,
            })
            seen_ids.add(transaction_id)

        return new_transactions, errors

    def show_import_report(self, path, total_rows, imported_count, errors):
        window = tk.Toplevel(self.root)
        window.title("Import Report")
        window.geometry("600x450")

        tk.Label(window, text="IMPORT REPORT", font=("Arial", 16, "bold")).pack(pady=15)
        tk.Label(window, text=path, font=("Arial", 9), wraplength=560).pack(pady=(0, 10))

        summary = f"{imported_count} of {total_rows} row(s) imported successfully."
        if errors:
            summary += f"  {len(errors)} row(s) skipped."
        tk.Label(window, text=summary, font=("Arial", 11, "bold")).pack(pady=(0, 10))

        if errors:
            frame = tk.Frame(window)
            frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))

            text = tk.Text(frame, wrap="word", font=("Courier", 9))
            scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=scrollbar.set)

            for row_number, reason in errors:
                text.insert("end", f"Row {row_number}: {reason}\n")
            text.config(state="disabled")

            text.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
        else:
            tk.Label(window, text="No errors. All rows imported cleanly.", font=("Arial", 10)).pack(pady=10)

        ttk.Button(window, text="Close", command=window.destroy).pack(pady=10)

    def download_import_template(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="transaction_import_template.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Save Import Template"
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["ID", "Date", "Type", "Category", "Amount", "Inclusive"])
                writer.writerow(["INV-001", "2026-08-01", "Sale", "Standard", "1000.00", "No"])
                writer.writerow(["PUR-001", "2026-08-03", "Purchase", "Standard", "500.00", "Yes"])
                writer.writerow(["INV-002", "2026-08-05", "Sale", "Zero-rated", "250.00", "No"])
            self.set_status(f"Import template saved to {path}")
            messagebox.showinfo(
                "Template Saved",
                f"Template saved to:\n{path}\n\n"
                "Fill it in and use File > Import Transactions to load it. "
                "The same column layout works for an .xlsx file too."
            )
        except OSError as error:
            messagebox.showerror("Save Failed", f"Could not save template:\n{error}")

    def on_close(self):
        if self.transactions:
            self.autosave()
        if self.transactions and self.current_file is None:
            answer = messagebox.askyesnocancel(
                "Unsaved Transactions",
                "You have unsaved transactions. Save to a file before exiting?\n"
                "(An autosave copy has already been kept for this account.)"
            )
            if answer is None:
                return  # cancel closing
            if answer:
                self.save_transactions()
        self.root.destroy()

    # ========================================================
    # EXPORT
    # ========================================================
    def export_vat_return_csv(self):
        summary = self.calculate_summary()
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export VAT Return Summary"
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Zimbabwe VAT Return Summary"])
                writer.writerow(["Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
                writer.writerow([])
                writer.writerow(["Item", "Amount"])
                writer.writerow(["Standard-rated sales", f"{summary['standard_sales']:.2f}"])
                writer.writerow(["Zero-rated sales", f"{summary['zero_rated_sales']:.2f}"])
                writer.writerow(["Exempt sales", f"{summary['exempt_sales']:.2f}"])
                writer.writerow(["Purchases", f"{summary['purchases']:.2f}"])
                writer.writerow(["Output VAT", f"{summary['output_vat']:.2f}"])
                writer.writerow(["Allowable input VAT", f"{summary['input_vat']:.2f}"])
                writer.writerow(["Output VAT adjustments", f"{summary['output_adjustment']:.2f}"])
                writer.writerow(["Input VAT adjustments", f"{summary['input_adjustment']:.2f}"])
                writer.writerow(["Net VAT", f"{summary['net_vat']:.2f}"])
            self.set_status(f"VAT return summary exported to {path}")
            messagebox.showinfo("Export Complete", f"VAT return summary exported to:\n{path}")
        except OSError as error:
            messagebox.showerror("Export Failed", f"Could not export file:\n{error}")

    def export_audit_trail_csv(self):
        if not self.transactions:
            messagebox.showinfo("Nothing to Export", "There are no transactions to export.")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Audit Trail"
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["ID", "Date", "Type", "Category", "Amount", "Taxable Value", "VAT", "Calculation"])
                for transaction in self.transactions:
                    amount = transaction["amount"]
                    category = transaction["category"]
                    if category == "Standard":
                        calculation = (
                            f"{amount} x 15.5 / 115.5" if transaction["inclusive"] else f"{amount} x 15.5%"
                        )
                    else:
                        calculation = "VAT = 0"
                    writer.writerow([
                        transaction["id"], transaction["date"], transaction["type"], category,
                        f"{amount:.2f}", f"{transaction['taxable']:.2f}", f"{transaction['vat']:.2f}",
                        calculation
                    ])
            self.set_status(f"Audit trail exported to {path}")
            messagebox.showinfo("Export Complete", f"Audit trail exported to:\n{path}")
        except OSError as error:
            messagebox.showerror("Export Failed", f"Could not export file:\n{error}")


# ============================================================
# START PROGRAM
# ============================================================
def launch_main_app(root, username):
    for widget in root.winfo_children():
        widget.destroy()
    root.geometry("1300x820")
    VATApp(root, username)


if __name__ == "__main__":
    root = tk.Tk()
    LoginWindow(root, lambda username: launch_main_app(root, username))
    root.mainloop()
