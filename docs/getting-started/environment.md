# Setting Up Your Environment

You need two things installed: **VS Code** and **uv**.

---

## 1. VS Code

**VS Code** (Visual Studio Code) is a free code editor from Microsoft. It's where you'll view and edit your pipeline files, and it has a built-in terminal for running commands.

- **Download it** from [code.visualstudio.com](https://code.visualstudio.com/) and run the installer
- During installation, **tick "Add to PATH"** and **tick "Register Code as an editor for supported file types"**
- Once installed, open it from the Start menu by searching for **Visual Studio Code**

---

## 2. uv

**uv** is a fast Python package manager. It handles Python installation, virtual environments, and dependencies for you - so on an unmanaged computer you don't need to install Python separately.

- Open the VS Code terminal (Ctrl + Shift + ') and run: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- To check it worked, close and reopen the terminal, then type `uv --version`

uv will automatically download and manage the right version of Python when you create your Haute project.

See [uv installation docs](https://docs.astral.sh/uv/getting-started/installation/) if you run into trouble.

### Managed Windows computers

If your organisation uses Windows Application Control, use a Python 3.11 or later installation approved by your IT team instead of allowing uv to download one. Ask IT for the full path to that `python.exe`; the [installation guide](installing-haute.md#managed-windows-computers) shows how to create the project environment from it. Haute then runs as an ordinary package inside that approved Python runtime.

---

## Troubleshooting

**`command not found` when I type `uv`**

uv isn't on your system PATH. Close and reopen VS Code after installing. If it still doesn't work, re-run the installer.

---

Once both are installed, head to **[Installing Haute](installing-haute.md)**.
