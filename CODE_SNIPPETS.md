# Code Snippets - Built-in Credentials Feature

## Complete Feature Code

### Section 1: Configuration (Added after `is_valid_date()` function)

```python
# ============================================================
# BUILT-IN CREDENTIALS (STORED IN CODE)
# ============================================================
# Built-in demo/default accounts that are hardcoded in the application.
# Set ENABLE_BUILTIN_CREDENTIALS to True to use these accounts.
# Format: "username": "plaintext_password"
# WARNING: Storing plaintext passwords in code is not secure for production!
# Use only for testing, demos, or development purposes.

ENABLE_BUILTIN_CREDENTIALS = True

BUILTIN_CREDENTIALS = {
    "demo": "demo123",           # Demo account for testing
    "admin": "admin123",         # Admin account
    "test": "test123",           # Test account
    "user": "password123",       # Default user account
}


def verify_builtin_credentials(username, password):
    """
    Verifies username and password against built-in credentials stored in code.
    
    Returns True if credentials match a built-in account, False otherwise.
    Only works if ENABLE_BUILTIN_CREDENTIALS is True.
    """
    if not ENABLE_BUILTIN_CREDENTIALS:
        return False
    
    return username in BUILTIN_CREDENTIALS and BUILTIN_CREDENTIALS[username] == password


def get_all_credentials():
    """
    Returns a dictionary of all available credentials (both built-in and file-based).
    Useful for administrative purposes or credential listing.
    """
    all_creds = {}
    
    if ENABLE_BUILTIN_CREDENTIALS:
        all_creds["builtin"] = BUILTIN_CREDENTIALS.copy()
    
    # Load file-based credentials
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                file_creds = json.load(f)
                all_creds["file_based"] = {k: v.get("hash", "***") for k, v in file_creds.items()}
        except (OSError, json.JSONDecodeError):
            pass
    
    return all_creds
```

---

### Section 2: Enhanced Login UI (Modified in `LoginWindow.build_ui()`)

```python
# Display credential storage information
cred_info = "Credentials are stored locally on this computer only."
if ENABLE_BUILTIN_CREDENTIALS:
    cred_info += f"\nBuilt-in accounts available: {', '.join(BUILTIN_CREDENTIALS.keys())}"

tk.Label(card, text=cred_info,
         bg="white", font=("Arial", 7), fg="#999999", wraplength=280,
         justify="center").pack(side="bottom", pady=15)
```

---

### Section 3: Enhanced Authentication (Modified in `LoginWindow.submit()`)

```python
def submit(self):
    username = self.username_entry.get().strip()
    password = self.password_entry.get()

    if not username or not password:
        self.error_label.config(text="Please enter a username and password.")
        return

    if self.mode == "register":
        # Check if trying to register with a built-in account
        if ENABLE_BUILTIN_CREDENTIALS and username in BUILTIN_CREDENTIALS:
            self.error_label.config(text="That username is reserved (built-in account).")
            return
        
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
        # Try built-in credentials first
        if verify_builtin_credentials(username, password):
            self.on_success(username)
            return
        
        # Fall back to file-based credentials
        record = self.users.get(username)
        if record is None or hash_password(password, record["salt"]) != record["hash"]:
            self.error_label.config(text="Incorrect username or password.")
            return
        self.on_success(username)
```

---

### Section 4: Settings Menu (Modified in `VATApp.create_menu()`)

```python
# Settings menu for credential management
settings_menu = tk.Menu(menubar, tearoff=0)
settings_menu.add_command(label="View Credentials Info", command=self.show_credentials_info)
settings_menu.add_command(label="Manage Built-in Credentials", command=self.manage_builtin_credentials)
menubar.add_cascade(label="Settings", menu=settings_menu)
```

---

### Section 5: Credential Management Methods (Added to `VATApp` class)

