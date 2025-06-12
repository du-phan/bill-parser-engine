#!/usr/bin/env python3
"""
Simple comprehensive test of the complete Normative Reference Resolver pipeline.
This version uses a simple string-based logging approach for reliable file output
and includes proper rate limiting to avoid API issues.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from dotenv import load_dotenv
from mistralai import Mistral

# Add the project root to the path so we can import the module
project_root = Path(__file__).parent.parent.parent  # Go up from tests/pipeline_testing/ to project root
sys.path.insert(0, str(project_root))

# Load environment from project root
load_dotenv(project_root / ".env.local")

from bill_parser_engine.core.reference_resolver import (
    complete_reference_pipeline,
    PipelineConfig,
    initialize_pipeline_components
)

# Configure shared rate limiter
from bill_parser_engine.core.reference_resolver.rate_limiter import rate_limiter
rate_limiter.update_delay(1.5)  # 1.5 seconds between API calls (safe buffer for 1 req/sec limit)


class SimpleLogger:
    """Simple logger that collects all output in a string."""
    
    def __init__(self):
        self.log_content = ""
        self.start_time = datetime.now()
    
    def log(self, message: str, level: str = "INFO"):
        """Add a log message to the content."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_message = f"{timestamp} - {level} - {message}"
        self.log_content += formatted_message + "\n"
        print(formatted_message)  # Also print to console
    
    def log_json(self, title: str, data: Any, level: str = "INFO"):
        """Log data as formatted JSON."""
        self.log(f"\n{'='*60}", level)
        self.log(title, level)
        self.log("="*60, level)
        
        try:
            if hasattr(data, '__dict__'):
                json_str = json.dumps(data.__dict__, indent=2, ensure_ascii=False, default=str)
            elif isinstance(data, (list, dict)):
                json_str = json.dumps(data, indent=2, ensure_ascii=False, default=str)
            else:
                json_str = json.dumps(str(data), indent=2, ensure_ascii=False)
            
            self.log(json_str, level)
        except Exception as e:
            self.log(f"Failed to serialize JSON for {title}: {e}", "ERROR")
            self.log(f"{title}: {str(data)}", level)
        
        self.log("="*60 + "\n", level)
    
    def save_to_file(self, filename: str):
        """Save all collected logs to a file."""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(self.log_content)
            self.log(f"✅ Logs saved to: {filename}")
            return True
        except Exception as e:
            self.log(f"❌ Failed to save logs to {filename}: {e}", "ERROR")
            return False


def add_rate_limit_delay(logger, step_name: str, delay_seconds: float = 15.0):
    """Add a delay between API calls to respect rate limits."""
    logger.log(f"⏱️ Adding {delay_seconds}s delay before {step_name} to respect rate limits...")
    time.sleep(delay_seconds)


def get_test_legislative_text() -> str:
    """Get the test legislative text."""
    return """
# TITRE Iᴱᴿ

METTRE FIN AUX SURTRANSPOSITIONS ET SURRÉGLEMENTATIONS FRANÇAISES EN MATIÈRE DE PRODUITS PHYTOSANITAIRES

## Article 1ᵉʳ

Le code rural et de la pêche maritime est ainsi modifié :

2° L'article L. 254-1 est ainsi modifié :

    b) Le VI est ainsi modifié :

    	- à la fin de la première phrase, les mots : « incompatible avec celui des activités mentionnées aux 1° ou 2° du II ou au IV » sont remplacés par les mots : « interdit aux producteurs au sens du 11 de l'article 3 du règlement (CE) n° 1107/2009 du 21 octobre 2009, sauf lorsque la production concerne des produits de biocontrôle figurant sur la liste mentionnée à l'article L. 253-5 du présent code, des produits composés uniquement de substances de base au sens de l'article 23 du règlement (CE) n° 1107/2009 ou de produits à faible risque au sens de l'article 47 du même règlement (CE) n° 1107/2009 et des produits dont l'usage est autorisé dans le cadre de l'agriculture biologique » ;

    	- la seconde phrase est supprimée ;
"""


