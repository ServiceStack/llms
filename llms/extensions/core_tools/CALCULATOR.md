# Calculator

A powerful and safe mathematical expression evaluator with a rich web interface.

## Features

### 🖥️ UX Friendly Interface
Experience a clean, modern interface designed for efficiency. The UI is fully responsive and supports dark mode, seamlessly integrating with the rest of the application.

### 💾 Persistent History
Never lose track of your calculations. The Calculator automatically saves your history to `localStorage`, ensuring your previous expressions and results are preserved between sessions.

### ⚡ 1-Click Interaction
Streamline your workflow with interactive history items:
- **Load & Copy**: Click on any past expression or answer to instantly load it into the input field and copy it to your clipboard.
- **Visual Feedback**: Temporary checkmarks confirm successful copy actions.

### ⌨️ Keyboard-Free Access
While full keyboard support is available, you can perform complex calculations entirely via the UI:
- **Numbers & Constants**: Quick access to digits and mathematical constants like `pi`, `e`, `inf`.
- **Operators**: Comprehensive set of buttons for arithmetic (`+`, `-`, `*`, `/`, `%`, `^`) and boolean logic (`and`, `or`, `not`).
- **Functions**: One-click insertion or wrapping of selection for all supported math functions.

### 🐍 Python Math Support
Unlock the power of Python's math library directly in the browser.
- **Math Functions**: Support for `sin`, `cos`, `tan`, `sqrt`, `log`, `factorial`, and many more.
- **Statistics**: Built-in functions for `mean`, `median`, `stdev`, and `variance`.

### 🛡️ Safe Evaluation
Security is a priority. Instead of using Python's unsafe `eval()`, the Calculator uses a robust **AST (Abstract Syntax Tree) evaluator**.
- **Restricted Environment**: Only allowed mathematical operations and functions are executed.
- **No Side Effects**: Prevents arbitrary code execution, making it safe to evaluate expressions from untrusted sources.
