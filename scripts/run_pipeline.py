#!/usr/bin/env python3
"""
Run the Bill Processing Pipeline

This script demonstrates the clean, object-oriented approach to running the
legislative bill processing pipeline using the BillProcessingPipeline class.

The pipeline includes these steps:
1. BillSplitter - breaks the bill into atomic chunks
2. TargetArticleIdentifier - identifies target articles for each chunk
3. OriginalTextRetriever - fetches current legal text for unique target articles
4. LegalAmendmentReconstructor - applies amendment instructions using 3-step LLM architecture:
   • InstructionDecomposer: parses compound instructions into atomic operations
   • OperationApplier: applies each operation with format-aware intelligence
   • ResultValidator: validates legal coherence and structure
5. ReferenceLocator - identifies normative references in deleted/replaced and new text fragments
6. ReferenceObjectLinker - links each reference to its grammatical object using context-aware analysis

Usage:
    poetry run python scripts/run_pipeline.py

Environment variables required:
    MISTRAL_API_KEY - Mistral API key for the LLM calls
    LEGIFRANCE_CLIENT_ID - (optional) Legifrance API credentials for better retrieval
    LEGIFRANCE_CLIENT_SECRET - (optional) Legifrance API credentials for better retrieval
"""

import os
import sys
from pathlib import Path
import logging
from datetime import datetime

from dotenv import load_dotenv

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from bill_parser_engine.core.reference_resolver.pipeline import BillProcessingPipeline


def print_section_header(title: str, char: str = "=", width: int = 80):
    """Print a formatted section header."""
    print(f"\n{char * width}")
    print(f"{title}")
    print(f"{char * width}")


def print_summary_section(title: str, data: dict, indent: str = ""):
    """Print a formatted summary section."""
    print(f"\n{indent}📋 {title}:")
    for key, value in data.items():
        if isinstance(value, dict):
            print(f"{indent}  {key}:")
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, float):
                    if "rate" in sub_key.lower():
                        print(f"{indent}    {sub_key}: {sub_value:.1%}")
                    else:
                        print(f"{indent}    {sub_key}: {sub_value:.0f}")
                elif isinstance(sub_value, int):
                    print(f"{indent}    {sub_key}: {sub_value:,}")
                else:
                    print(f"{indent}    {sub_key}: {sub_value}")
        elif isinstance(value, float):
            if "rate" in key.lower():
                print(f"{indent}  {key}: {value:.1%}")
            else:
                print(f"{indent}  {key}: {value:.0f}")
        elif isinstance(value, int):
            print(f"{indent}  {key}: {value:,}")
        else:
            print(f"{indent}  {key}: {value}")


