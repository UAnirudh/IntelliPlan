# Pinned educational-content renderer assets

Self-hosted npm distributions, acquired2026-10-10. No runtime CDN or external content request is required. `sources.json` records exact versions, npm integrity and SHA256 for the copied files. License files are retained beside the distributions.

- markdown-it15.0.2: `dist/browser/markdown-it.umd.min.js`, MIT, [upstream](https://github.com/markdown-it/markdown-it).
- DOMPurify3.4.16: `dist/purify.min.js`, Apache2.0 OR MPL2.0, [upstream](https://github.com/cure53/DOMPurify).
- KaTeX0.19.0: `dist/katex.min.js`, `dist/katex.min.css`, and `dist/fonts`, MIT, [upstream](https://github.com/KaTeX/KaTeX).
- highlight.js11.12.0: `@highlightjs/cdn-assets/highlight.min.js`, BSD3-Clause, [upstream](https://github.com/highlightjs/highlight.js).

The assets are unmodified except for the markdown-it UMD filename. App-specific behavior is in `static/js/learning_content.js`: HTML and generated links/images disabled, sanitized DOM insertion, untrusted/size-bounded math and escaped fenced code. Regenerate the manifest after a deliberate dependency upgrade; do not edit minified distributions manually.