def detailed_component_testing(client: Mistral, legislative_text: str, logger):
    """Run detailed component testing with comprehensive logging."""
    
    logger.log("🔬 STARTING DETAILED COMPONENT-BY-COMPONENT TESTING")
    logger.log("="*80)
    
    try:
        # Initialize components
        config = PipelineConfig(
            max_resolution_depth=3,
            confidence_threshold=0.7,
            cache_dir="./reference_cache",
            rate_limit_per_minute=3,  # Extremely conservative rate limiting (20 seconds between calls)
            timeout_seconds=300
        )
        
        components = initialize_pipeline_components(client, config)
        logger.log("✅ Components initialized successfully")
        logger.log_json("PIPELINE CONFIGURATION", config)
        
        # Step 1: Bill Splitting (no API calls)
        logger.log("\n🔄 STEP 1: BILL SPLITTING")
        chunks = components.splitter.split(legislative_text)
        logger.log(f"Split into {len(chunks)} chunks")
        
        for i, chunk in enumerate(chunks):
            logger.log_json(f"CHUNK {i+1} OUTPUT", chunk)
        
        if not chunks:
            logger.log("❌ No chunks produced by BillSplitter", "ERROR")
            return None
        
        test_chunk = chunks[0]
        logger.log(f"Using chunk for testing: {test_chunk.chunk_id}")
        
        # Step 2: Target Article Identification (API call)
        logger.log("\n🔄 STEP 2: TARGET ARTICLE IDENTIFICATION")
        logger.log_json("TARGET_IDENTIFIER INPUT", test_chunk)
        
        target_article = components.target_identifier.identify(test_chunk)
        logger.log_json("TARGET_IDENTIFIER OUTPUT", target_article)
        
        if not target_article.article:
            logger.log("❌ No target article identified", "ERROR")
            return None
        
        # Assign target article to the chunk
        test_chunk.target_article = target_article
        
        # Step 3: Original Text Retrieval (may use cache, no API delay needed)
        logger.log("\n🔄 STEP 3: ORIGINAL TEXT RETRIEVAL")
        logger.log(f"Fetching article: {target_article.code} - {target_article.article}")
        
        original_text, retrieval_metadata = components.text_retriever.fetch_article_text(
            code=target_article.code,
            article=target_article.article
        )
        
        logger.log_json("ORIGINAL_TEXT_RETRIEVER OUTPUT", {
            "original_text": original_text[:500] + "..." if len(original_text) > 500 else original_text,
            "original_text_length": len(original_text),
            "retrieval_metadata": retrieval_metadata
        })
        
        if not original_text:
            logger.log("⚠️ No original text retrieved", "WARNING")
            return None
        
        # Step 4: Text Reconstruction (API call)
        logger.log("\n🔄 STEP 4: TEXT RECONSTRUCTION")
        logger.log_json("TEXT_RECONSTRUCTOR INPUT", {
            "original_law_article": original_text[:200] + "..." if len(original_text) > 200 else original_text,
            "amendment_chunk": test_chunk.text
        })
        
        reconstructor_output = components.reconstructor.reconstruct(original_text, test_chunk)
        logger.log_json("TEXT_RECONSTRUCTOR OUTPUT", reconstructor_output)
        
        # Step 5: Reference Location (API call)
        logger.log("\n🔄 STEP 5: REFERENCE LOCATION")
        logger.log_json("REFERENCE_LOCATOR INPUT", reconstructor_output)
        
        located_references = components.locator.locate(reconstructor_output)
        logger.log_json("REFERENCE_LOCATOR OUTPUT", {
            "located_references": [ref.__dict__ for ref in located_references],
            "total_references_found": len(located_references)
        })
        
        # Step 6: Reference Object Linking (may have API calls if references found)
        logger.log("\n🔄 STEP 6: REFERENCE OBJECT LINKING")
        logger.log_json("REFERENCE_OBJECT_LINKER INPUT", {
            "located_references": [ref.__dict__ for ref in located_references],
            "reconstructor_output": reconstructor_output.__dict__
        })
        
        linked_references = components.linker.link_references(located_references, reconstructor_output)
        logger.log_json("REFERENCE_OBJECT_LINKER OUTPUT", {
            "linked_references": [ref.__dict__ for ref in linked_references],
            "total_references_linked": len(linked_references)
        })
        
        # Step 7: Resolution Orchestration (may have API calls)
        logger.log("\n🔄 STEP 7: RESOLUTION ORCHESTRATION")
        logger.log_json("RESOLUTION_ORCHESTRATOR INPUT", {
            "linked_references": [ref.__dict__ for ref in linked_references]
        })
        
        resolution_result = components.orchestrator.resolve_references(linked_references)
        logger.log_json("RESOLUTION_ORCHESTRATOR OUTPUT", {
            "resolved_deletional_references": [ref.__dict__ for ref in resolution_result.resolved_deletional_references],
            "resolved_definitional_references": [ref.__dict__ for ref in resolution_result.resolved_definitional_references],
            "resolution_tree": resolution_result.resolution_tree,
            "unresolved_references": [ref.__dict__ for ref in resolution_result.unresolved_references],
            "summary": {
                "deletional_count": len(resolution_result.resolved_deletional_references),
                "definitional_count": len(resolution_result.resolved_definitional_references),
                "unresolved_count": len(resolution_result.unresolved_references)
            }
        })
        
        # Step 8: Legal State Synthesis (API call)
        logger.log("\n🔄 STEP 8: LEGAL STATE SYNTHESIS")
        logger.log_json("LEGAL_STATE_SYNTHESIZER INPUT", {
            "resolution_result_summary": {
                "deletional_count": len(resolution_result.resolved_deletional_references),
                "definitional_count": len(resolution_result.resolved_definitional_references)
            },
            "reconstructor_output": reconstructor_output.__dict__,
            "source_chunk": test_chunk.__dict__,
            "target_article": target_article.__dict__
        })
        
        final_output = components.synthesizer.synthesize(
            resolution_result=resolution_result,
            reconstructor_output=reconstructor_output,
            source_chunk=test_chunk,
            target_article=target_article
        )
        
        logger.log_json("LEGAL_STATE_SYNTHESIZER OUTPUT - FINAL RESULT", {
            "before_state": final_output.before_state.__dict__,
            "after_state": final_output.after_state.__dict__,
            "source_chunk_id": final_output.source_chunk.chunk_id,
            "target_article": final_output.target_article.__dict__
        })
        
        logger.log("="*80)
        logger.log("✅ DETAILED COMPONENT TESTING COMPLETED SUCCESSFULLY")
        logger.log("="*80)
        
        return final_output
        
    except Exception as e:
        logger.log(f"❌ Detailed component testing failed: {e}", "ERROR")
        return None


