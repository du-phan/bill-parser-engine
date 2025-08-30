# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based bill parser engine designed to translate complex French legislative text into clear, accessible summaries. The system implements a sophisticated 8-step pipeline that mimics how lawyers analyze legislative amendments, focusing on producing comparative "diffs" of legal states.

## Development Commands

**Package Management:**
- Uses Poetry for dependency management
- Install dependencies: `poetry install`
- Activate environment: `poetry shell`

**Testing:**
- Run tests: `pytest` (configured in pyproject.toml)
- Test scripts available in `scripts/dev/` for component testing
- No formal test suite currently implemented - development uses individual component scripts

**Running the Pipeline:**
- Main pipeline script: `scripts/run_pipeline.py` 
- Individual component testing scripts available in `scripts/` directory
- Requires `MISTRAL_API_KEY` environment variable
- Optional: `LEGIFRANCE_CLIENT_ID` and `LEGIFRANCE_CLIENT_SECRET` for enhanced French code retrieval

## Architecture Overview

The system follows a strict 8-step pipeline architecture with clear separation of concerns:

**Core Pipeline (`bill_parser_engine/core/reference_resolver/`):**
1. **BillSplitter** - Parses legislative text into BillChunk objects using regex-based deterministic parsing
2. **TargetArticleIdentifier** - Identifies target legal articles using Mistral API in JSON mode with inheritance logic
3. **OriginalTextRetriever** - Fetches original law text from local French codes (`data/fr_code_text/`) and EU laws (`data/eu_law_text/`)
4. **LegalAmendmentReconstructor** - 3-step mini-pipeline that mechanically applies amendments (InstructionDecomposer → OperationApplier → ResultValidator)
5. **ReferenceLocator** - Scans text fragments for normative references, achieving 30x+ performance through focused scanning
6. **ReferenceObjectLinker** - Links references to grammatical objects using French legal text analysis
7. **ReferenceResolver** - Resolves references through question-guided content extraction
8. **LegalStateSynthesizer** - **Currently unimplemented** - Final synthesis of BeforeState/AfterState outputs

**Data Models:**
- All pipeline data structures defined in the core modules
- Key models: BillChunk, TargetArticle, ReconstructorOutput, LocatedReference, LinkedReference, ResolutionResult
- Missing: LegalAnalysisOutput for final pipeline output

**Key Design Principles:**
- **Lawyer's Mental Model**: Replicates how legal experts analyze amendments mechanically then contextually
- **Focused Processing**: Only processes delta fragments rather than entire documents for efficiency
- **DELETIONAL vs DEFINITIONAL**: References classified by their source context for proper resolution
- **Question-Guided Extraction**: Uses specific questions rather than returning entire legal documents

## Current Status

**Implemented (Steps 1-7):** Fully functional with sophisticated features, comprehensive caching, and production-ready code quality.

**Critical Gap:** Step 8 (LegalStateSynthesizer) is completely unimplemented, preventing the pipeline from delivering final analyzable output.

**Pipeline Orchestration:** `bill_parser_engine/core/reference_resolver/pipeline.py` coordinates Steps 1-7. API layer (`bill_parser_engine/api/routes.py`) exists but is empty.

## Development Patterns

**Error Handling:** Uses structured logging with loguru, though some modules mix print statements and logger calls.

**Caching:** Centralized caching system with comprehensive cache management. Some components temporarily bypass caching during development.

**LLM Integration:** Heavy use of Mistral API with JSON mode for structured outputs and function calling for complex analysis.

**Rate Limiting:** Conservative shared rate limiter implemented across components.

**Testing Strategy:** Component-level testing via scripts rather than formal unit test suite. Integration testing not yet implemented.

## Key Files to Understand

- `specs/pipeline_overview.md` - Comprehensive pipeline documentation and current status
- `.cursor/rules/startup-context.mdc` - Project context and architectural principles  
- `.cursor/rules/guidelines_summary.mdc` - Development standards and coding practices
- `bill_parser_engine/core/reference_resolver/pipeline.py` - Main orchestration logic
- Individual component files in `bill_parser_engine/core/reference_resolver/` for each pipeline step

## Data Dependencies

- French legal codes stored in `data/fr_code_text/`
- EU legal texts in `data/eu_law_text/`  
- Cached results in `cache/` directory
- Requires API access to Mistral for LLM components