def main():
    """Demonstrate pipeline usage with component-level caching."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    # Load environment variables
    env_file = project_root / ".env.local"
    load_dotenv(env_file)
    
    # Check required environment variables
    required_env_vars = ["MISTRAL_API_KEY"]
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    if missing_vars:
        print(f"❌ Error: Missing required environment variables: {missing_vars}")
        print(f"   Please ensure they are set in {env_file}")
        sys.exit(1)
    
    # Check optional Legifrance credentials
    legifrance_available = os.getenv("LEGIFRANCE_CLIENT_ID") and os.getenv("LEGIFRANCE_CLIENT_SECRET")
    if legifrance_available:
        print("✓ Legifrance credentials available - will use API for retrieval")
    else:
        print("⚠ Legifrance credentials not available - will use fallback methods")
    
    print("✓ Environment variables loaded")
    
    # Define paths
    bill_file = project_root / "data" / "legal_bill" / "full_legislative_bill.md"
    output_dir = project_root / "scripts" / "output"
    
    try:
        # Initialize pipeline with optional detailed logging
        log_file_path = output_dir / f"detailed_reconstruction_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        pipeline = BillProcessingPipeline(use_cache=True, log_file_path=str(log_file_path))
        
        logger.info("Pipeline initialized with detailed logging: %s", pipeline.get_reconstruction_log_file())
        
        # Load legislative text
        logger.info("Loading legislative text from %s", bill_file)
        pipeline.load_legislative_text_from_file(bill_file)
        
        # Demonstrate step-by-step execution with comprehensive chunk tracing
        print_section_header("STEP-BY-STEP EXECUTION WITH COMPREHENSIVE TRACING")
        
        # Step 1: Split the bill into chunks
        logger.info("🔄 Step 1: Splitting legislative bill into chunks...")
        chunks = pipeline.step_1_split_bill()
        logger.info("✅ Created %d chunks", len(chunks))
        
        # Check trace status after step 1
        status = pipeline.get_current_trace_status()
        logger.info("📊 Trace Status: %d chunks traced, steps completed: %s", 
                   status['chunks_traced'], status['steps_completed'])
        
        # Optional: Export traces after step 1 for early debugging
        step1_trace_file = pipeline.export_traces_after_step("step_1", 
                                                           output_dir / "traces_after_step1.txt")
        if step1_trace_file:
            logger.info("📄 Step 1 traces exported to: %s", step1_trace_file)
        
        # Step 2: Identify target articles
        logger.info("\n🔄 Step 2: Identifying target articles...")
        target_results = pipeline.step_2_identify_target_articles()
        logger.info("✅ Identified targets for %d chunks", len(target_results))
        
        # Check updated trace status
        status = pipeline.get_current_trace_status()
        logger.info("📊 Updated Trace Status: %d chunks traced, steps: %s", 
                   status['chunks_traced'], status['steps_completed'])
        logger.info("📈 Step completion per chunk: %s", status['total_steps_per_chunk'])
        
        # Show sample of what's being traced
        if status['chunks_traced'] > 0:
            sample_chunks = list(status['steps_per_chunk_sample'].items())[:2]
            logger.info("🔍 Sample chunk progress:")
            for chunk_id, steps in sample_chunks:
                logger.info("  • %s: %s", chunk_id[:30] + "...", steps)
        
        # Step 3: Retrieve original texts (not chunk-specific, so not traced)
        logger.info("\n🔄 Step 3: Retrieving original texts for unique articles...")
        retrieval_results = pipeline.step_3_retrieve_original_texts()
        logger.info("✅ Retrieved texts for %d unique articles", len(retrieval_results))
        logger.info("ℹ️  Note: Step 3 is article-level, not chunk-level, so not included in chunk traces")
        
        # Step 4: Text reconstruction with detailed tracing
        logger.info("\n🔄 Step 4: Applying text reconstruction...")
        reconstruction_results = pipeline.step_4_reconstruct_texts()
        logger.info("✅ Reconstructed %d chunks", len(reconstruction_results))
        
        # Final comprehensive trace status
        status = pipeline.get_current_trace_status()
        logger.info("\n📊 Final Comprehensive Trace Status:")
        logger.info("  • Chunks traced: %d", status['chunks_traced'])
        logger.info("  • Steps completed: %s", status['steps_completed'])
        logger.info("  • Step completion counts: %s", status['total_steps_per_chunk'])
        
        # Export comprehensive traces for debugging
        comprehensive_trace_file = output_dir / f"comprehensive_chunk_traces_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        pipeline.export_chunk_traces_to_file(comprehensive_trace_file)
        logger.info("💾 Comprehensive chunk traces exported to: %s", comprehensive_trace_file)
        
        # Show sample of individual chunk traces for illustration
        logger.info("\n🔍 Sample Individual Chunk Traces (first 2 chunks):")
        for i, (chunk_id, trace_data) in enumerate(list(pipeline.chunk_traces.items())[:2]):
            logger.info("  Chunk %d: %s", i+1, chunk_id[:40] + "...")
            
            # Show chunk metadata
            if "chunk_metadata" in trace_data:
                metadata = trace_data["chunk_metadata"]
                logger.info("    📝 Text: %s", metadata.get('chunk_text_preview', 'N/A')[:60] + "...")
                logger.info("    📍 Hierarchy: %s", ' > '.join(metadata.get('hierarchy_path', [])))
            
            # Show step results
            steps = [step for step in trace_data.keys() if step != 'chunk_metadata']
            logger.info("    🔄 Steps completed: %s", steps)
            
            # Show target identification result if available
            if "step_2_target_identification" in trace_data:
                step_2 = trace_data["step_2_target_identification"]
                if step_2.get("success"):
                    output = step_2.get("output_result", {})
                    logger.info("    🎯 Target: %s (%s)", 
                               output.get('article', 'N/A'), 
                               output.get('operation_type', 'N/A'))
                    logger.info("    ⏱️  Processing time: %.3f seconds", 
                               step_2.get('processing_duration_seconds', 0))
            
            # Show reconstruction result if available
            if "step_4_text_reconstruction" in trace_data:
                step_4 = trace_data["step_4_text_reconstruction"]
                if step_4.get("success"):
                    output = step_4.get("output_result", {})
                    logger.info("    🔧 Reconstruction: %d operations applied, %d failed", 
                               output.get('operations_applied', 0),
                               output.get('operations_failed', 0))
                    logger.info("    📏 Result length: %d characters", 
                               output.get('final_text_length', 0))
        
        # Continue with remaining steps (these don't have chunk-level tracing yet)
        logger.info("\n🔄 Step 5: Locating references...")
        reference_location_results = pipeline.step_5_locate_references()
        logger.info("✅ Located references in %d chunks", len(reference_location_results))
        
        logger.info("\n🔄 Step 6: Linking references...")
        reference_linking_results = pipeline.step_6_link_references()
        logger.info("✅ Linked references in %d chunks", len(reference_linking_results))
        
        # Save results
        logger.info("Saving results...")
        results_file = pipeline.save_results(output_dir, "pipeline_results")
        logger.info("Results saved to: %s", results_file)
        
        # Demonstrate enhanced reconstruction capabilities
        logger.info("\nLegalAmendmentReconstructor Enhanced Analysis:")
        successful_reconstructions = [r for r in reconstruction_results if r.get("reconstruction_result")]
        if successful_reconstructions:
            total_operations = sum(r.get("advanced_reconstruction_metadata", {}).get("operations_applied", 0) 
                                 for r in successful_reconstructions)
            total_processing_time = sum(r.get("advanced_reconstruction_metadata", {}).get("processing_time_ms", 0) 
                                      for r in successful_reconstructions)
            avg_processing_time = total_processing_time / len(successful_reconstructions) if successful_reconstructions else 0
            
            # Count operation types
            operation_types = {}
            validation_warnings = 0
            for result in successful_reconstructions:
                metadata = result.get("advanced_reconstruction_metadata", {})
                for op_detail in metadata.get("operations_details", []):
                    op_type = op_detail.get("type", "UNKNOWN")
                    operation_types[op_type] = operation_types.get(op_type, 0) + 1
                validation_warnings += len(metadata.get("validation_warnings", []))
            
            logger.info("  📊 Advanced Metrics:")
            logger.info("    • Total atomic operations applied: %d", total_operations)
            logger.info("    • Average processing time: %.0f ms per chunk", avg_processing_time)
            logger.info("    • Operation type distribution: %s", dict(operation_types))
            logger.info("    • Validation warnings generated: %d", validation_warnings)
            
            # Show example of enhanced metadata
            example_result = successful_reconstructions[0]
            example_metadata = example_result.get("advanced_reconstruction_metadata", {})
            if example_metadata.get("operations_details"):
                logger.info("  📋 Example operation detail:")
                op_detail = example_metadata["operations_details"][0]
                logger.info("    • Type: %s", op_detail.get("type"))
                logger.info("    • Position: %s", op_detail.get("position"))
                logger.info("    • Confidence: %.2f", op_detail.get("confidence", 0))
        else:
            logger.info("  No successful reconstructions to analyze")
        
        # Print summary
        summary = pipeline.get_summary()
        logger.info("Pipeline Summary:")
        logger.info("  Total chunks: %d", summary["total_chunks"])
        logger.info("  Target identification: %d unique articles, %.1f%% success", 
                   summary["target_identification"]["unique_articles"],
                   (summary["target_identification"]["chunks_with_articles"] / summary["total_chunks"]) * 100)
        logger.info("  Original text retrieval: %.1f%% success (%d/%d)", 
                   summary["original_text_retrieval"]["success_rate"] * 100,
                   summary["original_text_retrieval"]["successful_retrievals"],
                   summary["original_text_retrieval"]["total_articles"])
        logger.info("  Text reconstruction (LegalAmendmentReconstructor): %.1f%% success (%d/%d)", 
                   summary["text_reconstruction"]["success_rate"] * 100,
                   summary["text_reconstruction"]["successful_reconstructions"],
                   summary["text_reconstruction"]["total_chunks"])
        logger.info("  Reference location: %.1f%% success (%d/%d) - found %d refs (%.1f%% conf)", 
                   summary["reference_location"]["success_rate"] * 100,
                   summary["reference_location"]["successful_locations"],
                   summary["reference_location"]["total_chunks"],
                   summary["reference_location"]["total_references"],
                   summary["reference_location"]["average_confidence"] * 100)
        logger.info("    └─ DELETIONAL: %d, DEFINITIONAL: %d, chunks with refs: %d", 
                   summary["reference_location"]["deletional_references"],
                   summary["reference_location"]["definitional_references"],
                   summary["reference_location"]["chunks_with_references"])
        logger.info("  Reference linking: %.1f%% success (%d/%d) - linked %d/%d refs (%.1f%% link rate, %.1f%% conf)", 
                   summary["reference_linking"]["success_rate"] * 100,
                   summary["reference_linking"]["successful_linkings"],
                   summary["reference_linking"]["total_chunks"],
                   summary["reference_linking"]["total_linked_references"],
                   summary["reference_linking"]["total_located_references"],
                   summary["reference_linking"]["linking_success_rate"] * 100,
                   summary["reference_linking"]["average_confidence"] * 100)
        logger.info("    └─ DELETIONAL linked: %d, DEFINITIONAL linked: %d, chunks with linkings: %d", 
                   summary["reference_linking"]["deletional_linked"],
                   summary["reference_linking"]["definitional_linked"],
                   summary["reference_linking"]["chunks_with_linkings"])
        
        # Demonstrate tracing control features
        print_section_header("TRACING CONTROL AND MANAGEMENT", "=", 60)
        
        logger.info("🎛️  Tracing Control Features:")
        logger.info("  • Tracing is enabled by default during pipeline initialization")
        logger.info("  • You can disable/enable tracing at any point:")
        logger.info("    - pipeline.disable_tracing()  # Stop collecting traces")
        logger.info("    - pipeline.enable_tracing()   # Resume collecting traces")
        logger.info("    - pipeline.clear_traces()     # Clear accumulated data")
        
        logger.info("\n📊 Current Trace Statistics:")
        final_status = pipeline.get_current_trace_status()
        logger.info("  • Total chunks traced: %d", final_status['chunks_traced'])
        logger.info("  • Steps with traces: %s", final_status['steps_completed'])
        logger.info("  • Tracing currently: %s", "ENABLED" if final_status['tracing_enabled'] else "DISABLED")
        
        logger.info("\n💾 Export Options:")
        logger.info("  • Export after any step: pipeline.export_traces_after_step('step_2')")
        logger.info("  • Export comprehensive traces: pipeline.export_chunk_traces_to_file(path)")
        logger.info("  • Auto-export with pipeline: pipeline.run_full_pipeline_with_tracing()")
        
        logger.info("\n🔍 Debugging Benefits:")
        logger.info("  • See exact input/output for each chunk at each step")
        logger.info("  • Track processing times and identify bottlenecks")
        logger.info("  • Isolate failures to specific chunks and steps")
        logger.info("  • Compare successful vs failed chunk processing patterns")
        logger.info("  • Verify component behavior with real data")
        
        # Cache management examples
        print_section_header("CACHE MANAGEMENT EXAMPLES", "=", 60)
        
        # Clear specific component cache (useful when debugging a component)
        # pipeline.clear_component_cache("original_text_retriever")
        # pipeline.clear_component_cache("reference_object_linker")
        # pipeline.clear_component_cache("text_reconstructor")  # Clears LegalAmendmentReconstructor caches
        
        # Clear all component caches (fresh start)
        # pipeline.clear_component_cache()
        
        # Note: LegalAmendmentReconstructor manages caches for its 3 sub-components:
        # - InstructionDecomposer cache (parses amendment instructions)
        # - OperationApplier cache (applies atomic operations)
        # - ResultValidator cache (validates legal coherence)
        logger.info("💾 LegalAmendmentReconstructor uses 3-tier caching for optimal performance")
        
        logger.info("Pipeline execution completed successfully!")
        
        # Demonstrate alternative: run_full_pipeline_with_tracing()
        print_section_header("ALTERNATIVE: FULL PIPELINE WITH AUTO-TRACING", "=", 60)
        
        logger.info("🚀 Alternative Approach - Auto-Export Tracing:")
        logger.info("  Instead of running steps individually, you can use:")
        logger.info("  ")
        logger.info("    # Clear existing traces and run full pipeline with auto-export")
        logger.info("    pipeline.clear_traces()")
        logger.info("    results = pipeline.run_full_pipeline_with_tracing()")
        logger.info("    # -> Automatically exports to chunk_traces_TIMESTAMP.txt")
        logger.info("  ")
        logger.info("  This method:")
        logger.info("  • Runs all 6 steps sequentially")
        logger.info("  • Collects comprehensive chunk traces throughout")
        logger.info("  • Automatically exports traces at the end")
        logger.info("  • Returns results with trace_export_path included")
        
        logger.info("\n🎉 Enhanced Features Available:")
        logger.info("  • Comprehensive chunk-by-chunk tracing for Steps 1, 2, and 4")
        logger.info("  • Real-time trace status monitoring")
        logger.info("  • Flexible export options (after each step or at the end)")
        logger.info("  • Detailed input/output logging for debugging")
        logger.info("  • Processing time tracking per chunk per step")
        logger.info("  • Error isolation and context preservation")
        logger.info("  • Integration with existing LegalAmendmentReconstructor logging")
        logger.info("  • Perfect for Jupyter notebook step-by-step debugging")
        logger.info("    Reconstruction log file: %s", pipeline.get_reconstruction_log_file())
        logger.info("    Comprehensive trace file: %s", comprehensive_trace_file)
        
        # Detailed logging examples
        logger.info("\n📝 Detailed Logging Information:")
        logger.info("  • Each chunk's reconstruction is logged with full details")
        logger.info("  • Includes: original text, amendment instruction, operations, before/after states")
        logger.info("  • Step-by-step operation application tracking")
        logger.info("  • Validation results and error analysis")
        logger.info("  • Perfect for manual verification and debugging")
        
        # Example of how to set custom log file
        # pipeline.set_reconstruction_log_file("custom_reconstruction_log.txt")
        # logger.info("Custom log file set to: %s", pipeline.get_reconstruction_log_file())
        
    except FileNotFoundError:
        logger.error("Legislative bill file not found at %s", bill_file)
        logger.error("Please ensure the file exists or update the path")
    except Exception as e:
        logger.error("Pipeline execution failed: %s", e)
        raise


if __name__ == "__main__":
    main() 