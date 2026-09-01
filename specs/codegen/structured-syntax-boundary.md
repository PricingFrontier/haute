# Structured Python Syntax Boundary

**Status:** Accepted
**Date:** 2026-08-31

## Context

Haute's Python pipeline surface has two simultaneous obligations. It must preserve
human-authored formatting, comments, preamble, and recovery evidence, while also making
semantic decisions about decorators, calls, expressions, pipeline structure, and generated
runtime behaviour. Those obligations were previously spread across string templates,
`tokenize`, AST walks, regular expressions, and substring classifiers. Adding another local
text recognizer would increase the number of representations and retain false matches inside
comments or string literals.

Three boundaries were considered: a concrete-syntax-tree boundary, a versioned graph/source
IR, and the existing token/AST split.

## Decision

Haute adopts a structured valid-Python boundary with two deliberately different projections:

1. LibCST is the authority for surgical modification of valid Python and for syntax-aware
   call-site discovery when comments and original formatting must survive. The codegen-owned
   `haute._python_syntax` module hides LibCST types behind small typed results and raises a
   structured, value-free error with a source position when input is not valid Python.
2. The standard-library AST remains the authority for semantic extraction and evaluation
   where formatting is irrelevant: strict pipeline parsing, expression interpretation,
   generated-file compilation gates, and executable safety analysis. Callers do not unparse
   an AST back over authored source.
3. Invalid Python is never repaired or normalized through the valid-source boundary. The
   editor recovery path retains its conservative AST/regex fragment model, source captures,
   diagnostics, and recovery spans. Strict execution and save paths continue to reject invalid
   syntax.
4. String templates remain the readable initial source generator. Any post-generation edit
   must use the LibCST boundary rather than a token splice, regex replacement, or substring
   mutation. Text search remains acceptable only for non-semantic display/search features.

This is a source-syntax boundary, not a new runtime or persistence format. `PipelineGraph`
continues to represent the supported graph domain, while Python source remains capable of
carrying authored preamble, preserved blocks, comments, and unsupported constructs.

## Ownership boundaries

| Concern | Owner and representation |
|---|---|
| Comments and formatting during a valid-source edit | LibCST in `haute._python_syntax`; all untouched syntax is emitted from the original CST. |
| Generated source | Per-node string builders create readable source; LibCST performs decorator-keyword injection; the final AST parse gate rejects any invalid emitted module. |
| Strict pipeline parsing and semantic evaluation | Standard-library AST plus the closed parser/evaluator models. AST source locations own valid-source semantic error spans. |
| Invalid-source recovery | `_pipeline_recovery` and `_parser_regex`; recovery is read-only evidence for the editor and never becomes execution authority. Recovery captures own invalid-source spans. |
| Structured rewrite/classifier parse failures | `StructuredSyntaxError` with stable reason, one-based line, and zero-based column; the calling component maps that evidence to its public failure or conservative result. |
| Project discovery | Project configuration and strict pipeline parsing remain authoritative; this pilot does not promote content substrings into syntax authority. |
| Runtime/codegen node behaviour | `NODE_REGISTRY` remains the dispatch authority. A shared semantics declaration is used only for node types whose runtime and generated input behaviour are genuinely identical. |

## Rejected alternatives

- **Versioned graph/source IR as the immediate authority.** The graph model cannot represent
  arbitrary preamble, comments, preserved blocks, unsupported Python, or recovery fragments.
  Making it authoritative now would either discard authored material or require a second,
  near-complete Python syntax schema with migrations and dual-write rules.
- **Retain the token/AST split for mutation and add more recognizers.** `tokenize` can find a
  balanced delimiter but still leaves manual source splicing and formatting ownership at every
  caller. Substring classifiers also match comments, strings, and longer attribute names. This
  is not an adequate semantic boundary.
- **Use LibCST for execution semantics.** A concrete syntax tree preserves trivia but is not a
  safer or clearer evaluator. The existing AST validators and closed interpreters remain the
  appropriate semantic layer.
- **Run recovery output through LibCST.** Invalid documents cannot be represented by a valid
  CST. A fallback rewrite would obscure the original defect and weaken recovery spans.

## Consequences

Valid-source rewrites conserve comments and formatting outside the changed syntax node. A
syntax-aware classifier ignores lookalike text in comments and literals and can report exact
call-site spans. Parse failure is explicit; safety-sensitive consumers may conservatively
classify an unreadable fragment as unknown/unsafe, but may not fall back to substring meaning.

LibCST is not introduced into canonical runtime evaluation, and generated source is not
globally reformatted. Adding a future source mutation or classifier requires extending the
small structured boundary or documenting why the operation is non-semantic. A broader source
IR would require a separate decision with conservation and migration evidence.

## Pilot and evidence

The pilot has three bounded parts:

- contract decorator injection migrates from `_matching_close_paren` plus manual text splicing
  to `inject_decorator_keyword`, with differential fixtures covering single-line, multiline,
  bare, comment-bearing, CRLF, adversarial string, invalid-source, and recovery inputs;
- trace row-reorder detection migrates from `_ROW_REORDERING_TOKENS` substring search to exact
  LibCST method-call sites, with tests proving that real calls are detected while comments,
  strings, longer attribute names, and bare attributes are not;
- the modelling node's first-input passthrough semantics and decorator metadata come from one
  registry declaration consumed by both runtime and codegen, with a direct cross-path result
  contract. No other node type is claimed to share those semantics without equivalent proof.

Round-trip tests continue to cover all supported node types, hand-authored formatting,
preserved blocks, strict parsing, and editor recovery. The pilot is accepted only when the
targeted codegen, parser/recovery, trace, registry, and execution-equivalence suites remain
green.
