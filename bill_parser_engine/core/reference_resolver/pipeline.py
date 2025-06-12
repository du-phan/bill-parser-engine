"""
Master pipeline for processing legislative bills through the reference resolver.

This module provides a comprehensive pipeline that orchestrates the processing
of legislative bills through multiple stages, managing data flow and providing
detailed analysis and reporting.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bill_parser_engine.core.reference_resolver.bill_splitter import BillSplitter
from bill_parser_engine.core.reference_resolver.target_identifier import TargetArticleIdentifier
from bill_parser_engine.core.reference_resolver.original_text_retriever import OriginalTextRetriever
from bill_parser_engine.core.reference_resolver.text_reconstructor import TextReconstructor

from bill_parser_engine.core.reference_resolver.models import (
    BillChunk, 
    TargetArticle, 
    TargetOperationType,
    ReconstructorOutput
)

logger = logging.getLogger(__name__)


class BillProcessingPipeline:
    """
    Master pipeline for processing legislative bills through the reference resolver.
    
    This class orchestrates the complete processing workflow from raw legislative text
    to text reconstruction results, managing data flow between components and providing
    comprehensive analysis and reporting.
    
    Current pipeline steps:
    1. BillSplitter - breaks the bill into atomic chunks
    2. TargetArticleIdentifier - identifies target articles for each chunk
    3. OriginalTextRetriever - fetches current legal text for unique target articles
    4. TextReconstructor - mechanically applies amendment instructions and produces before/after fragments
    """

    def __init__(self, use_cache: bool = True):
        """
        Initialize the pipeline with all required components.
        
        Note: Caching is handled at the component level (e.g., OriginalTextRetriever)
        where expensive operations like API calls occur.
        """
        
        # Initialize all pipeline components
        self.bill_splitter = BillSplitter()
        self.target_identifier = TargetArticleIdentifier(use_cache=use_cache)
        self.original_text_retriever = OriginalTextRetriever(use_cache=use_cache)
        self.text_reconstructor = TextReconstructor()
        
        # Pipeline state and results
        self.legislative_text: Optional[str] = None
        self.chunks: List[BillChunk] = []
        self.target_results: List[Dict] = []
        self.retrieval_results: List[Dict] = []
        self.reconstruction_results: List[Dict] = []
        
        # Analysis results
        self.target_analysis: Dict = {}
        self.retrieval_analysis: Dict = {}
        self.reconstruction_analysis: Dict = {}
        
        logger.info("BillProcessingPipeline initialized (component-level caching enabled)")

    def load_legislative_text(self, text: str) -> None:
        """
        Load legislative text into the pipeline.

        Args:
            text: The raw legislative text to process
        """
        self.legislative_text = text
        logger.info("Loaded legislative text: %d characters", len(text))

    def load_legislative_text_from_file(self, file_path: Path) -> None:
        """
        Load legislative text from a file.

        Args:
            file_path: Path to the legislative text file

        Raises:
            FileNotFoundError: If the file doesn't exist
            Exception: If there's an error reading the file
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
            self.load_legislative_text(text)
            logger.info("Loaded legislative text from file: %s", file_path)
        except FileNotFoundError:
            logger.error("Legislative bill file not found at %s", file_path)
            raise
        except Exception as e:
            logger.error("Error reading legislative bill file: %s", e)
            raise

    def step_1_split_bill(self) -> List[BillChunk]:
        """
        Step 1: Split the legislative bill into atomic chunks.

        Returns:
            List of BillChunk objects

        Raises:
            ValueError: If no legislative text has been loaded
        """
        if not self.legislative_text:
            raise ValueError("No legislative text loaded. Call load_legislative_text() first.")

        logger.info("Step 1: Splitting legislative bill into chunks...")
        self.chunks = self.bill_splitter.split(self.legislative_text)
        logger.info("Split into %d chunks", len(self.chunks))
        
        return self.chunks

    def step_2_identify_target_articles(self) -> List[Dict]:
        """
        Step 2: Process chunks through TargetArticleIdentifier.

        Returns:
            List of target identification results

        Raises:
            ValueError: If chunks haven't been created yet
        """
        if not self.chunks:
            raise ValueError("No chunks available. Call step_1_split_bill() first.")

        logger.info("Step 2: Identifying target articles for %d chunks...", len(self.chunks))
        
        results = []
        for i, chunk in enumerate(self.chunks, 1):
            logger.debug("Processing chunk %d/%d: %s", i, len(self.chunks), chunk.chunk_id[:50])
            
            try:
                target_article = self.target_identifier.identify(chunk)
                
                result_entry = {
                    "chunk_id": chunk.chunk_id,
                    "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                    "hierarchy_path": chunk.hierarchy_path,
                    "target_article": {
                        "operation_type": target_article.operation_type.value if target_article.operation_type else None,
                        "code": target_article.code,
                        "article": target_article.article,
                        "full_citation": target_article.full_citation,
                        "confidence": target_article.confidence,
                        "raw_text": target_article.raw_text
                    }
                }
                
                results.append(result_entry)
                
                if target_article.article:
                    logger.debug("Identified: %s (%s)", target_article.article, target_article.operation_type.value)
                else:
                    logger.debug("No specific article identified (%s)", target_article.operation_type.value)
                    
            except Exception as e:
                logger.error("Error processing chunk %s: %s", chunk.chunk_id, e)
                result_entry = {
                    "chunk_id": chunk.chunk_id,
                    "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                    "hierarchy_path": chunk.hierarchy_path,
                    "target_article": None,
                    "error": str(e)
                }
                results.append(result_entry)

        self.target_results = results
        self.target_analysis = self._analyze_target_results(results)
        logger.info("Target identification complete: %d unique articles identified", 
                   self.target_analysis['total_unique_articles'])
        
        return results

    def step_3_retrieve_original_texts(self) -> List[Dict]:
        """
        Step 3: Retrieve original text for unique target articles.

        Returns:
            List of retrieval results

        Raises:
            ValueError: If target identification hasn't been done yet
        """
        if not self.target_results:
            raise ValueError("No target results available. Call step_2_identify_target_articles() first.")

        logger.info("Step 3: Retrieving original text for target articles...")
        
        unique_articles_data = self.target_analysis["unique_articles_data"]
        retrieval_results = []
        
        for i, (article_key, article_info) in enumerate(unique_articles_data.items(), 1):
            code = article_info["code"]
            article = article_info["article"]
            operation_type = article_info["operation_type"]
            
            logger.debug("Retrieving %d/%d: %s (%s)", i, len(unique_articles_data), article_key, operation_type)
            
            try:
                # Handle INSERT operations - skip retrieval as articles don't exist yet
                if operation_type == "INSERT":
                    result_entry = {
                        "article_key": article_key,
                        "code": code,
                        "article": article,
                        "operation_type": operation_type,
                        "original_text": "",
                        "text_length": 0,
                        "retrieval_metadata": {"source": "insert_operation", "success": True, "note": "Empty text for INSERT operation"},
                        "retrieved_at": datetime.now().isoformat()
                    }
                    retrieval_results.append(result_entry)
                    logger.debug("Handled INSERT operation")
                    continue
                
                # Filter out exotic formats (titles, books, etc.)
                if self._is_exotic_format(article):
                    result_entry = {
                        "article_key": article_key,
                        "code": code,
                        "article": article,
                        "operation_type": operation_type,
                        "original_text": "",
                        "text_length": 0,
                        "retrieval_metadata": {"source": "exotic_format", "success": False, "note": "Exotic format skipped"},
                        "retrieved_at": datetime.now().isoformat()
                    }
                    retrieval_results.append(result_entry)
                    logger.debug("Skipped exotic format")
                    continue
                
                if not code or not article:
                    logger.warning("Skipping malformed article: code=%s, article=%s", code, article)
                    continue
                
                # Fetch the original text
                original_text, metadata = self.original_text_retriever.fetch_article_text(code, article)
                
                # Hierarchical fallback is now handled inside OriginalTextRetriever
                
                result_entry = {
                    "article_key": article_key,
                    "code": code,
                    "article": article,
                    "operation_type": operation_type,
                    "original_text": original_text,
                    "text_length": len(original_text),
                    "retrieval_metadata": metadata,
                    "retrieved_at": datetime.now().isoformat()
                }
                
                retrieval_results.append(result_entry)
                
                if metadata.get("success", False):
                    logger.debug("Retrieved from %s (%d chars)", metadata.get("source", "unknown"), len(original_text))
                else:
                    logger.warning("Failed to retrieve %s: %s", article_key, metadata.get("error", "Unknown error"))
                    
            except Exception as e:
                logger.error("Error retrieving %s: %s", article_key, e)
                result_entry = {
                    "article_key": article_key,
                    "code": code,
                    "article": article,
                    "operation_type": operation_type,
                    "original_text": "",
                    "text_length": 0,
                    "retrieval_metadata": {"source": "none", "success": False, "error": str(e)},
                    "retrieved_at": datetime.now().isoformat(),
                    "error": str(e)
                }
                retrieval_results.append(result_entry)

        self.retrieval_results = retrieval_results
        self.retrieval_analysis = self._analyze_retrieval_results(retrieval_results)
        logger.info("Original text retrieval complete: %d/%d successful retrievals", 
                   self.retrieval_analysis['successful_retrievals'],
                   self.retrieval_analysis['total_articles'])
        
        return retrieval_results

    def step_4_reconstruct_texts(self) -> List[Dict]:
        """
        Step 4: Apply text reconstruction to produce before/after fragments.

        Returns:
            List of reconstruction results

        Raises:
            ValueError: If previous steps haven't been completed
        """
        if not self.target_results or not self.retrieval_results:
            raise ValueError("Target identification and original text retrieval must be completed first.")

        logger.info("Step 4: Applying text reconstruction...")
        
        # Enrich chunks with target articles
        enriched_chunks = self._enrich_chunks_with_target_articles()
        
        # Create lookup for original texts
        original_texts_lookup = self._create_original_texts_lookup()
        
        reconstruction_results = []
        
        for i, chunk in enumerate(enriched_chunks, 1):
            logger.debug("Processing chunk %d/%d: %s", i, len(enriched_chunks), chunk.chunk_id[:50])
            
            try:
                # Skip chunks without target articles or with errors
                if not chunk.target_article:
                    result_entry = {
                        "chunk_id": chunk.chunk_id,
                        "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                        "hierarchy_path": chunk.hierarchy_path,
                        "target_article": None,
                        "reconstruction_result": None,
                        "error": "No target article identified"
                    }
                    reconstruction_results.append(result_entry)
                    continue
                
                # Get the original text for this chunk's target article
                article_key = self._build_article_key(chunk.target_article.code, chunk.target_article.article)
                original_text = original_texts_lookup.get(article_key, "")
                
                if not original_text and chunk.target_article.operation_type.value != "INSERT":
                    result_entry = {
                        "chunk_id": chunk.chunk_id,
                        "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                        "hierarchy_path": chunk.hierarchy_path,
                        "target_article": {
                            "operation_type": chunk.target_article.operation_type.value,
                            "code": chunk.target_article.code,
                            "article": chunk.target_article.article,
                            "confidence": chunk.target_article.confidence,
                            "raw_text": chunk.target_article.raw_text
                        },
                        "reconstruction_result": None,
                        "error": f"No original text found for {article_key}"
                    }
                    reconstruction_results.append(result_entry)
                    continue
                
                # Apply text reconstruction
                reconstruction_output = self.text_reconstructor.reconstruct(original_text, chunk)
                
                # Create result entry
                result_entry = {
                    "chunk_id": chunk.chunk_id,
                    "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                    "hierarchy_path": chunk.hierarchy_path,
                    "target_article": {
                        "operation_type": chunk.target_article.operation_type.value,
                        "code": chunk.target_article.code,
                        "article": chunk.target_article.article,
                        "confidence": chunk.target_article.confidence,
                        "raw_text": chunk.target_article.raw_text
                    },
                    "reconstruction_result": {
                        "deleted_or_replaced_text": reconstruction_output.deleted_or_replaced_text,
                        "intermediate_after_state_text": reconstruction_output.intermediate_after_state_text,
                        "deleted_text_length": len(reconstruction_output.deleted_or_replaced_text),
                        "after_state_length": len(reconstruction_output.intermediate_after_state_text)
                    },
                    "original_text_used": {
                        "article_key": article_key,
                        "text_length": len(original_text),
                        "text_preview": original_text[:100] + "..." if len(original_text) > 100 else original_text
                    },
                    "reconstructed_at": datetime.now().isoformat()
                }
                
                reconstruction_results.append(result_entry)
                
                # Progress indicator
                deleted_len = len(reconstruction_output.deleted_or_replaced_text)
                after_len = len(reconstruction_output.intermediate_after_state_text)
                logger.debug("Reconstructed: %d chars deleted/replaced → %d chars after state", deleted_len, after_len)
                    
            except Exception as e:
                logger.error("Error processing chunk %s: %s", chunk.chunk_id, e)
                result_entry = {
                    "chunk_id": chunk.chunk_id,
                    "chunk_text_preview": chunk.text[:100] + "..." if len(chunk.text) > 100 else chunk.text,
                    "hierarchy_path": chunk.hierarchy_path,
                    "target_article": {
                        "operation_type": chunk.target_article.operation_type.value if chunk.target_article else None,
                        "code": chunk.target_article.code if chunk.target_article else None,
                        "article": chunk.target_article.article if chunk.target_article else None,
                        "confidence": chunk.target_article.confidence if chunk.target_article else None,
                        "raw_text": chunk.target_article.raw_text if chunk.target_article else None
                    },
                    "reconstruction_result": None,
                    "error": str(e)
                }
                reconstruction_results.append(result_entry)

        self.reconstruction_results = reconstruction_results
        self.reconstruction_analysis = self._analyze_reconstruction_results(reconstruction_results)
        logger.info("Text reconstruction complete: %d/%d successful reconstructions", 
                   self.reconstruction_analysis['successful_reconstructions'],
                   self.reconstruction_analysis['total_chunks'])
        
        return reconstruction_results

    def run_full_pipeline(self) -> Dict:
        """
        Run the complete pipeline from start to finish.

        Returns:
            Dictionary containing all results and analyses

        Raises:
            ValueError: If no legislative text has been loaded
        """
        logger.info("Starting full pipeline execution...")
        
        # Execute all pipeline steps
        chunks = self.step_1_split_bill()
        target_results = self.step_2_identify_target_articles()
        retrieval_results = self.step_3_retrieve_original_texts()
        reconstruction_results = self.step_4_reconstruct_texts()
        
        # Compile comprehensive results
        pipeline_results = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_chunks": len(chunks),
                "pipeline_version": "1.0",
                "pipeline_steps": ["BillSplitter", "TargetArticleIdentifier", "OriginalTextRetriever", "TextReconstructor"]
            },
            "chunks": [self._chunk_to_dict(chunk) for chunk in chunks],
            "target_analysis": self.target_analysis,
            "target_identification_results": target_results,
            "retrieval_analysis": self.retrieval_analysis,
            "original_text_results": retrieval_results,
            "reconstruction_analysis": self.reconstruction_analysis,
            "text_reconstruction_results": reconstruction_results
        }
        
        logger.info("Full pipeline execution complete")
        return pipeline_results

    def save_results(self, output_dir: Path, filename_prefix: str = "pipeline_results") -> Path:
        """
        Save all pipeline results to JSON files.

        Args:
            output_dir: Directory to save results
            filename_prefix: Prefix for output files

        Returns:
            Path to the main results file
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save comprehensive results
        results_file = output_dir / f"{filename_prefix}_{timestamp}.json"
        pipeline_results = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_chunks": len(self.chunks),
                "pipeline_version": "1.0",
                "pipeline_steps": ["BillSplitter", "TargetArticleIdentifier", "OriginalTextRetriever", "TextReconstructor"]
            },
            "target_analysis": self.target_analysis,
            "target_identification_results": self.target_results,
            "retrieval_analysis": self.retrieval_analysis,
            "original_text_results": self.retrieval_results,
            "reconstruction_analysis": self.reconstruction_analysis,
            "text_reconstruction_results": self.reconstruction_results
        }
        
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(pipeline_results, f, indent=2, ensure_ascii=False)
        
        # Save reconstruction results for next pipeline step (ReferenceLocator)
        reconstruction_output_file = output_dir / f"text_reconstruction_output_{timestamp}.json"
        reconstruction_output = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_chunks_processed": len(self.reconstruction_results),
                "successful_reconstructions": self.reconstruction_analysis.get("successful_reconstructions", 0),
                "next_pipeline_step": "ReferenceLocator"
            },
            "reconstruction_results": self.reconstruction_results,
            "analysis": self.reconstruction_analysis
        }
        
        with open(reconstruction_output_file, 'w', encoding='utf-8') as f:
            json.dump(reconstruction_output, f, indent=2, ensure_ascii=False)
        
        logger.info("Results saved to %s", results_file)
        return results_file

    def get_summary(self) -> Dict:
        """
        Get a summary of pipeline execution results.

        Returns:
            Dictionary containing summary statistics
        """
        return {
            "total_chunks": len(self.chunks),
            "target_identification": {
                "unique_articles": self.target_analysis.get("total_unique_articles", 0),
                "chunks_with_articles": self.target_analysis.get("chunks_with_identified_articles", 0),
                "operation_distribution": self.target_analysis.get("operation_type_stats", {})
            },
            "original_text_retrieval": {
                "success_rate": self.retrieval_analysis.get("success_rate", 0),
                "successful_retrievals": self.retrieval_analysis.get("successful_retrievals", 0),
                "total_articles": self.retrieval_analysis.get("total_articles", 0)
            },
            "text_reconstruction": {
                "success_rate": self.reconstruction_analysis.get("success_rate", 0),
                "successful_reconstructions": self.reconstruction_analysis.get("successful_reconstructions", 0),
                "total_chunks": self.reconstruction_analysis.get("total_chunks", 0)
            }
        }

    # Cache management methods

    def clear_component_cache(self, component_name: Optional[str] = None) -> None:
        """
        Clear cache for a specific component or all components.
        
        Args:
            component_name: Name of component to clear ('target_identifier', 
                          'original_text_retriever', 'text_reconstructor'), 
                          or None to clear all
        """
        if component_name:
            if component_name == "target_identifier" and hasattr(self.target_identifier, 'clear_cache'):
                self.target_identifier.clear_cache()
            elif component_name == "original_text_retriever" and hasattr(self.original_text_retriever, 'clear_cache'):
                self.original_text_retriever.clear_cache()
            elif component_name == "text_reconstructor" and hasattr(self.text_reconstructor, 'clear_cache'):
                self.text_reconstructor.clear_cache()
            else:
                logger.warning("Component '%s' not found or doesn't support caching", component_name)
                return
        else:
            # Clear all component caches
            for component_name, component in [
                ("target_identifier", self.target_identifier),
                ("original_text_retriever", self.original_text_retriever),
                ("text_reconstructor", self.text_reconstructor)
            ]:
                if hasattr(component, 'clear_cache'):
                    component.clear_cache()
        
        logger.info("Cleared component cache for: %s", component_name or "all components")

    # Private helper methods

    def _analyze_target_results(self, results: List[Dict]) -> Dict:
        """Analyze target identification results."""
        unique_articles_data = {}
        operation_stats = {}
        code_stats = {}
        error_count = 0
        
        for result in results:
            if "error" in result:
                error_count += 1
                continue
                
            target_article = result.get("target_article")
            if not target_article or not target_article.get("article"):
                continue
                
            article = target_article["article"]
            code = target_article.get("code")
            operation_type = target_article.get("operation_type")
            
            if article:
                article_key = f"{code}::{article}" if code else article
                if article_key not in unique_articles_data:
                    unique_articles_data[article_key] = {
                        "code": code,
                        "article": article,
                        "operation_type": operation_type,
                        "article_key": article_key
                    }
                
            if operation_type:
                operation_stats[operation_type] = operation_stats.get(operation_type, 0) + 1
                
            if code:
                code_stats[code] = code_stats.get(code, 0) + 1
        
        return {
            "unique_articles_data": unique_articles_data,
            "unique_articles": sorted(list(unique_articles_data.keys())),
            "total_unique_articles": len(unique_articles_data),
            "operation_type_stats": operation_stats,
            "code_stats": code_stats,
            "total_chunks_processed": len(results),
            "chunks_with_errors": error_count,
            "chunks_with_identified_articles": len([r for r in results if r.get("target_article", {}).get("article")])
        }

    def _analyze_retrieval_results(self, retrieval_results: List[Dict]) -> Dict:
        """Analyze retrieval results."""
        successful_retrievals = [r for r in retrieval_results if r["retrieval_metadata"].get("success", False)]
        failed_retrievals = [r for r in retrieval_results if not r["retrieval_metadata"].get("success", False)]
        
        source_stats = {}
        for result in successful_retrievals:
            source = result["retrieval_metadata"].get("source", "unknown")
            source_stats[source] = source_stats.get(source, 0) + 1
        
        text_lengths = [r["text_length"] for r in successful_retrievals]
        avg_length = sum(text_lengths) / len(text_lengths) if text_lengths else 0
        max_length = max(text_lengths) if text_lengths else 0
        min_length = min(text_lengths) if text_lengths else 0
        
        return {
            "total_articles": len(retrieval_results),
            "successful_retrievals": len(successful_retrievals),
            "failed_retrievals": len(failed_retrievals),
            "success_rate": len(successful_retrievals) / len(retrieval_results) if retrieval_results else 0,
            "source_stats": source_stats,
            "text_length_stats": {
                "average": avg_length,
                "maximum": max_length,
                "minimum": min_length,
                "total_characters": sum(text_lengths)
            },
            "failed_articles": [r["article_key"] for r in failed_retrievals]
        }

    def _analyze_reconstruction_results(self, reconstruction_results: List[Dict]) -> Dict:
        """Analyze reconstruction results."""
        successful_reconstructions = [r for r in reconstruction_results if r.get("reconstruction_result")]
        failed_reconstructions = [r for r in reconstruction_results if not r.get("reconstruction_result")]
        
        operation_stats = {}
        deleted_text_lengths = []
        after_state_lengths = []
        
        for result in successful_reconstructions:
            target_article = result.get("target_article", {})
            operation_type = target_article.get("operation_type")
            if operation_type:
                operation_stats[operation_type] = operation_stats.get(operation_type, 0) + 1
                
            reconstruction = result["reconstruction_result"]
            deleted_text_lengths.append(reconstruction["deleted_text_length"])
            after_state_lengths.append(reconstruction["after_state_length"])
        
        avg_deleted_length = sum(deleted_text_lengths) / len(deleted_text_lengths) if deleted_text_lengths else 0
        avg_after_length = sum(after_state_lengths) / len(after_state_lengths) if after_state_lengths else 0
        
        return {
            "total_chunks": len(reconstruction_results),
            "successful_reconstructions": len(successful_reconstructions),
            "failed_reconstructions": len(failed_reconstructions),
            "success_rate": len(successful_reconstructions) / len(reconstruction_results) if reconstruction_results else 0,
            "operation_type_stats": operation_stats,
            "text_length_stats": {
                "average_deleted_length": avg_deleted_length,
                "average_after_state_length": avg_after_length,
                "max_deleted_length": max(deleted_text_lengths) if deleted_text_lengths else 0,
                "max_after_state_length": max(after_state_lengths) if after_state_lengths else 0,
                "total_deleted_characters": sum(deleted_text_lengths),
                "total_after_state_characters": sum(after_state_lengths)
            },
            "failed_chunks": [r["chunk_id"] for r in failed_reconstructions]
        }

    def _enrich_chunks_with_target_articles(self) -> List[BillChunk]:
        """Enrich BillChunk objects with their identified target articles."""
        target_lookup = {result["chunk_id"]: result for result in self.target_results}
        
        enriched_chunks = []
        for chunk in self.chunks:
            enriched_chunk = BillChunk(
                text=chunk.text,
                titre_text=chunk.titre_text,
                article_label=chunk.article_label,
                article_introductory_phrase=chunk.article_introductory_phrase,
                major_subdivision_label=chunk.major_subdivision_label,
                major_subdivision_introductory_phrase=chunk.major_subdivision_introductory_phrase,
                numbered_point_label=chunk.numbered_point_label,
                hierarchy_path=chunk.hierarchy_path,
                chunk_id=chunk.chunk_id,
                start_pos=chunk.start_pos,
                end_pos=chunk.end_pos,
                cross_references=chunk.cross_references
            )
            
            target_data = target_lookup.get(chunk.chunk_id)
            if target_data and target_data.get("target_article") and not target_data.get("error"):
                target_article_data = target_data["target_article"]
                enriched_chunk.target_article = TargetArticle(
                    operation_type=TargetOperationType[target_article_data["operation_type"]],
                    code=target_article_data["code"],
                    article=target_article_data["article"],
                    confidence=target_article_data["confidence"],
                    raw_text=target_article_data["raw_text"],
                    full_citation=target_article_data.get("full_citation")
                )
            
            enriched_chunks.append(enriched_chunk)
        
        return enriched_chunks

    def _create_original_texts_lookup(self) -> Dict[str, str]:
        """Create a lookup dictionary from article keys to original texts."""
        lookup = {}
        for result in self.retrieval_results:
            article_key = result["article_key"]
            original_text = result["original_text"]
            lookup[article_key] = original_text
        return lookup

    def _build_article_key(self, code: str, article: str) -> str:
        """Build article key for lookup."""
        return f"{code}::{article}" if code and article else article or ""

    def _chunk_to_dict(self, chunk: BillChunk) -> Dict:
        """Convert BillChunk to dictionary for serialization."""
        return {
            "chunk_id": chunk.chunk_id,
            "text": chunk.text,
            "titre_text": chunk.titre_text,
            "article_label": chunk.article_label,
            "article_introductory_phrase": chunk.article_introductory_phrase,
            "major_subdivision_label": chunk.major_subdivision_label,
            "major_subdivision_introductory_phrase": chunk.major_subdivision_introductory_phrase,
            "numbered_point_label": chunk.numbered_point_label,
            "hierarchy_path": chunk.hierarchy_path,
            "start_pos": chunk.start_pos,
            "end_pos": chunk.end_pos,
            "cross_references": chunk.cross_references
        }

    def _dict_to_chunk(self, chunk_dict: Dict) -> BillChunk:
        """Convert dictionary to BillChunk object for deserialization."""
        return BillChunk(
            chunk_id=chunk_dict["chunk_id"],
            text=chunk_dict["text"],
            titre_text=chunk_dict["titre_text"],
            article_label=chunk_dict["article_label"],
            article_introductory_phrase=chunk_dict["article_introductory_phrase"],
            major_subdivision_label=chunk_dict["major_subdivision_label"],
            major_subdivision_introductory_phrase=chunk_dict["major_subdivision_introductory_phrase"],
            numbered_point_label=chunk_dict["numbered_point_label"],
            hierarchy_path=chunk_dict["hierarchy_path"],
            start_pos=chunk_dict["start_pos"],
            end_pos=chunk_dict["end_pos"],
            cross_references=chunk_dict.get("cross_references", [])
        )

    # Utility methods from original script
    def _is_exotic_format(self, article: str) -> bool:
        """Check if the article format is too exotic to process."""
        exotic_patterns = ["Titre", "titre", "Livre", "livre", "Chapitre", "chapitre", "Section", "section"]
        return any(pattern in article for pattern in exotic_patterns)

 