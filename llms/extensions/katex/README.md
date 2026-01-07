# KaTeX Extension

This extension enables beautiful rendering of LaTeX math expressions in AI responses using [KaTeX](https://katex.org/). It integrates automatically with the markdown parser to render math equations in both inline and block formats.

## Features

- **Fast Rendering**: Uses KaTeX for high-performance rendering of math expressions.
- **Inline Math**: Renders math within text using `$` or `$$` delimiters.
- **Block Math**: Renders complex equations in their own block using `$` or `$$` delimiters across multiple lines.
- **Auto-Integration**: Automatically extends the `marked` parser used in the application.

## Usage

The extension supports standard LaTeX math syntax.

### Inline Math

Surround your LaTeX expression with single `$` (for inline style) or double `$$` (for display style) delimiters.

**Example:**
`The quadratic formula is $x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}$.`

### Block Math

For larger equations or when you want the math to be displayed on its own line, use block syntax by placing the delimiters on separate lines. Standard usage is to use double `$$` delimiters.

**Example:**
```latex
$$
\int_{-\infty}^{\infty} e^{-x^2} dx = \sqrt{\pi}
$$
```

## Configuration

The extension automatically registers:
- **Import Maps**: Loads `katex.min.mjs` for the frontend.
- **CSS**: Injects `katex.min.css` for styling.
- **Markdown Extension**: Adds a custom tokenizer and renderer to `marked` to detect and render LaTeX patterns.
