#!/usr/bin/env python3
"""
Run the Bill Processing Pipeline

This script demonstrates the clean, object-oriented approach to running the
legislative bill processing pipeline using the BillProcessingPipeline class.

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
        # Initialize pipeline (components handle their own caching)
        pipeline = BillProcessingPipeline()
        
        # Load legislative text
        logger.info("Loading legislative text from %s", bill_file)
        pipeline.load_legislative_text_from_file(bill_file)
        
        # Run pipeline steps individually (useful for debugging)
        logger.info("Running pipeline steps individually...")
        chunks = pipeline.step_1_split_bill()
        target_results = pipeline.step_2_identify_target_articles()
        retrieval_results = pipeline.step_3_retrieve_original_texts()
        reconstruction_results = pipeline.step_4_reconstruct_texts()
        
        # Save results
        logger.info("Saving results...")
        results_file = pipeline.save_results(output_dir, "pipeline_results")
        logger.info("Results saved to: %s", results_file)
        
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
        logger.info("  Text reconstruction: %.1f%% success (%d/%d)", 
                   summary["text_reconstruction"]["success_rate"] * 100,
                   summary["text_reconstruction"]["successful_reconstructions"],
                   summary["text_reconstruction"]["total_chunks"])
        
        # Cache management examples
        logger.info("\nCache Management Examples:")
        
        # Clear specific component cache (useful when debugging a component)
        # pipeline.clear_component_cache("original_text_retriever")
        
        # Clear all component caches (fresh start)
        # pipeline.clear_component_cache()
        
        logger.info("Pipeline execution completed successfully!")
        
    except FileNotFoundError:
        logger.error("Legislative bill file not found at %s", bill_file)
        logger.error("Please ensure the file exists or update the path")
    except Exception as e:
        logger.error("Pipeline execution failed: %s", e)
        raise


if __name__ == "__main__":
    main() 