```python
# ========================================================
# CREDENTIAL MANAGEMENT (Settings Menu)
# ========================================================
def show_credentials_info(self):
    """Display information about all available credentials (built-in and file-based)."""
    window = tk.Toplevel(self.root)
    window.title("Credentials Information")
    window.geometry("500x400")

    tk.Label(window, text="CREDENTIAL STORAGE INFORMATION", font=("Arial", 14, "bold")).pack(pady=15)

    # Get all credentials info
    creds = get_all_credentials()
    
    info_frame = tk.Frame(window)
    info_frame.pack(fill="both", expand=True, padx=20, pady=10)

    # Built-in credentials
    tk.Label(info_frame, text="Built-in Credentials (Stored in Code):", 
            font=("Arial", 11, "bold"), fg="#17365D").pack(anchor="w", pady=(10, 5))
    
    if ENABLE_BUILTIN_CREDENTIALS:
        builtin_text = f"Status: ENABLED\nAccounts: {', '.join(BUILTIN_CREDENTIALS.keys())}"
        tk.Label(info_frame, text=builtin_text, font=("Arial", 9), 
                bg="#E8F5E9", fg="#1B5E20", justify="left", padx=10, pady=8).pack(fill="x", pady=5)
    else:
        tk.Label(info_frame, text="Status: DISABLED", font=("Arial", 9), 
                bg="#FFEBEE", fg="#B00020", justify="left", padx=10, pady=8).pack(fill="x", pady=5)

    # File-based credentials
    tk.Label(info_frame, text="File-based Credentials (User Registered):", 
            font=("Arial", 11, "bold"), fg="#17365D").pack(anchor="w", pady=(15, 5))
    
    if os.path.exists(USERS_FILE):
        users = self.load_users()
        if users:
            users_text = f"Total users: {len(users)}\nUsernames: {', '.join(users.keys())}"
            tk.Label(info_frame, text=users_text, font=("Arial", 9), 
                    bg="#E3F2FD", fg="#0D47A1", justify="left", padx=10, pady=8).pack(fill="x", pady=5)
        else:
            tk.Label(info_frame, text="No registered users yet.", font=("Arial", 9), 
                    bg="#F5F5F5", fg="#666666", justify="left", padx=10, pady=8).pack(fill="x", pady=5)
    else:
        tk.Label(info_frame, text="Users file not created yet.", font=("Arial", 9), 
                bg="#F5F5F5", fg="#666666", justify="left", padx=10, pady=8).pack(fill="x", pady=5)

    # Storage location info
    tk.Label(info_frame, text="Storage Location:", 
            font=("Arial", 11, "bold"), fg="#17365D").pack(anchor="w", pady=(15, 5))
    
    storage_info = f"File-based credentials: {USERS_FILE}\n\nBuilt-in credentials are stored directly in the Python code."
    tk.Label(info_frame, text=storage_info, font=("Arial", 8, "italic"), 
            justify="left", fg="#666666").pack(anchor="w", pady=5)

    ttk.Button(window, text="Close", command=window.destroy).pack(pady=10)

def load_users(self):
    """Load registered users from file (used by credentials info)."""
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}

def manage_builtin_credentials(self):
    """Allow viewing and modifying built-in credentials."""
    if not ENABLE_BUILTIN_CREDENTIALS:
        messagebox.showinfo("Built-in Credentials Disabled", 
                           "Built-in credentials are currently disabled.\nSet ENABLE_BUILTIN_CREDENTIALS = True in the code to enable.")
        return

    window = tk.Toplevel(self.root)
    window.title("Manage Built-in Credentials")
    window.geometry("550x500")

    tk.Label(window, text="MANAGE BUILT-IN CREDENTIALS", font=("Arial", 14, "bold")).pack(pady=15)

    info_label = tk.Label(window, 
        text="⚠ WARNING: Built-in credentials are stored as plaintext in the code.\nUse only for testing/demo purposes, not production.",
        font=("Arial", 9), fg="#B00020", wraplength=500, justify="center")
    info_label.pack(pady=10)

    # Credentials list
    frame = ttk.LabelFrame(window, text=" Current Built-in Accounts ", padding=10)
    frame.pack(fill="both", expand=True, padx=15, pady=10)

    # Create a scrollable text area showing current credentials
    text_widget = tk.Text(frame, height=12, width=60, font=("Courier", 9), relief="solid", bd=1)
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
    text_widget.configure(yscrollcommand=scrollbar.set)

    credentials_text = "Current Built-in Credentials:\n" + "=" * 50 + "\n\n"
    for username, password in BUILTIN_CREDENTIALS.items():
        credentials_text += f"Username: {username}\nPassword: {password}\n\n"

    text_widget.insert("1.0", credentials_text)
    text_widget.config(state="disabled")

    text_widget.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    button_frame = tk.Frame(window)
    button_frame.pack(fill="x", padx=15, pady=10)

    def copy_to_clipboard():
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(credentials_text)
            messagebox.showinfo("Copied", "Credentials copied to clipboard!")
        except Exception as e:
            messagebox.showerror("Error", f"Could not copy to clipboard:\n{e}")

    ttk.Button(button_frame, text="Copy All to Clipboard", command=copy_to_clipboard).pack(side="left", padx=5)
    ttk.Button(button_frame, text="Close", command=window.destroy).pack(side="left", padx=5)

    info_label2 = tk.Label(window, 
        text="To modify credentials, edit the BUILTIN_CREDENTIALS dictionary at the top of the code.",
        font=("Arial", 8, "italic"), fg="#666666", wraplength=500, justify="center")
    info_label2.pack(pady=5)
```

---

## Key Changes Overview

| Change | Type | File Line | Purpose |
|--------|------|-----------|---------|
| `ENABLE_BUILTIN_CREDENTIALS` | Config | ~255 | Toggle built-in credentials on/off |
| `BUILTIN_CREDENTIALS` | Dictionary | ~257 | Store demo account credentials |
| `verify_builtin_credentials()` | Function | ~278 | Authenticate built-in accounts |
| `get_all_credentials()` | Function | ~288 | List all available credentials |
| Login UI update | UI | ~395 | Display available accounts |
| Enhanced `submit()` | Method | ~430 | Check built-in first, then file-based |
| Settings menu | Menu | ~560 | Add credential management options |
| `show_credentials_info()` | Method | ~1330 | Display credential information |
| `manage_builtin_credentials()` | Method | ~1380 | Manage built-in accounts UI |

---

## Integration Points

1. **Authentication Flow:**
   ```
   User enters credentials
   → Check built-in credentials (if enabled)
   → If not found, check file-based credentials
   → Return result
   ```

2. **Registration Flow:**
   ```
   User tries to register
   → Check if username is reserved (built-in)
   → If reserved, show error
   → If free, create new account normally
   ```

3. **Settings Access:**
   ```
   User clicks Settings menu
   → Choose "View Credentials Info" or "Manage Built-in Credentials"
   → Display appropriate window
   ```

---

## Testing Checklist

- [ ] Syntax validation passed
- [ ] Application launches without errors
- [ ] Login with demo account works
- [ ] Settings menu appears after login
- [ ] Credentials info window displays correctly
- [ ] Can copy credentials to clipboard
- [ ] Registration prevents built-in username
- [ ] Can toggle ENABLE_BUILTIN_CREDENTIALS

---

## Files Affected

1. `5.py` - Main application file (modified)
2. `BUILTIN_CREDENTIALS_GUIDE.md` - User guide (created)
3. `IMPLEMENTATION_SUMMARY.md` - Summary (created)
4. `CODE_SNIPPETS.md` - This file (created)

---

**All changes are backward compatible and tested for syntax errors!**
