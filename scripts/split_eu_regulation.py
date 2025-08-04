#!/usr/bin/env python3
"""
EU Regulation Splitter Script

This script splits the EU Regulation 1107/2009 into granular markdown files
for optimal reference retrieval. Instead of just splitting by articles,
it creates hierarchical structure: Article -> Points/Paragraphs.

Usage:
    python scripts/split_eu_regulation.py

Output:
    data/law_text/Règlement CE No 1107:2009/articles/
    ├── Article_1.md
    ├── Article_2.md  
    ├── Article_3/
    │   ├── Point_1.md
    │   ├── Point_2.md
    │   ├── ...
    │   └── Point_33.md
    ├── Article_23/
    │   ├── Paragraph_1.md
    │   ├── Paragraph_2.md
    │   └── ...
    └── ...
"""

import re
import os
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class EURegulationSplitter:
    """Splits EU Regulation into hierarchical markdown files for optimal retrieval."""
    
    def __init__(self, input_file: str, output_dir: str):
        self.input_file = Path(input_file)
        self.output_dir = Path(output_dir)
        self.current_article = None
        self.current_article_content = []
        # Updated pattern to handle markdown headers and various formats
        self.article_pattern = re.compile(r'^#+\s*Article\s+(\d+|premier)\s*$|^Article\s+(\d+|premier)\s*$', re.IGNORECASE | re.MULTILINE)
        
        # Patterns for internal structure
        self.numbered_point_pattern = re.compile(r'^(\d+)\)\s+(.+)', re.MULTILINE)
        self.numbered_paragraph_pattern = re.compile(r'^(\d+)\.\s+(.+)', re.MULTILINE)
        self.lettered_point_pattern = re.compile(r'^([a-z])\)\s+(.+)', re.MULTILINE)
        
    def read_regulation(self) -> str:
        """Read the full regulation file."""
        try:
            with open(self.input_file, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading file {self.input_file}: {e}")
            raise
    
    def extract_articles(self, content: str) -> Dict[str, str]:
        """Extract articles and their content from the regulation."""
        articles = {}
        lines = content.split('\n')
        current_article = None
        current_content = []
        
        for line in lines:
            # Check if this is an article header
            article_match = self.article_pattern.match(line.strip())
            if article_match:
                # Save previous article if exists
                if current_article and current_content:
                    articles[current_article] = '\n'.join(current_content).strip()
                
                # Start new article - handle both capture groups
                current_article = article_match.group(1) or article_match.group(2)
                if current_article and current_article.lower() == 'premier':
                    current_article = '1'
                current_content = [line]
            elif current_article:
                current_content.append(line)
        
        # Save last article
        if current_article and current_content:
            articles[current_article] = '\n'.join(current_content).strip()
        
        logger.info(f"Extracted {len(articles)} articles")
        if logger.level <= logging.INFO:
            article_nums = sorted([int(k) for k in articles.keys() if k.isdigit()])
            logger.info(f"Article numbers found: {article_nums}")
            if article_nums:
                logger.info(f"Range: Article {min(article_nums)} to Article {max(article_nums)}")
        return articles
    
    def split_article_content(self, article_content: str) -> Dict[str, str]:
        """Split article content into numbered points/paragraphs."""
        parts = {}
        lines = article_content.split('\n')
        
        # Skip the article title line
        content_lines = lines[1:] if lines else []
        content = '\n'.join(content_lines).strip()
        
        # Try numbered points first (like Article 3 definitions)
        numbered_points = self.numbered_point_pattern.findall(content)
        if numbered_points and len(numbered_points) >= 3:  # At least 3 points to consider it structured
            current_point = None
            current_lines = []
            
            for line in content_lines:
                point_match = self.numbered_point_pattern.match(line.strip())
                if point_match:
                    # Save previous point
                    if current_point and current_lines:
                        parts[f"Point_{current_point}"] = '\n'.join(current_lines).strip()
                    
                    # Start new point
                    current_point = point_match.group(1)
                    current_lines = [line]
                elif current_point:
                    current_lines.append(line)
            
            # Save last point
            if current_point and current_lines:
                parts[f"Point_{current_point}"] = '\n'.join(current_lines).strip()
                
            logger.debug(f"Split into {len(parts)} numbered points")
            return parts
        
        # Try numbered paragraphs (like Article 23)
        numbered_paragraphs = self.numbered_paragraph_pattern.findall(content)
        if numbered_paragraphs and len(numbered_paragraphs) >= 2:  # At least 2 paragraphs
            current_paragraph = None
            current_lines = []
            
            for line in content_lines:
                para_match = self.numbered_paragraph_pattern.match(line.strip())
                if para_match:
                    # Save previous paragraph
                    if current_paragraph and current_lines:
                        parts[f"Paragraph_{current_paragraph}"] = '\n'.join(current_lines).strip()
                    
                    # Start new paragraph
                    current_paragraph = para_match.group(1)
                    current_lines = [line]
                elif current_paragraph:
                    current_lines.append(line)
            
            # Save last paragraph
            if current_paragraph and current_lines:
                parts[f"Paragraph_{current_paragraph}"] = '\n'.join(current_lines).strip()
                
            logger.debug(f"Split into {len(parts)} numbered paragraphs")
            return parts
        
        # If no clear structure, return as single unit
        logger.debug("No clear internal structure found, keeping as single unit")
        return {"main": content}
    
    def create_article_files(self, articles: Dict[str, str]) -> None:
        """Create markdown files for articles and their sub-parts."""
        # Create output directory
        articles_dir = self.output_dir / "articles"
        articles_dir.mkdir(parents=True, exist_ok=True)
        
        for article_num, article_content in articles.items():
            logger.info(f"Processing Article {article_num}")
            
            # Split article into sub-parts
            parts = self.split_article_content(article_content)
            
            if len(parts) == 1 and "main" in parts:
                # Single unit - create one file
                article_file = articles_dir / f"Article_{article_num}.md"
                self._write_file(article_file, article_content, f"Article {article_num}")
            else:
                # Multiple parts - create directory with sub-files
                article_dir = articles_dir / f"Article_{article_num}"
                article_dir.mkdir(exist_ok=True)
                
                # Create overview file
                overview_content = self._create_article_overview(article_num, article_content, parts)
                overview_file = article_dir / "overview.md"
                self._write_file(overview_file, overview_content, f"Article {article_num} Overview")
                
                # Create individual part files
                for part_name, part_content in parts.items():
                    part_file = article_dir / f"{part_name}.md"
                    full_content = f"# Article {article_num} - {part_name}\n\n{part_content}"
                    self._write_file(part_file, full_content, f"Article {article_num} {part_name}")
                
                logger.info(f"Created Article {article_num} directory with {len(parts)} parts")
    
    def _create_article_overview(self, article_num: str, full_content: str, parts: Dict[str, str]) -> str:
        """Create an overview file for articles with multiple parts."""
        lines = full_content.split('\n')
        title_line = lines[0] if lines else f"Article {article_num}"
        
        overview = f"{title_line}\n\n"
        overview += "## Structure\n\n"
        overview += f"This article contains {len(parts)} parts:\n\n"
        
        for part_name in sorted(parts.keys(), key=lambda x: self._extract_number(x)):
            overview += f"- [{part_name}](./{part_name}.md)\n"
        
        overview += "\n## Full Content\n\n"
        overview += full_content
        
        return overview
    
    def _extract_number(self, part_name: str) -> int:
        """Extract number from part name for sorting."""
        match = re.search(r'(\d+)', part_name)
        return int(match.group(1)) if match else 0
    
    def _write_file(self, file_path: Path, content: str, description: str) -> None:
        """Write content to file with error handling."""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.debug(f"Created {description}: {file_path}")
        except Exception as e:
            logger.error(f"Error writing {description} to {file_path}: {e}")
            raise
    
    def create_index(self, articles: Dict[str, str]) -> None:
        """Create an index file listing all articles and their structure."""
        index_file = self.output_dir / "articles" / "index.md"
        
        index_content = "# EU Regulation 1107/2009 - Article Index\n\n"
        index_content += "This directory contains the EU Regulation split into granular articles and sub-parts.\n\n"
        index_content += "## Articles\n\n"
        
        for article_num in sorted(articles.keys(), key=lambda x: int(x) if x.isdigit() else 999):
            article_content = articles[article_num]
            parts = self.split_article_content(article_content)
            
            if len(parts) == 1 and "main" in parts:
                index_content += f"- [Article {article_num}](./Article_{article_num}.md)\n"
            else:
                index_content += f"- [Article {article_num}](./Article_{article_num}/overview.md) ({len(parts)} parts)\n"
                for part_name in sorted(parts.keys(), key=self._extract_number):
                    index_content += f"  - [Article {article_num} - {part_name}](./Article_{article_num}/{part_name}.md)\n"
        
        index_content += "\n## Usage for Reference Resolution\n\n"
        index_content += "When resolving references like 'du 11 de l'article 3', look for:\n"
        index_content += "- `Article_3/Point_11.md` for numbered definitions\n"
        index_content += "- `Article_23/Paragraph_1.md` for numbered paragraphs\n"
        index_content += "- `Article_X.md` for articles without internal structure\n"
        
        self._write_file(index_file, index_content, "Article Index")
    
    def run(self) -> None:
        """Main execution method."""
        logger.info(f"Starting EU Regulation splitting from {self.input_file}")
        logger.info(f"Output directory: {self.output_dir}")
        
        # Read and parse the regulation
        content = self.read_regulation()
        articles = self.extract_articles(content)
        
        if not articles:
            logger.error("No articles found in the regulation")
            return
        
        # Create output structure
        self.create_article_files(articles)
        self.create_index(articles)
        
        logger.info(f"Successfully split regulation into {len(articles)} articles")
        logger.info(f"Output available in: {self.output_dir / 'articles'}")


def main():
    """Main function to run the splitter."""
    # File paths
    input_file = "data/law_text/Règlement CE No 1107:2009/Règlement CE No 1107:2009 du parlement européen.md"
    output_dir = "data/law_text/Règlement CE No 1107:2009"
    
    # Validate input file
    if not Path(input_file).exists():
        logger.error(f"Input file not found: {input_file}")
        return
    
    # Run the splitter
    splitter = EURegulationSplitter(input_file, output_dir)
    splitter.run()


if __name__ == "__main__":
    main() 