def full_pipeline_testing(client: Mistral, legislative_text: str, logger):
    """Run the complete pipeline and log results with rate limiting."""
    
    logger.log("🔄 STARTING FULL PIPELINE TESTING")
    logger.log("="*80)
    
    try:
        # More conservative rate limiting for full pipeline
        config = PipelineConfig(
            max_resolution_depth=3,
            confidence_threshold=0.7,
            cache_dir="./reference_cache",
            rate_limit_per_minute=3,  # Extremely conservative rate limiting (20 seconds between calls)
            timeout_seconds=300
        )
        
        logger.log_json("PIPELINE INPUT", {
            "legislative_text": legislative_text,
            "config": config.__dict__
        })
        
        # Add initial delay to ensure we start fresh
        logger.log("⏱️ Adding initial 10-second delay before starting full pipeline...")
        time.sleep(10.0)
        
        result = complete_reference_pipeline(legislative_text, client, config)
        
        logger.log_json("FULL PIPELINE RESULT", {
            "success": result.success,
            "total_outputs": len(result.outputs),
            "total_failed_chunks": len(result.failed_chunks),
            "metadata": result.metadata,
            "error": result.error
        })
        
        # Log each successful output
        for i, output in enumerate(result.outputs):
            logger.log_json(f"SUCCESSFUL OUTPUT {i+1}", {
                "before_state": output.before_state.__dict__,
                "after_state": output.after_state.__dict__,
                "source_chunk_id": output.source_chunk.chunk_id,
                "target_article": output.target_article.__dict__
            })
        
        # Log failed chunks
        for i, failed_chunk in enumerate(result.failed_chunks):
            logger.log_json(f"FAILED CHUNK {i+1}", failed_chunk)
        
        logger.log("="*80)
        logger.log("✅ FULL PIPELINE TESTING COMPLETED")
        logger.log("="*80)
        
        return result
        
    except Exception as e:
        logger.log(f"❌ Full pipeline testing failed: {e}", "ERROR")
        return None


def main():
    """Main execution function."""
    # Initialize logger
    logger = SimpleLogger()
    
    try:
        logger.log("🚀 STARTING COMPREHENSIVE PIPELINE TESTING WITH RATE LIMITING")
        
        # Environment variables already loaded at import time
        
        # Verify API keys
        api_key = os.getenv('MISTRAL_API_KEY')
        if not api_key:
            raise ValueError("MISTRAL_API_KEY not found in environment variables")
        
        logger.log("✅ Environment variables loaded successfully")
        
        # Initialize Mistral client
        client = Mistral(api_key=api_key)
        logger.log("✅ Mistral client initialized")
        
        # Get test data
        legislative_text = get_test_legislative_text()
        logger.log(f"📄 Test legislative text loaded ({len(legislative_text)} characters)")
        
        # Run detailed component testing
        logger.log("\n" + "🔬"*20 + " DETAILED COMPONENT TESTING " + "🔬"*20)
        detailed_result = detailed_component_testing(client, legislative_text, logger)
        
        # Add delay between test phases
        logger.log("⏱️ Adding 10-second delay between test phases...")
        time.sleep(10.0)
        
        # Run full pipeline testing
        logger.log("\n" + "🔄"*20 + " FULL PIPELINE TESTING " + "🔄"*20)
        pipeline_result = full_pipeline_testing(client, legislative_text, logger)
        
        # Final summary
        logger.log("\n" + "📊"*20 + " FINAL SUMMARY " + "📊"*20)
        logger.log("✅ Testing completed successfully!")
        
        if pipeline_result:
            logger.log(f"📈 Pipeline success: {pipeline_result.success}")
            logger.log(f"📊 Outputs produced: {len(pipeline_result.outputs)}")
            logger.log(f"❌ Failed chunks: {len(pipeline_result.failed_chunks)}")
        
        if detailed_result:
            logger.log("\n🎯 SAMPLE RESULTS:")
            logger.log(f"Before State (first 200 chars): {detailed_result.before_state.state_text[:200]}...")
            logger.log(f"After State (first 200 chars): {detailed_result.after_state.state_text[:200]}...")
        
        # Save logs to file in the testing directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"pipeline_with_rate_limiting_{timestamp}.log"
        logger.save_to_file(log_filename)
        
        return True
        
    except Exception as e:
        logger.log(f"❌ Pipeline testing failed: {e}", "ERROR")
        
        # Still try to save logs even if there was an error
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"pipeline_error_rate_limiting_{timestamp}.log"
        logger.save_to_file(log_filename)
        
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 