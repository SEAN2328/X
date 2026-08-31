# Built-in Credentials Feature Guide

## Overview
A new feature has been added to store and manage login credentials directly within the Python code. This allows you to define demo accounts, test users, or quick-access credentials without relying on external files.

---

## Key Features

### 1. **Built-in Credentials Storage**
Credentials are defined in a dictionary at the top of the code:

```python
BUILTIN_CREDENTIALS = {
    "demo": "demo123",           # Demo account for testing
    "admin": "admin123",         # Admin account
    "test": "test123",           # Test account
    "user": "password123",       # Default user account
}
```

### 2. **Enable/Disable Control**
Toggle built-in credentials with a simple flag:

```python
ENABLE_BUILTIN_CREDENTIALS = True  # Set to False to disable
```

### 3. **Dual Authentication System**
The login system now checks credentials in this order:
1. **Built-in credentials** (if enabled) - checked first for quick authentication
2. **File-based credentials** - registered users stored in JSON files

---

## Configuration

### Adding New Built-in Accounts
Edit the `BUILTIN_CREDENTIALS` dictionary:

```python
BUILTIN_CREDENTIALS = {
    "demo": "demo123",
    "accountant": "secure123",      # Add your account
    "finance_manager": "finance456", # Add another account
}
```

### Disabling Built-in Credentials
Set the flag to `False`:

```python
ENABLE_BUILTIN_CREDENTIALS = False
```

---

## Usage

### Login with Built-in Account
1. Run the application
2. Enter any built-in username and password
3. Click "LOG IN"

### Viewing Credentials Information
1. After logging in, go to **Settings → View Credentials Info**
2. See all available credentials (built-in and registered users)
3. View storage location information

### Managing Built-in Credentials
1. After logging in, go to **Settings → Manage Built-in Credentials**
2. View all current built-in accounts
3. Copy credentials to clipboard if needed
4. Edit the code to modify accounts

---

## Functions Added

### `verify_builtin_credentials(username, password)`
Checks if provided credentials match any built-in account.
- **Returns:** `True` if credentials match, `False` otherwise
- **Parameters:**
  - `username`: Account username
  - `password`: Account password

### `get_all_credentials()`
Returns a dictionary of all available credentials (both built-in and file-based).
- **Returns:** Dictionary with "builtin" and "file_based" keys
- **Useful for:** Administrative reporting and credential auditing

### `show_credentials_info()`
Displays a window with credential storage information.
- **Access:** Settings → View Credentials Info
- **Shows:** Built-in status, registered users, storage locations

### `manage_builtin_credentials()`
Provides interface to view and copy built-in credentials.
- **Access:** Settings → Manage Built-in Credentials
- **Features:** View all accounts, copy to clipboard, edit guide

---

## Login Screen Changes
The login screen now displays available built-in accounts:

```
Credentials are stored locally on this computer only.
Built-in accounts available: demo, admin, test, user
```

---

## Security Considerations

⚠️ **IMPORTANT SECURITY WARNING**

Storing plaintext passwords in code is **NOT SECURE** for production environments. 

### Recommended Practices:
- ✅ Use for **development and testing only**
- ✅ Use for **demo/trial accounts**
- ✅ Use for **internal tools** on secure machines
- ❌ **Never use** for production applications
- ❌ **Never commit** real passwords to version control
- ❌ **Never share** code containing credentials

### Best Practices:
1. Keep credential definitions in a separate development environment
2. Use environment variables for sensitive data
3. Consider using a secrets management system for production
4. Regularly audit who has access to the source code
5. Change built-in passwords frequently during development

---

## Quick Demo

### Default Demo Account
```
Username: demo
Password: demo123
```

### Default Admin Account
```
Username: admin
Password: admin123
```

Try logging in with these credentials immediately after launching the application!

---

## Troubleshooting

### Built-in Accounts Not Working
- Check if `ENABLE_BUILTIN_CREDENTIALS` is set to `True`
- Verify the username and password are in the `BUILTIN_CREDENTIALS` dictionary
- Check for typos in credentials

### Can't See Settings Menu
- Make sure you're logged in (settings menu only appears in the main application)
- Built-in credentials must be enabled in code to access credential management

### Reserved Username Error on Registration
- You cannot register a username that matches a built-in account name
- Try using a different username for registration

---

## File Locations

### Built-in Credentials
- **Location:** Top of the Python code (lines ~255-265)
- **File:** `5.py`

### File-based Credentials
- **Location:** `~\.zw_vat_system\users.json`
- **Format:** JSON with hashed passwords

---

## Examples

### Example 1: Add a New Department Account
```python
BUILTIN_CREDENTIALS = {
    "demo": "demo123",
    "admin": "admin123",
    "test": "test123",
    "user": "password123",
    "payroll": "payroll456",  # ← New account
    "hr": "hrteam789",        # ← New account
}
```

### Example 2: Disable Built-in Credentials Temporarily
```python
ENABLE_BUILTIN_CREDENTIALS = False  # Existing users still work
```

### Example 3: Check Credentials Programmatically
```python
if verify_builtin_credentials("demo", "demo123"):
    print("Valid demo account!")
```

---

## Summary

The built-in credentials feature provides:
- ✅ Quick access with predefined demo accounts
- ✅ Easy testing without user registration
- ✅ Flexible enable/disable control
- ✅ Credential management interface
- ✅ Plaintext storage for development convenience (⚠️ not for production)

For any modifications or questions, edit the `BUILTIN_CREDENTIALS` dictionary and restart the application!
