#!/usr/bin/env python3
"""
Demonstration of Optimal Reference Resolution

This script demonstrates how the hierarchical splitting approach
enables precise reference resolution compared to article-based splitting.

Example: "du 11 de l'article 3 du règlement (CE) n° 1107/2009"
"""

from pathlib import Path


def demonstrate_reference_resolution():
    """Demonstrate the difference between approaches."""
    
    print("="*70)
    print("REFERENCE RESOLUTION DEMONSTRATION")
    print("="*70)
    print()
    
    # Example reference from the legislative bill
    reference = "du 11 de l'article 3 du règlement (CE) n° 1107/2009"
    print(f"📋 Reference to resolve: '{reference}'")
    print("   Translation: 'point 11 of article 3 of regulation (CE) no 1107/2009'")
    print()
    
    # Show the difference between approaches
    print("🔍 COMPARISON OF APPROACHES:")
    print()
    
    print("❌ OLD APPROACH (Article-based splitting):")
    print("   → Retrieve: Article_3.md (entire article)")
    print("   → Content: All 33 definitions (several KB)")
    print("   → Precision: Low (32 irrelevant definitions included)")
    print("   → User needs to manually find point 11 among 33 items")
    print()
    
    print("✅ NEW APPROACH (Hierarchical splitting):")
    print("   → Retrieve: Article_3/Point_11.md (specific point)")
    print("   → Content: Only the 'producteur' definition")
    print("   → Precision: High (exactly what's needed)")
    print("   → Direct access to the target content")
    print()
    
    # Show the actual content
    articles_dir = Path("data/law_text/Règlement CE No 1107:2009/articles")
    point_11_file = articles_dir / "Article_3" / "Point_11.md"
    
    if point_11_file.exists():
        print("📄 RETRIEVED CONTENT:")
        print("-" * 50)
        with open(point_11_file, 'r', encoding='utf-8') as f:
            content = f.read()
        print(content)
        print("-" * 50)
    else:
        print("⚠️  Point_11.md not found. Run split_eu_regulation.py first.")
    
    print()
    print("💡 BENEFITS:")
    print("   • Precise retrieval (only relevant content)")
    print("   • Faster processing (smaller files)")
    print("   • Better context isolation")
    print("   • Scalable for complex legal texts")
    print("   • Direct mapping from references to files")
    print()
    
    print("🎯 USAGE IN PIPELINE:")
    print("   Reference: 'du 11 de l'article 3' → File: 'Article_3/Point_11.md'")
    print("   Reference: 'du paragraphe 1 de l'article 23' → File: 'Article_23/Paragraph_1.md'")
    print("   Reference: 'de l'article 47' → File: 'Article_47.md' (if no sub-structure)")


if __name__ == "__main__":
    demonstrate_reference_resolution() 